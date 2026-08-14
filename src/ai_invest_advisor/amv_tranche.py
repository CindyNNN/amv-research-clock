"""Five-unit position sizing on the ChiNext 0AMV clock.

Same close-confirm / next-open fill as the binary gate. Cost is charged on
the traded fraction of NAV (one-way 10bp on 20% of NAV = 2bp of total).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

N_UNITS = 5


@dataclass(frozen=True)
class TrancheSpec:
    name: str
    kind: str  # binary | ramp | step | sleeve_emotion | sleeve_delay
    note: str


def _apply_fills(
    frame: pd.DataFrame,
    target_units: pd.Series,
    *,
    n_units: int = N_UNITS,
    cost: float = 0.001,
) -> pd.DataFrame:
    data = frame.reset_index(drop=True).copy()
    target_units = target_units.reset_index(drop=True).astype(int)
    equity = 1.0
    units = 0
    rows: list[dict] = []
    fills = 0
    for i, row in data.iterrows():
        open_px = float(row["open"])
        close_px = float(row["close"])
        traded = 0
        if i == 0:
            rows.append(
                {
                    "date": row["date"],
                    "equity": equity,
                    "units": 0,
                    "weight": 0.0,
                    "traded_units": 0,
                    "open": open_px,
                    "close": close_px,
                }
            )
            continue
        prev_close = float(data.iloc[i - 1]["close"])
        weight = units / n_units
        equity *= 1.0 + weight * (open_px / prev_close - 1.0)
        want = int(target_units.iloc[i - 1])
        want = max(0, min(n_units, want))
        delta = want - units
        if delta != 0:
            equity *= 1.0 - cost * abs(delta) / n_units
            units = want
            traded = abs(delta)
            fills += 1
        weight = units / n_units
        equity *= 1.0 + weight * (close_px / open_px - 1.0)
        rows.append(
            {
                "date": row["date"],
                "equity": equity,
                "units": units,
                "weight": weight,
                "traded_units": traded,
                "open": open_px,
                "close": close_px,
            }
        )
    daily = pd.DataFrame(rows)
    if not daily.empty:
        daily.iloc[-1, daily.columns.get_loc("units")] = daily.iloc[-1]["units"]
    return daily, fills


def binary_targets(frame: pd.DataFrame, n_units: int = N_UNITS) -> pd.Series:
    """After each close: full n_units if the binary 0AMV machine wants to be long next open."""
    data = frame.reset_index(drop=True)
    units = 0
    pending = 0
    next_targets = []
    for i, row in data.iterrows():
        if pending == 1:
            units = n_units
            pending = 0
        elif pending == -1:
            units = 0
            pending = 0
        in_pos = units > 0
        if in_pos and bool(row["exit_signal"]) and i + 1 < len(data):
            pending = -1
        elif (not in_pos) and bool(row["entry_signal"]) and i + 1 < len(data):
            pending = 1
        if pending == 1:
            next_targets.append(n_units)
        elif pending == -1:
            next_targets.append(0)
        else:
            next_targets.append(units)
    return pd.Series(next_targets, index=data.index)


def ramp_targets(frame: pd.DataFrame, n_units: int = N_UNITS) -> pd.Series:
    """Same 0AMV regime as binary, but move one unit per session toward 0 or 5."""
    data = frame.reset_index(drop=True)
    regime = 0
    units = 0
    targets = []
    for i, row in data.iterrows():
        if regime == 1 and bool(row["exit_signal"]):
            regime = 0
        elif regime == 0 and bool(row["entry_signal"]):
            regime = 1
        if regime == 1:
            units = min(n_units, units + 1)
        else:
            units = max(0, units - 1)
        targets.append(units)
    return pd.Series(targets, index=data.index)


def step_targets(frame: pd.DataFrame, n_units: int = N_UNITS) -> pd.Series:
    """Each AMV entry adds one unit; each emotion exit removes one. Exit wins ties."""
    data = frame.reset_index(drop=True)
    units = 0
    targets = []
    for _i, row in data.iterrows():
        if bool(row["exit_signal"]) and units > 0:
            units -= 1
        elif bool(row["entry_signal"]) and units < n_units:
            units += 1
        targets.append(units)
    return pd.Series(targets, index=data.index)


def summarize_daily(daily: pd.DataFrame, *, fills: int) -> dict:
    eq = daily["equity"]
    dd = float((eq / eq.cummax() - 1.0).min()) if not eq.empty else 0.0
    rets = eq.pct_change().dropna()
    if len(rets) > 1 and float(rets.std()) > 0:
        sharpe = float(rets.mean() / rets.std() * (252 ** 0.5))
    else:
        sharpe = 0.0
    turnover = float(daily["traded_units"].sum() / N_UNITS) if "traded_units" in daily else 0.0
    last = daily.iloc[-1]
    return {
        "total_return": float(eq.iloc[-1] - 1.0) if not eq.empty else 0.0,
        "max_drawdown": dd,
        "sharpe": sharpe,
        "exposure": float(daily["weight"].mean()) if "weight" in daily else 0.0,
        "fills": int(fills),
        "turnover_nav": turnover,
        "last_units": int(last["units"]) if "units" in daily else 0,
        "last_weight": float(last["weight"]) if "weight" in daily else 0.0,
        "last_date": str(pd.Timestamp(last["date"]).date()),
        "daily": daily,
    }


def run_target_backtest(frame: pd.DataFrame, targets: pd.Series, *, cost: float) -> dict:
    daily, fills = _apply_fills(frame, targets, cost=cost)
    return summarize_daily(daily, fills=fills)


def mean_sleeve_equity(dailies: list[pd.DataFrame]) -> pd.DataFrame:
    base = dailies[0][["date"]].copy()
    eq = sum(d["equity"] for d in dailies) / len(dailies)
    units = sum(d["units"] for d in dailies) / len(dailies)
    traded = sum(d["traded_units"] for d in dailies)
    out = base.copy()
    out["equity"] = eq
    out["units"] = units
    out["weight"] = units / N_UNITS
    out["traded_units"] = traded
    return out
