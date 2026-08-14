"""0AMV-gated single-index backtest: close confirm, next open fill."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from ai_invest_advisor.amv_index_data import ENTRY_TWO_DAY_SUM, EXIT_THRESHOLD


class AmvBacktestError(ValueError):
    pass


def simulate_amv_gate(
    frame: pd.DataFrame,
    *,
    cost: float = 0.001,
    min_hold_days: int = 0,
    peak_dd_exit: float | None = None,
    atr_trail_mult: float | None = None,
    force_eod_exit: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate full-position strategy on one aligned index+0AMV frame.

    State machine:
    - flat
    - pending_buy  (entry signal at prior close)
    - long
    - pending_sell (exit signal at prior close)

    Priority: while long, only exits; while flat, only entries.
    Fills at next session open with one-way cost on each side.

    Optional path-dependent exits (evaluated on close, same as gate):
    - peak_dd_exit: exit if close falls ``peak_dd_exit`` from trade peak close
    - atr_trail_mult: exit if close <= peak_close - mult * atr14
    """
    if not 0.0 <= cost < 1.0:
        raise AmvBacktestError("cost must be in [0, 1)")
    if min_hold_days < 0:
        raise AmvBacktestError("min_hold_days cannot be negative")
    if peak_dd_exit is not None and not (0.0 < peak_dd_exit < 1.0):
        raise AmvBacktestError("peak_dd_exit must be in (0, 1)")
    if atr_trail_mult is not None and atr_trail_mult <= 0:
        raise AmvBacktestError("atr_trail_mult must be positive")
    required = {"date", "open", "close", "entry_signal", "exit_signal"}
    missing = required.difference(frame.columns)
    if missing:
        raise AmvBacktestError(f"frame missing columns: {sorted(missing)}")
    if atr_trail_mult is not None and "atr14" not in frame.columns:
        raise AmvBacktestError("atr_trail_mult requires atr14 column")

    data = frame.reset_index(drop=True).copy()
    if "min_hold_days" in data.columns and min_hold_days == 0:
        # Allow per-frame override from strategy variants.
        raw = data["min_hold_days"].iloc[0]
        if pd.notna(raw):
            min_hold_days = int(raw)

    trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []

    state = "flat"
    realized_equity = 1.0
    entry: dict[str, Any] | None = None
    peak_close: float | None = None
    pending_exit_reason = "gate_exit"

    def mark_to_market(close: float) -> float:
        if entry is None:
            return realized_equity
        return realized_equity * close / float(entry["entry_price"]) * (1.0 - cost)

    def path_exit_hit(row: pd.Series, close_px: float) -> str | None:
        nonlocal peak_close
        assert peak_close is not None
        peak_close = max(peak_close, close_px)
        if peak_dd_exit is not None and close_px <= peak_close * (1.0 - peak_dd_exit):
            return "peak_dd"
        if atr_trail_mult is not None:
            atr = row.get("atr14")
            if atr is not None and pd.notna(atr) and close_px <= peak_close - atr_trail_mult * float(atr):
                return "atr_trail"
        return None

    for index, row in data.iterrows():
        action = "flat"
        open_px = float(row["open"])
        close_px = float(row["close"])
        exited_today = False

        if state == "pending_buy":
            entry = {
                "entry_index": index,
                "entry_date": row["date"],
                "entry_open": open_px,
                "entry_price": open_px * (1.0 + cost),
                "signal_date": data.iloc[index - 1]["date"] if index > 0 else row["date"],
            }
            peak_close = close_px
            state = "long"
            action = "entry"

        elif state == "pending_sell":
            assert entry is not None
            exit_price = open_px * (1.0 - cost)
            entry_price = float(entry["entry_price"])
            entry_open = float(entry["entry_open"])
            gross = open_px / entry_open - 1.0
            net = exit_price / entry_price - 1.0
            holding_days = index - int(entry["entry_index"]) + 1
            realized_equity *= 1.0 + net
            trades.append(
                {
                    "entry_index": entry["entry_index"],
                    "entry_date": entry["entry_date"],
                    "signal_entry_date": entry["signal_date"],
                    "entry_price": entry_price,
                    "exit_index": index,
                    "exit_date": row["date"],
                    "signal_exit_date": data.iloc[index - 1]["date"] if index > 0 else row["date"],
                    "exit_price": exit_price,
                    "exit_reason": pending_exit_reason,
                    "holding_days": holding_days,
                    "gross_return": gross,
                    "net_return": net,
                }
            )
            entry = None
            peak_close = None
            pending_exit_reason = "gate_exit"
            state = "flat"
            action = "exit"
            exited_today = True

        if state == "long" and not exited_today:
            assert entry is not None
            holding_days = index - int(entry["entry_index"]) + 1
            can_exit = min_hold_days == 0 or holding_days > min_hold_days
            path_reason = path_exit_hit(row, close_px)
            want_exit = bool(row["exit_signal"]) or path_reason is not None
            exit_reason = path_reason if path_reason is not None else "gate_exit"
            if want_exit and can_exit and index + 1 < len(data):
                state = "pending_sell"
                pending_exit_reason = exit_reason
                action = "schedule_exit"
            elif want_exit and not can_exit:
                action = "hold_min_hold"
            elif want_exit:
                action = "exit_unfilled_final_row"
            elif action != "entry":
                action = "hold"

        if state == "flat" and not exited_today and bool(row["entry_signal"]):
            if index + 1 < len(data):
                state = "pending_buy"
                action = "schedule_entry"
            else:
                action = "entry_unfilled_final_row"

        if state in {"long", "pending_sell"}:
            equity = mark_to_market(close_px)
            position = 1
        else:
            equity = realized_equity
            position = 0

        daily_rows.append(
            {
                "date": row["date"],
                "equity": equity,
                "position": position,
                "action": action,
                "open": open_px,
                "close": close_px,
                "amv_ret_1d": row.get("amv_ret_1d"),
                "amv_ret_2d_sum": row.get("amv_ret_2d_sum"),
                "entry_signal": bool(row["entry_signal"]),
                "exit_signal": bool(row["exit_signal"]),
            }
        )

    # Force mark any open position at final close (research convention).
    # Public site passes force_eod_exit=False so live NAV/position stay marked-to-market.
    if force_eod_exit and state in {"long", "pending_sell"} and entry is not None:
        last = data.iloc[-1]
        exit_price = float(last["close"]) * (1.0 - cost)
        entry_price = float(entry["entry_price"])
        entry_open = float(entry["entry_open"])
        net = exit_price / entry_price - 1.0
        gross = float(last["close"]) / entry_open - 1.0
        realized_equity *= 1.0 + net
        trades.append(
            {
                "entry_index": entry["entry_index"],
                "entry_date": entry["entry_date"],
                "signal_entry_date": entry["signal_date"],
                "entry_price": entry_price,
                "exit_index": len(data) - 1,
                "exit_date": last["date"],
                "signal_exit_date": last["date"],
                "exit_price": exit_price,
                "exit_reason": "end_of_sample",
                "holding_days": len(data) - 1 - int(entry["entry_index"]) + 1,
                "gross_return": gross,
                "net_return": net,
            }
        )
        daily_rows[-1]["equity"] = realized_equity
        daily_rows[-1]["position"] = 0
        daily_rows[-1]["action"] = "exit_eod_force"

    daily = pd.DataFrame(daily_rows)
    trade_frame = pd.DataFrame(trades)
    return daily, trade_frame


