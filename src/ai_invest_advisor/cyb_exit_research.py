"""Research-frame preparation for CYB exit-rule studies.

All indicators are calculated in chronological order on the complete index
history supplied by the caller.  The requested date range is applied only
afterwards so rolling values cannot accidentally use future observations.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

import pandas as pd

from ai_invest_advisor.cyb_market_data import chinese_sma


class ResearchFrameError(ValueError):
    """Raised when a research input cannot support a deterministic study."""


@dataclass(frozen=True)
class ExitSpec:
    """Parameters for one deterministic, close-confirmed exit rule."""

    name: str
    family: str
    max_hold: int
    stop_loss: float | None = None
    take_profit: float | None = None
    trail_pct: float | None = None
    trail_activation: float = 0.0
    atr_multiple: float | None = None
    ma_period: int | None = None
    kdj_dead_cross: bool = False
    min_hold: int = 0


_MARKET_COLUMNS = ("date", "open", "close", "high", "low")
_BREADTH_COLUMNS = ("date", "advancers", "decliners")


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ResearchFrameError(f"{name} is missing required columns: {missing}")


def _normalise_dates(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    result = frame.copy()
    try:
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.normalize()
    except (TypeError, ValueError) as exc:
        raise ResearchFrameError(f"{name} contains an invalid date") from exc
    if result["date"].isna().any():
        raise ResearchFrameError(f"{name} contains a missing date")
    if result["date"].duplicated().any():
        raise ResearchFrameError(f"{name} contains duplicate trading dates")
    return result.sort_values("date").reset_index(drop=True)


def _numeric_fields(
    frame: pd.DataFrame, columns: tuple[str, ...], name: str
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[list(columns)].isna().any().any():
        raise ResearchFrameError(f"{name} contains missing or non-numeric required values")
    return result


def _load_market(path: str | Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError) as exc:
        raise ResearchFrameError(f"could not load index CSV: {path}") from exc
    _require_columns(frame, _MARKET_COLUMNS, "index CSV")
    frame = _normalise_dates(frame, "index CSV")
    frame = _numeric_fields(frame, _MARKET_COLUMNS[1:], "index CSV")
    if (frame[list(_MARKET_COLUMNS[1:])] <= 0).any().any():
        raise ResearchFrameError("index CSV contains nonpositive OHLC prices")
    return frame


def _load_breadth(path: str | Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError) as exc:
        raise ResearchFrameError(f"could not load breadth CSV: {path}") from exc
    _require_columns(frame, _BREADTH_COLUMNS, "breadth CSV")
    frame = _normalise_dates(frame, "breadth CSV")
    frame = _numeric_fields(frame, _BREADTH_COLUMNS[1:], "breadth CSV")
    if (frame["advancers"] + frame["decliners"] <= 0).any():
        raise ResearchFrameError("breadth CSV contains a nonpositive quoted total")
    return frame


def _research_dates(start_date: object, end_date: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
    except (TypeError, ValueError) as exc:
        raise ResearchFrameError("research dates must be valid dates") from exc
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ResearchFrameError("research date range is invalid")
    return start, end


def _add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous_close = result["close"].shift(1)
    result["true_range"] = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr14"] = result["true_range"].rolling(14, min_periods=14).mean()
    result["ma250"] = result["close"].rolling(250, min_periods=250).mean()
    for period in (5, 10, 20):
        result[f"ma{period}"] = result["close"].rolling(period, min_periods=period).mean()

    lowest = result["low"].rolling(9, min_periods=9).min()
    highest = result["high"].rolling(9, min_periods=9).max()
    spread = (highest - lowest).where(lambda values: values != 0)
    rsv = 100.0 * (result["close"] - lowest) / spread
    result["k"] = chinese_sma(rsv, 3)
    result["d"] = chinese_sma(result["k"], 3)
    result["j"] = 3.0 * result["k"] - 2.0 * result["d"]
    result["kdj_dead_cross"] = (
        (result["k"] < result["d"])
        & (result["k"].shift(1) >= result["d"].shift(1))
    ).fillna(False).astype(bool)
    return result


def build_research_frame(
    index_csv: str | Path,
    breadth_csv: str | Path,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    """Return the requested CYB study range with causal indicators and entries.

    ``signal_day`` is evaluated at that session's close.  ``entry_day`` is its
    next supplied trading session, where ``entry_price`` is that session's open.
    Stable IDs retain the complete supplied-history ordering rather than the
    filtered frame's positional index.
    """

    start, end = _research_dates(start_date, end_date)
    market = _load_market(index_csv)
    breadth = _load_breadth(breadth_csv)
    frame = market.merge(
        breadth[list(_BREADTH_COLUMNS)], on="date", how="left", validate="one_to_one"
    )
    frame["emotion"] = (
        frame["advancers"] / (frame["advancers"] + frame["decliners"]) * 100.0
    )
    frame = _add_indicators(frame)
    frame["session_id"] = pd.RangeIndex(len(frame), dtype="int64")
    frame["row_id"] = frame["session_id"]

    in_range = frame["date"].between(start, end)
    if not in_range.any():
        raise ResearchFrameError("research date range contains no trading sessions")
    required = ["open", "close", "high", "low", "advancers", "decliners"]
    if frame.loc[in_range, required].isna().any().any():
        raise ResearchFrameError("research range has missing required market or breadth values")

    frame["signal_day"] = (frame["emotion"] < 15.0) & (frame["close"] >= frame["ma250"])
    frame["signal_day"] = frame["signal_day"].fillna(False).astype(bool)
    frame["entry_day"] = frame["signal_day"].shift(1, fill_value=False).astype(bool)
    frame["entry_price"] = frame["open"].where(frame["entry_day"])
    frame["entry_signal_date"] = frame["date"].shift(1).where(frame["entry_day"])
    frame["entry_signal_session_id"] = frame["session_id"].shift(1).where(frame["entry_day"])

    return frame.loc[in_range].reset_index(drop=True)


def _conditional_reason(
    row: pd.Series,
    *,
    entry_price: float,
    peak_close: float,
    spec: ExitSpec,
    holding_days: int,
) -> str | None:
    """Return the first close-known exit condition in its documented order."""

    if holding_days < spec.min_hold:
        return None
    trade_return = float(row["close"]) / entry_price - 1.0
    if spec.stop_loss is not None and trade_return <= -spec.stop_loss:
        return "stop_loss"
    if spec.take_profit is not None and trade_return >= spec.take_profit:
        return "take_profit"
    if (
        spec.trail_pct is not None
        and peak_close / entry_price - 1.0 >= spec.trail_activation
        and float(row["close"]) <= peak_close * (1.0 - spec.trail_pct)
    ):
        return "trailing_close"
    if (
        spec.atr_multiple is not None
        and float(row["close"])
        <= peak_close - spec.atr_multiple * float(row["atr14"])
    ):
        return "atr_trailing"
    if spec.ma_period is not None and float(row["close"]) < float(
        row[f"ma{spec.ma_period}"]
    ):
        return f"below_ma{spec.ma_period}"
    if spec.kdj_dead_cross:
        dead_cross = row["kdj_dead_cross"]
        if pd.notna(dead_cross) and bool(dead_cross):
            return "kdj_dead_cross"
    return None


def simulate_exit(
    frame: pd.DataFrame,
    spec: ExitSpec,
    cost: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate a single-position exit rule without using future prices.

    Entry and conditional exit signals are observed at each session close, so
    they are executed only at the following session's open.  A maximum holding
    period is the sole exception and liquidates at that session's close.
    """

    if spec.max_hold < 1:
        raise ValueError("max_hold must be at least one session")
    if spec.min_hold < 0:
        raise ValueError("min_hold cannot be negative")
    if not 0.0 <= cost < 1.0:
        raise ValueError("cost must be in [0, 1)")

    required = {"date", "open", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"frame is missing required columns: {sorted(missing)}")
    signal_column = "entry_signal" if "entry_signal" in frame else "signal_day"
    if signal_column not in frame:
        raise ValueError("frame is missing entry_signal")
    if spec.ma_period is not None and f"ma{spec.ma_period}" not in frame:
        raise ValueError(f"frame is missing ma{spec.ma_period}")
    if spec.atr_multiple is not None and "atr14" not in frame:
        raise ValueError("frame is missing atr14")
    if spec.kdj_dead_cross and "kdj_dead_cross" not in frame:
        raise ValueError("frame is missing kdj_dead_cross")

    data = frame.reset_index(drop=True).copy()
    trade_columns = [
        "entry_index",
        "entry_date",
        "entry_price",
        "exit_signal_index",
        "exit_index",
        "exit_date",
        "exit_price",
        "exit_reason",
        "holding_days",
        "gross_return",
        "net_return",
    ]
    daily_columns = ["date", "equity", "position", "action"]
    trades: list[dict[str, object]] = []
    daily: list[dict[str, object]] = []
    state = "flat"
    realized_equity = 1.0
    entry: dict[str, object] | None = None
    pending_exit: dict[str, object] | None = None

    def complete_exit(
        *,
        exit_index: int,
        exit_price: float,
        exit_reason: str,
        exit_signal_index: int,
        holding_days: int,
    ) -> None:
        nonlocal entry, realized_equity
        assert entry is not None
        entry_price = float(entry["entry_price"])
        gross_return = exit_price / entry_price - 1.0
        net_return = exit_price / entry_price * (1.0 - cost) ** 2 - 1.0
        realized_equity *= 1.0 + net_return
        trades.append(
            {
                "entry_index": entry["entry_index"],
                "entry_date": entry["entry_date"],
                "entry_price": entry_price,
                "exit_signal_index": exit_signal_index,
                "exit_index": exit_index,
                "exit_date": data.iloc[exit_index]["date"],
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "holding_days": holding_days,
                "gross_return": gross_return,
                "net_return": net_return,
            }
        )
        entry = None

    for index, row in data.iterrows():
        action = "flat"
        exited_today = False

        if state == "pending_entry":
            entry = {
                "entry_index": index,
                "entry_date": row["date"],
                "entry_price": float(row["open"]),
                "peak_close": float(row["close"]),
            }
            state = "holding"
            action = "entry"
        elif state == "pending_exit":
            assert pending_exit is not None
            complete_exit(
                exit_index=index,
                exit_price=float(row["open"]),
                exit_reason=str(pending_exit["exit_reason"]),
                exit_signal_index=int(pending_exit["exit_signal_index"]),
                holding_days=int(pending_exit["holding_days"]),
            )
            pending_exit = None
            state = "flat"
            action = "exit"
            exited_today = True

        if state == "holding":
            assert entry is not None
            entry["peak_close"] = max(float(entry["peak_close"]), float(row["close"]))
            holding_days = index - int(entry["entry_index"]) + 1
            if holding_days >= spec.max_hold:
                complete_exit(
                    exit_index=index,
                    exit_price=float(row["close"]),
                    exit_reason="max_hold",
                    exit_signal_index=index,
                    holding_days=holding_days,
                )
                state = "flat"
                action = "exit_max_hold"
                exited_today = True
            else:
                reason = _conditional_reason(
                    row,
                    entry_price=float(entry["entry_price"]),
                    peak_close=float(entry["peak_close"]),
                    spec=spec,
                    holding_days=holding_days,
                )
                if reason is not None and index + 1 < len(data):
                    pending_exit = {
                        "exit_reason": reason,
                        "exit_signal_index": index,
                        "holding_days": holding_days,
                    }
                    state = "pending_exit"
                    action = f"schedule_exit_{reason}"
                elif reason is not None:
                    action = "exit_unfilled_final_row"
                elif action != "entry":
                    action = "hold"

        if state == "flat" and not exited_today and bool(row[signal_column]):
            if index + 1 < len(data):
                state = "pending_entry"
                action = "schedule_entry"
            else:
                action = "entry_unfilled_final_row"

        if state in {"holding", "pending_exit"}:
            assert entry is not None
            equity = realized_equity * float(row["close"]) / float(entry["entry_price"]) * (
                1.0 - cost
            )
            position = 1
        else:
            equity = realized_equity
            position = 0
        daily.append(
            {
                "date": row["date"],
                "equity": equity,
                "position": position,
                "action": action,
            }
        )

    return (
        pd.DataFrame(daily, columns=daily_columns),
        pd.DataFrame(trades, columns=trade_columns),
    )


