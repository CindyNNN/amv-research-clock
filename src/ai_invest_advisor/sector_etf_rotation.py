"""Monthly industry-ETF rotation on top of the ChiNext 0AMV gate.

The engine is causal: ranks use yesterday's close, fills at today's open,
and a new month freezes last month-end ranks. Full-book rebuild when the
pick set changes (conservative costs for top-k > 1).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product

import pandas as pd

NEXT_LONG_ACTIONS = {"schedule_entry", "hold", "entry", "hold_min_hold"}


@dataclass(frozen=True)
class RotRule:
    name: str
    lookback: int = 20
    top_k: int = 1
    skip_negative: bool = False
    use_gate: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def n_optional(self) -> int:
        return (
            int(self.lookback != 20)
            + int(self.top_k != 1)
            + int(self.skip_negative)
        )


def baseline_rot_rules() -> list[RotRule]:
    return [
        RotRule("m20_k1_raw_gate", lookback=20, top_k=1, skip_negative=False, use_gate=True),
        RotRule("m20_k1_nogate", lookback=20, top_k=1, skip_negative=False, use_gate=False),
        RotRule("m60_k1_gate", lookback=60, top_k=1, skip_negative=False, use_gate=True),
    ]


def iter_rot_hypotheses() -> list[RotRule]:
    rules: list[RotRule] = []
    seen: set[str] = set()
    for lookback, top_k, skip_negative in product((20, 60, 120), (1, 2, 3), (False, True)):
        name = f"m{lookback}_k{top_k}_{'skipneg' if skip_negative else 'raw'}_gate"
        rule = RotRule(
            name=name,
            lookback=lookback,
            top_k=top_k,
            skip_negative=skip_negative,
            use_gate=True,
        )
        if name not in seen:
            seen.add(name)
            rules.append(rule)
    return rules


def _want_long_next(action: str) -> bool:
    return str(action) in NEXT_LONG_ACTIONS


def _rank_picks(
    scores: dict[str, float],
    *,
    top_k: int,
    skip_negative: bool,
) -> tuple[str, ...]:
    ordered = sorted(scores, key=scores.get, reverse=True)
    picks: list[str] = []
    for code in ordered:
        if skip_negative and scores[code] <= 0:
            continue
        picks.append(code)
        if len(picks) >= top_k:
            break
    return tuple(picks)


def rotate(
    prices: dict[str, pd.DataFrame],
    gate_daily: pd.DataFrame,
    *,
    rule: RotRule,
    cost: float,
) -> dict:
    calendar = gate_daily[["date", "action"]].copy()
    calendar["date"] = pd.to_datetime(calendar["date"]).dt.normalize()
    calendar = calendar.sort_values("date").reset_index(drop=True)
    codes = [c for c in prices if c != "159915"]
    close_map: dict[str, pd.Series] = {}
    open_map: dict[str, pd.Series] = {}
    for code in codes:
        part = prices[code][["date", "open", "close"]].copy()
        part["date"] = pd.to_datetime(part["date"]).dt.normalize()
        close_map[code] = part.set_index("date")["close"]
        open_map[code] = part.set_index("date")["open"]

    equity = 1.0
    held: tuple[str, ...] = ()
    month_key = None
    month_picks: tuple[str, ...] = ()
    rows: list[dict] = []
    trades = 0
    dates = list(calendar.itertuples(index=False))

    for i, row in enumerate(dates):
        dt = row.date
        if i == 0:
            rows.append(
                {
                    "date": dt,
                    "equity": equity,
                    "held": "",
                    "n_held": 0,
                    "traded": 0,
                }
            )
            continue
        prev = dates[i - 1]
        prev_dt = prev.date
        want_long = True if not rule.use_gate else _want_long_next(prev.action)
        scores: dict[str, float] = {}
        for code in codes:
            series = close_map[code]
            if prev_dt not in series.index:
                continue
            loc = series.index.get_loc(prev_dt)
            if not isinstance(loc, int) or loc < rule.lookback:
                continue
            past = series.iloc[loc - rule.lookback]
            now = series.iloc[loc]
            if pd.isna(past) or pd.isna(now) or past <= 0:
                continue
            scores[code] = float(now / past - 1.0)
        ranked = _rank_picks(
            scores, top_k=rule.top_k, skip_negative=rule.skip_negative
        )
        ym = str(pd.Timestamp(dt).to_period("M"))
        if month_key != ym:
            month_picks = ranked
            month_key = ym
        picks = month_picks if want_long else ()
        if not want_long:
            picks = ()

        traded = 0
        if held:
            gap = 0.0
            live = 0
            for code in held:
                if dt in open_map[code].index and prev_dt in close_map[code].index:
                    gap += float(open_map[code].loc[dt]) / float(close_map[code].loc[prev_dt]) - 1.0
                    live += 1
            if live:
                equity *= 1.0 + gap / live
        if held != picks:
            if held:
                equity *= 1.0 - cost
                trades += 1
                traded = 1
                held = ()
            if picks:
                buyable = tuple(
                    code
                    for code in picks
                    if dt in open_map[code].index and dt in close_map[code].index
                )
                if buyable:
                    equity *= 1.0 - cost
                    trades += 1
                    traded = 1
                    held = buyable
                    day_ret = 0.0
                    for code in held:
                        day_ret += float(close_map[code].loc[dt]) / float(open_map[code].loc[dt]) - 1.0
                    equity *= 1.0 + day_ret / len(held)
        elif held:
            day_ret = 0.0
            live = 0
            for code in held:
                if dt in close_map[code].index and dt in open_map[code].index:
                    day_ret += float(close_map[code].loc[dt]) / float(open_map[code].loc[dt]) - 1.0
                    live += 1
            if live:
                equity *= 1.0 + day_ret / live

        rows.append(
            {
                "date": dt,
                "equity": equity,
                "held": ",".join(held),
                "n_held": len(held),
                "traded": traded,
            }
        )

    daily = pd.DataFrame(rows)
    eq = daily["equity"]
    dd = float((eq / eq.cummax() - 1.0).min()) if not eq.empty else 0.0
    rets = eq.pct_change().dropna()
    if len(rets) > 1 and float(rets.std()) > 0:
        sharpe = float(rets.mean() / rets.std() * (252 ** 0.5))
    else:
        sharpe = 0.0
    exposure = float((daily["n_held"] > 0).mean()) if not daily.empty else 0.0
    last_held = daily["held"].iloc[-1] if not daily.empty else ""
    return {
        "total_return": float(eq.iloc[-1] - 1.0) if not eq.empty else 0.0,
        "max_drawdown": dd,
        "trades": int(trades),
        "sharpe": sharpe,
        "exposure": exposure,
        "last_held": None if not last_held else last_held,
        "daily": daily,
    }


def window_from_equity(
    daily: pd.DataFrame,
    *,
    start: str | None,
    end: str | None,
    equity_col: str = "equity",
) -> dict:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.sort_values("date")
    if start:
        before = frame[frame["date"] < pd.Timestamp(start)]
        during = frame[frame["date"] >= pd.Timestamp(start)]
    else:
        before = frame.iloc[0:0]
        during = frame
    if end:
        during = during[during["date"] <= pd.Timestamp(end)]
    if during.empty:
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "trades": 0,
            "skipped": True,
            "bars": 0,
        }
    start_eq = float(before[equity_col].iloc[-1]) if not before.empty else float(during[equity_col].iloc[0])
    end_eq = float(during[equity_col].iloc[-1])
    if start_eq <= 0:
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "trades": 0,
            "skipped": True,
            "bars": 0,
        }
    path = during[equity_col] / start_eq
    dd = float((path / path.cummax() - 1.0).min())
    trades = int(during["traded"].sum()) if "traded" in during.columns else 0
    return {
        "total_return": float(end_eq / start_eq - 1.0),
        "max_drawdown": dd,
        "trades": trades,
        "skipped": False,
        "bars": int(len(during)),
    }