def buy_and_hold_equity(frame: pd.DataFrame, *, cost: float = 0.001) -> pd.DataFrame:
    """Buy at first open, hold to last close, one round-trip cost."""
    if frame.empty:
        return pd.DataFrame(columns=["date", "equity", "position"])
    data = frame.reset_index(drop=True)
    entry = float(data.iloc[0]["open"]) * (1.0 + cost)
    equities = []
    for i, row in data.iterrows():
        if i == len(data) - 1:
            mark = float(row["close"]) * (1.0 - cost)
        else:
            mark = float(row["close"])
        equities.append(
            {
                "date": row["date"],
                "equity": mark / entry,
                "position": 0 if i == len(data) - 1 else 1,
            }
        )
    return pd.DataFrame(equities)


def summarize_backtest(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    cost: float,
    code: str,
    name: str,
    tencent_symbol: str,
    benchmark_daily: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if daily.empty:
        total_return = annualized_return = max_drawdown = sharpe = 0.0
        exposure = 0.0
    else:
        equity = pd.to_numeric(daily["equity"], errors="coerce")
        if equity.isna().any() or (equity <= 0).any():
            raise AmvBacktestError("daily equity must be positive and finite")
        total_return = float(equity.iloc[-1] - 1.0)
        drawdowns = equity / equity.cummax() - 1.0
        max_drawdown = float(min(drawdowns.min(), 0.0))
        dates = pd.to_datetime(daily["date"])
        elapsed_days = int((dates.iloc[-1] - dates.iloc[0]).days)
        if elapsed_days <= 0:
            annualized_return = total_return
        else:
            annualized_return = math.expm1(
                math.log(float(equity.iloc[-1])) * 365.25 / elapsed_days
            )
        rets = equity.pct_change().dropna()
        if len(rets) > 1 and float(rets.std()) > 0:
            sharpe = float(rets.mean() / rets.std() * math.sqrt(252))
        else:
            sharpe = 0.0
        exposure = float(pd.to_numeric(daily["position"], errors="coerce").fillna(0).mean())

    trade_returns = pd.to_numeric(trades.get("net_return", pd.Series(dtype=float)), errors="coerce")
    holding = pd.to_numeric(trades.get("holding_days", pd.Series(dtype=float)), errors="coerce")
    n_trades = int(len(trades))
    if n_trades == 0:
        win_rate = mean_trade = avg_hold = float("nan")
    else:
        win_rate = float((trade_returns > 0).mean())
        mean_trade = float(trade_returns.mean())
        avg_hold = float(holding.mean())

    bench_total = float("nan")
    if benchmark_daily is not None and not benchmark_daily.empty:
        bench_total = float(benchmark_daily["equity"].iloc[-1] - 1.0)

    start = str(pd.Timestamp(daily["date"].iloc[0]).date()) if not daily.empty else None
    end = str(pd.Timestamp(daily["date"].iloc[-1]).date()) if not daily.empty else None

    return {
        "code": code,
        "name": name,
        "tencent_symbol": tencent_symbol,
        "cost": float(cost),
        "start": start,
        "end": end,
        "bars": int(len(daily)),
        "trades": n_trades,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "mean_net_trade_return": mean_trade,
        "average_holding_days": avg_hold,
        "exposure": exposure,
        "benchmark_total_return": bench_total,
        "exit_threshold": EXIT_THRESHOLD,
        "entry_two_day_sum": ENTRY_TWO_DAY_SUM,
        "execution": "next_open",
    }


def run_index_backtest(
    frame: pd.DataFrame,
    *,
    cost: float = 0.001,
    peak_dd_exit: float | None = None,
    atr_trail_mult: float | None = None,
    force_eod_exit: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        raise AmvBacktestError("empty research frame")
    daily, trades = simulate_amv_gate(
        frame,
        cost=cost,
        peak_dd_exit=peak_dd_exit,
        atr_trail_mult=atr_trail_mult,
        force_eod_exit=force_eod_exit,
    )
    benchmark = buy_and_hold_equity(frame, cost=cost)
    summary = summarize_backtest(
        daily,
        trades,
        cost=cost,
        code=str(frame["code"].iloc[0]) if "code" in frame.columns else "",
        name=str(frame["name"].iloc[0]) if "name" in frame.columns else "",
        tencent_symbol=str(frame["tencent_symbol"].iloc[0])
        if "tencent_symbol" in frame.columns
        else "",
        benchmark_daily=benchmark,
    )
    return daily, trades, benchmark, summary