_SUMMARY_COLUMNS = [
    "spec_name",
    "family",
    "cost",
    "complexity",
    "trades",
    "total_return",
    "annualized_return",
    "max_drawdown",
    "calmar",
    "win_rate",
    "mean_net_trade_return",
    "median_net_trade_return",
    "best_net_trade_return",
    "worst_net_trade_return",
    "average_holding_days",
]
_GRID_TRADE_COLUMNS = [
    "spec_name",
    "family",
    "cost",
    "entry_index",
    "entry_date",
    "entry_price",
    "exit_signal_index",
    "exit_index",
    "exit_date",
    "exit_price",
    "exit_reason",
    "holding_days",
    "gross_return",
    "net_return",
]


def _number_label(value: float) -> str:
    """Render a parameter in an unambiguous, stable rule name."""

    return f"{value:g}".replace(".", "_")


def build_exit_specs() -> list[ExitSpec]:
    """Return the approved CYB exit-rule candidate grid in a fixed order."""

    specs: list[ExitSpec] = []
    for max_hold in range(5, 16):
        specs.append(ExitSpec(f"time_hold_{max_hold}", "time", max_hold))

    for stop_loss in (0.04, 0.06, 0.08):
        for max_hold in (9, 12, 15):
            specs.append(
                ExitSpec(
                    f"stop_{_number_label(stop_loss)}_hold_{max_hold}",
                    "threshold",
                    max_hold,
                    stop_loss=stop_loss,
                )
            )
    for take_profit in (0.05, 0.08, 0.10, 0.12):
        for max_hold in (9, 12, 15):
            specs.append(
                ExitSpec(
                    f"take_{_number_label(take_profit)}_hold_{max_hold}",
                    "threshold",
                    max_hold,
                    take_profit=take_profit,
                )
            )
    for trail_pct in (0.04, 0.06, 0.08, 0.10):
        for trail_activation in (0.0, 0.03, 0.05):
            for max_hold in (9, 12, 15):
                specs.append(
                    ExitSpec(
                        "trail_"
                        f"{_number_label(trail_pct)}_activate_"
                        f"{_number_label(trail_activation)}_hold_{max_hold}",
                        "trailing",
                        max_hold,
                        trail_pct=trail_pct,
                        trail_activation=trail_activation,
                    )
                )
    for atr_multiple in (2.0, 2.5, 3.0):
        for max_hold in (9, 12, 15):
            specs.append(
                ExitSpec(
                    f"atr_{_number_label(atr_multiple)}_hold_{max_hold}",
                    "atr",
                    max_hold,
                    atr_multiple=atr_multiple,
                )
            )
    for ma_period in (5, 10, 20):
        for min_hold in (2, 3):
            for max_hold in (9, 12, 15):
                specs.append(
                    ExitSpec(
                        f"ma_{ma_period}_min_{min_hold}_hold_{max_hold}",
                        "ma",
                        max_hold,
                        ma_period=ma_period,
                        min_hold=min_hold,
                    )
                )
    for min_hold in (2, 3):
        for max_hold in (9, 12, 15):
            specs.append(
                ExitSpec(
                    f"kdj_min_{min_hold}_hold_{max_hold}",
                    "kdj",
                    max_hold,
                    kdj_dead_cross=True,
                    min_hold=min_hold,
                )
            )
    for min_hold in (2, 3):
        for max_hold in (9, 12, 15):
            specs.append(
                ExitSpec(
                    f"combo_kdj_trail_08_min_{min_hold}_hold_{max_hold}",
                    "combo",
                    max_hold,
                    trail_pct=0.08,
                    kdj_dead_cross=True,
                    min_hold=min_hold,
                )
            )
    for ma_period in (5, 10, 20):
        for min_hold in (2, 3):
            for max_hold in (9, 12, 15):
                specs.append(
                    ExitSpec(
                        f"combo_ma_{ma_period}_trail_08_min_{min_hold}_hold_{max_hold}",
                        "combo",
                        max_hold,
                        trail_pct=0.08,
                        ma_period=ma_period,
                        min_hold=min_hold,
                    )
                )
    return specs


def _complexity(spec: ExitSpec) -> int:
    """Return a small, transparent count of active exit-rule components."""

    return 1 + sum(
        value
        for value in (
            spec.stop_loss is not None,
            spec.take_profit is not None,
            spec.trail_pct is not None,
            spec.atr_multiple is not None,
            spec.ma_period is not None,
            spec.kdj_dead_cross,
        )
    )


def summarize_run(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    spec: ExitSpec,
    cost: float,
) -> dict[str, object]:
    """Summarize one simulation with stable no-data conventions.

    Empty daily curves return zero for curve-level metrics.  Empty trade tables
    return zero for count and win rate, and NaN for trade-distribution metrics.
    Calmar is zero when there is no drawdown, avoiding an infinite ratio.
    """

    trade_returns = pd.to_numeric(trades.get("net_return", pd.Series(dtype=float)), errors="coerce")
    holding_days = pd.to_numeric(trades.get("holding_days", pd.Series(dtype=float)), errors="coerce")
    completed = int(len(trades))
    if daily.empty:
        total_return = annualized_return = max_drawdown = calmar = 0.0
    else:
        equity = pd.to_numeric(daily["equity"], errors="coerce")
        if (
            equity.isna().any()
            or not bool(equity.map(math.isfinite).all())
            or (equity <= 0).any()
        ):
            raise ValueError("daily equity must be finite and positive")
        total_return = float(equity.iloc[-1] - 1.0)
        drawdowns = equity / equity.cummax() - 1.0
        max_drawdown = float(min(drawdowns.min(), 0.0))
        dates = pd.to_datetime(daily["date"], errors="raise")
        elapsed_days = int((dates.iloc[-1] - dates.iloc[0]).days)
        if elapsed_days <= 0:
            annualized_return = total_return
        else:
            exponent = math.log(float(equity.iloc[-1])) * 365.25 / elapsed_days
            if exponent >= math.log(sys.float_info.max):
                annualized_return = sys.float_info.max
            else:
                annualized_return = math.expm1(exponent)
        if max_drawdown == 0.0:
            calmar = 0.0
        elif abs(annualized_return) >= sys.float_info.max * abs(max_drawdown):
            calmar = math.copysign(sys.float_info.max, annualized_return)
        else:
            calmar = annualized_return / abs(max_drawdown)

    if completed == 0:
        win_rate = 0.0
        mean_return = median_return = best_return = worst_return = float("nan")
        average_holding_days = float("nan")
    else:
        win_rate = float((trade_returns > 0.0).mean())
        mean_return = float(trade_returns.mean())
        median_return = float(trade_returns.median())
        best_return = float(trade_returns.max())
        worst_return = float(trade_returns.min())
        average_holding_days = float(holding_days.mean())
    return {
        "spec_name": spec.name,
        "family": spec.family,
        "cost": float(cost),
        "complexity": _complexity(spec),
        "trades": completed,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "win_rate": win_rate,
        "mean_net_trade_return": mean_return,
        "median_net_trade_return": median_return,
        "best_net_trade_return": best_return,
        "worst_net_trade_return": worst_return,
        "average_holding_days": average_holding_days,
    }


def run_grid(
    frame: pd.DataFrame, config: object
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate every approved spec at baseline, primary, and sensitivity costs."""

    try:
        configured_costs = (0.0, float(config.primary_cost), float(config.sensitivity_cost))
    except AttributeError as exc:
        raise ValueError("config must provide primary_cost and sensitivity_cost") from exc
    costs = sorted(set(configured_costs))
    summary_rows: list[dict[str, object]] = []
    all_trades: list[pd.DataFrame] = []
    for spec in sorted(build_exit_specs(), key=lambda candidate: candidate.name):
        for cost in costs:
            daily, trades = simulate_exit(frame, spec, cost)
            summary_rows.append(summarize_run(daily, trades, spec, cost))
            if not trades.empty:
                annotated = trades.copy()
                annotated.insert(0, "cost", cost)
                annotated.insert(0, "family", spec.family)
                annotated.insert(0, "spec_name", spec.name)
                all_trades.append(annotated)
    return (
        pd.DataFrame(summary_rows, columns=_SUMMARY_COLUMNS),
        pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(columns=_GRID_TRADE_COLUMNS),
    )


_RANKED_CANDIDATE_COLUMNS = [
    *_SUMMARY_COLUMNS,
    "baseline_spec_name",
    "baseline_total_return",
    "excess_return_over_baseline",
    "years_with_trades",
    "profitable_years",
    "largest_yearly_profit_contribution",
    "annual_contributions",
    "neighbor_stable",
    "rank",
]
_LEAVE_ONE_YEAR_OUT_COLUMNS = [
    "excluded_year",
    "spec_name",
    "family",
    "cost",
    "complexity",
    "trades",
    "total_return",
    "annualized_return",
    "max_drawdown",
    "calmar",
    "rank",
]
_ADJACENCY_FIELDS = (
    "max_hold",
    "stop_loss",
    "take_profit",
    "trail_pct",
    "trail_activation",
    "atr_multiple",
    "ma_period",
    "kdj_dead_cross",
    "min_hold",
)


def _configured_cost(config: object, attribute: str, default: float) -> float:
    """Read a finite transaction cost while retaining usable defaults for callers."""

    value = float(getattr(config, attribute, default))
    if not math.isfinite(value) or not 0.0 <= value < 1.0:
        raise ValueError(f"{attribute} must be a finite value in [0, 1)")
    return value


def _finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _full_daily_baseline_return(
    primary: pd.DataFrame, baseline_daily: pd.DataFrame | None
) -> float:
    """Derive and audit the `time_hold_9` full-daily comparison hurdle.

    A supplied daily curve is the authoritative value for callers that run a
    limited candidate prefix.  When that curve is also represented in the
    primary summary, the two values must agree.  Without a supplied curve, a
    complete-grid summary must contain exactly one primary-cost baseline row.
    """

    baseline_rows = primary.loc[primary["spec_name"] == "time_hold_9", "total_return"]
    if baseline_daily is None:
        if len(baseline_rows) != 1 or not _finite_number(baseline_rows.iloc[0]):
            raise ValueError(
                "rank_candidates requires the primary-cost time_hold_9 full-daily baseline"
            )
        return float(baseline_rows.iloc[0])

    if "equity" not in baseline_daily:
        raise ValueError("baseline_daily is missing required column: equity")
    equity = pd.to_numeric(baseline_daily["equity"], errors="coerce")
    if equity.empty or equity.isna().any() or not bool(equity.map(math.isfinite).all()):
        raise ValueError("baseline_daily equity must be finite and nonempty")
    baseline_return = float(equity.iloc[-1] - 1.0)
    if not baseline_rows.empty:
        if len(baseline_rows) != 1 or not _finite_number(baseline_rows.iloc[0]):
            raise ValueError("primary summary has an invalid time_hold_9 baseline")
        if not math.isclose(
            float(baseline_rows.iloc[0]), baseline_return, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("primary summary time_hold_9 does not match baseline_daily")
    return baseline_return


def _summary_with_stable_columns(summary: pd.DataFrame) -> pd.DataFrame:
    """Copy summary data into the public ranking schema without mutating it."""

    result = summary.copy(deep=True)
    specs_by_name = {spec.name: spec for spec in build_exit_specs()}
    for column in _SUMMARY_COLUMNS:
        if column not in result:
            if column == "family":
                result[column] = result.get("spec_name", pd.Series(dtype=object)).map(
                    lambda name: specs_by_name.get(name).family if name in specs_by_name else pd.NA
                )
            elif column == "complexity":
                result[column] = result.get("spec_name", pd.Series(dtype=object)).map(
                    lambda name: _complexity(specs_by_name[name]) if name in specs_by_name else pd.NA
                )
            else:
                result[column] = pd.NA
    return result


def _annual_trade_contributions(
    trades: pd.DataFrame, primary_cost: float
) -> dict[str, tuple[dict[int, float], int, int, float]]:
    """Compound completed net trade returns by their exit calendar year.

    The returned annual contribution for a year is
    ``product(1 + net_return) - 1`` over trades whose recorded exit date is in
    that year.  The mapping is inserted in ascending calendar-year order, so
    it is deterministic both in memory and when rendered by a caller.
    """

    required = {"spec_name", "cost", "exit_date", "net_return"}
    if trades.empty or not required.issubset(trades.columns):
        return {}
    data = trades.loc[:, list(required)].copy()
    data["cost"] = pd.to_numeric(data["cost"], errors="coerce")
    data["net_return"] = pd.to_numeric(data["net_return"], errors="coerce")
    data["exit_date"] = pd.to_datetime(data["exit_date"], errors="coerce")
    data = data.loc[
        (data["cost"] == primary_cost)
        & data["exit_date"].notna()
        & data["net_return"].map(_finite_number)
    ].copy()
    if data.empty:
        return {}
    data["exit_year"] = data["exit_date"].dt.year
    output: dict[str, tuple[dict[int, float], int, int, float]] = {}
    for spec_name, by_spec in data.groupby("spec_name", sort=True):
        annual: dict[int, float] = {}
        for year, by_year in by_spec.groupby("exit_year", sort=True):
            contribution = math.prod(1.0 + float(value) for value in by_year["net_return"]) - 1.0
            annual[int(year)] = contribution
        values = list(annual.values())
        output[str(spec_name)] = (
            annual,
            len(annual),
            sum(value > 0.0 for value in values),
            max((value for value in values if value > 0.0), default=0.0),
        )
    return output


def _specs_are_adjacent(left: ExitSpec, right: ExitSpec, family_specs: list[ExitSpec]) -> bool:
    """Test one-step lattice adjacency using actual normalized ExitSpec values."""

    if left.family != right.family:
        return False
    differing = [field for field in _ADJACENCY_FIELDS if getattr(left, field) != getattr(right, field)]
    if len(differing) != 1:
        return False
    field = differing[0]
    fixed = tuple(candidate for candidate in _ADJACENCY_FIELDS if candidate != field)
    # Candidate-grid declaration order is the parameter domain order.  Keeping
    # it explicitly avoids comparing nullable values such as ``None`` and 5,
    # while still making only adjacent values in the actual normalized lattice
    # neighbors.  (The full behavioral identity above separates KDJ and MA
    # combo branches before a domain is built.)
    values = list(
        dict.fromkeys(
            getattr(candidate, field)
            for candidate in family_specs
            if all(getattr(candidate, other) == getattr(left, other) for other in fixed)
        )
    )
    try:
        return abs(values.index(getattr(left, field)) - values.index(getattr(right, field))) == 1
    except ValueError:
        return False


def _neighbor_stability(primary: pd.DataFrame) -> dict[str, bool]:
    """Return stability flags for primary-cost candidates from the approved grid."""

    specs_by_name = {spec.name: spec for spec in build_exit_specs()}
    by_family: dict[str, list[ExitSpec]] = {}
    for spec in specs_by_name.values():
        by_family.setdefault(spec.family, []).append(spec)

    primary_by_name = {
        str(row["spec_name"]): row
        for _, row in primary.drop_duplicates("spec_name", keep="last").iterrows()
    }
    flags: dict[str, bool] = {}
    for spec_name, row in primary_by_name.items():
        spec = specs_by_name.get(spec_name)
        if spec is None or not _finite_number(row["total_return"]):
            flags[spec_name] = False
            continue
        direction = (float(row["total_return"]) > 0.0) - (float(row["total_return"]) < 0.0)
        stable = False
        for neighbor in by_family[spec.family]:
            if not _specs_are_adjacent(spec, neighbor, by_family[spec.family]):
                continue
            neighbor_row = primary_by_name.get(neighbor.name)
            if neighbor_row is None:
                continue
            if not (
                _finite_number(neighbor_row["total_return"])
                and _finite_number(neighbor_row["max_drawdown"])
                and float(neighbor_row["max_drawdown"]) >= -0.20
            ):
                continue
            neighbor_direction = (
                (float(neighbor_row["total_return"]) > 0.0)
                - (float(neighbor_row["total_return"]) < 0.0)
            )
            if direction == neighbor_direction:
                stable = True
                break
        flags[spec_name] = stable
    return flags


def _rank_metric_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Apply the public deterministic performance ordering and one-based rank."""

    ranked = rows.copy()
    ranked["_absolute_max_drawdown"] = ranked["max_drawdown"].abs()
    ranked = ranked.sort_values(
        ["total_return", "calmar", "_absolute_max_drawdown", "complexity", "spec_name"],
        ascending=[False, False, True, True, True],
        kind="stable",
    ).reset_index(drop=True)
    ranked["rank"] = pd.RangeIndex(1, len(ranked) + 1)
    return ranked.drop(columns="_absolute_max_drawdown")


def rank_candidates(
    summary: pd.DataFrame,
    trades: pd.DataFrame,
    config: object,
    *,
    baseline_daily: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Qualify rules against the exact `time_hold_9` full-daily benchmark.

    Candidate returns and the benchmark are both final marked-to-market daily
    equity returns.  Completed-trade products remain available to callers as
    a separate disclosure and are never used as this qualification hurdle.
    """

    primary_cost = _configured_cost(config, "primary_cost", 0.001)
    sensitivity_cost = _configured_cost(config, "sensitivity_cost", 0.0015)
    prepared = _summary_with_stable_columns(summary)
    if prepared.empty:
        return pd.DataFrame(columns=_RANKED_CANDIDATE_COLUMNS)

    prepared["cost"] = pd.to_numeric(prepared["cost"], errors="coerce")
    primary = prepared.loc[prepared["cost"] == primary_cost].copy()
    sensitivity = prepared.loc[prepared["cost"] == sensitivity_cost].copy()
    if primary.empty:
        return pd.DataFrame(columns=_RANKED_CANDIDATE_COLUMNS)
    baseline_total_return = _full_daily_baseline_return(primary, baseline_daily)

    sensitivity_passes = (
        sensitivity.groupby("spec_name", sort=False)["total_return"]
        .apply(lambda values: bool(len(values)) and all(_finite_number(value) and float(value) > 0.0 for value in values))
        .to_dict()
    )
    finite_primary = primary[["total_return", "max_drawdown", "calmar"]].map(_finite_number).all(axis=1)
    qualified = primary.loc[
        finite_primary
        & (pd.to_numeric(primary["total_return"], errors="coerce") > baseline_total_return)
        & (pd.to_numeric(primary["max_drawdown"], errors="coerce") >= -0.20)
        & primary["spec_name"].map(lambda name: bool(sensitivity_passes.get(name, False)))
    ].copy()
    if qualified.empty:
        return pd.DataFrame(columns=_RANKED_CANDIDATE_COLUMNS)

    contributions = _annual_trade_contributions(trades, primary_cost)
    qualified["baseline_spec_name"] = "time_hold_9"
    qualified["baseline_total_return"] = baseline_total_return
    qualified["excess_return_over_baseline"] = (
        pd.to_numeric(qualified["total_return"], errors="coerce") - baseline_total_return
    )
    qualified["annual_contributions"] = qualified["spec_name"].map(
        lambda name: contributions.get(str(name), ({}, 0, 0, 0.0))[0]
    )
    qualified["years_with_trades"] = qualified["spec_name"].map(
        lambda name: contributions.get(str(name), ({}, 0, 0, 0.0))[1]
    )
    qualified["profitable_years"] = qualified["spec_name"].map(
        lambda name: contributions.get(str(name), ({}, 0, 0, 0.0))[2]
    )
    qualified["largest_yearly_profit_contribution"] = qualified["spec_name"].map(
        lambda name: contributions.get(str(name), ({}, 0, 0, 0.0))[3]
    )
    stability = _neighbor_stability(primary)
    qualified["neighbor_stable"] = qualified["spec_name"].map(stability).fillna(False).astype(bool)
    ranked = _rank_metric_rows(qualified)
    return ranked.reindex(columns=_RANKED_CANDIDATE_COLUMNS)


def _year_segments(frame: pd.DataFrame) -> list[pd.DataFrame]:
    """Split a filtered study at missing calendar years to prevent state bridging."""

    if frame.empty:
        return []
    ordered = frame.copy(deep=True)
    ordered["date"] = pd.to_datetime(ordered["date"], errors="raise")
    ordered = ordered.sort_values("date", kind="stable").reset_index(drop=True)
    years = ordered["date"].dt.year
    starts = years.diff().fillna(0).gt(1)
    boundaries = list(starts[starts].index)
    return [
        segment.reset_index(drop=True)
        for segment in ([ordered.iloc[start:end] for start, end in zip([0, *boundaries], [*boundaries, len(ordered)])])
        if not segment.empty
    ]


def _simulate_segments(
    segments: list[pd.DataFrame], spec: ExitSpec, cost: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run each calendar segment independently and retain a cumulative equity scale."""

    all_daily: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    realized_equity = 1.0
    for segment_index, segment in enumerate(segments):
        daily, trades = simulate_exit(segment, spec, cost)
        if (
            segment_index < len(segments) - 1
            and not daily.empty
            and int(daily.iloc[-1]["position"]) == 1
        ):
            # The next segment starts from a reset state, so an unclosed
            # position at this removed-year boundary is not a trade and cannot
            # contribute either return or drawdown.  Neutralize its entire
            # marked-to-market tail rather than retaining a mark that vanishes
            # when the following segment begins.
            active_start = len(daily) - 1
            while active_start > 0 and int(daily.iloc[active_start - 1]["position"]) == 1:
                active_start -= 1
            boundary_equity = (
                float(daily.iloc[active_start - 1]["equity"])
                if active_start > 0
                else 1.0
            )
            daily = daily.copy()
            daily.loc[active_start:, "equity"] = boundary_equity
            daily.loc[active_start:, "position"] = 0
            daily.loc[active_start:, "action"] = "discard_open_at_year_gap"
        scaled_daily = daily.copy()
        if not scaled_daily.empty:
            scaled_daily["equity"] = scaled_daily["equity"] * realized_equity
            all_daily.append(scaled_daily)
        if not trades.empty:
            all_trades.append(trades)
            realized_equity *= math.prod(1.0 + float(value) for value in trades["net_return"])
    daily_result = (
        pd.concat(all_daily, ignore_index=True)
        if all_daily
        else pd.DataFrame(columns=["date", "equity", "position", "action"])
    )
    trades_result = (
        pd.concat(all_trades, ignore_index=True)
        if all_trades
        else pd.DataFrame(columns=_GRID_TRADE_COLUMNS[3:])
    )
    return daily_result, trades_result


def leave_one_year_out(
    frame: pd.DataFrame, specs: list[ExitSpec], config: object
) -> pd.DataFrame:
    """Re-run each supplied fixed rule after excluding each calendar year 2020--2026."""

    primary_cost = _configured_cost(config, "primary_cost", 0.001)
    if "date" not in frame:
        raise ValueError("frame is missing required columns: ['date']")
    data = frame.copy(deep=True)
    data["date"] = pd.to_datetime(data["date"], errors="raise")
    supplied_specs = list(specs)
    rows: list[dict[str, object]] = []
    for excluded_year in range(2020, 2027):
        subset = data.loc[data["date"].dt.year != excluded_year].copy()
        segments = _year_segments(subset)
        year_rows: list[dict[str, object]] = []
        for spec in supplied_specs:
            daily, trades = _simulate_segments(segments, spec, primary_cost)
            summary = summarize_run(daily, trades, spec, primary_cost)
            year_rows.append({"excluded_year": excluded_year, **summary})
        if year_rows:
            ranked = _rank_metric_rows(pd.DataFrame(year_rows))
            rows.extend(ranked.to_dict("records"))
    return pd.DataFrame(rows, columns=_LEAVE_ONE_YEAR_OUT_COLUMNS)
