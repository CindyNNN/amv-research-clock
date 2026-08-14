"""Relative crowding-heat rules for ChiNext ETF timing.

Skeleton: high relative heat means the Bagholder50 basket is beating ChiNext,
i.e. a low-quality chase. That is an exit / stay-flat condition, not a buy list.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterator, Literal

import numpy as np
import pandas as pd

Window = Literal["expanding", "roll504", "roll756"]
Mode = Literal["hyst", "binary"]


@dataclass(frozen=True)
class RelHeatRule:
    name: str
    window: Window = "expanding"
    mode: Mode = "hyst"
    buy_th: float = 30.0
    sell_th: float = 70.0
    min_hold_days: int = 0
    exit_ignore_if_above_ma: int | None = None
    require_above_ma: int | None = None
    peak_dd_exit: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def n_optional(self) -> int:
        return int(
            sum(
                [
                    self.min_hold_days > 0,
                    self.exit_ignore_if_above_ma is not None,
                    self.require_above_ma is not None,
                    self.peak_dd_exit is not None,
                    self.window != "expanding",
                ]
            )
        )


def _percentile_last(values: np.ndarray, min_periods: int, window: int | None) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        v = values[i]
        if not np.isfinite(v):
            continue
        start = 0 if window is None else max(0, i + 1 - window)
        chunk = values[start : i + 1]
        chunk = chunk[np.isfinite(chunk)]
        if len(chunk) < min_periods:
            continue
        out[i] = float((chunk <= v).mean() * 100.0)
    return out


def enrich_rel_heat(frame: pd.DataFrame) -> pd.DataFrame:
    """Causal relative-heat features. Requires close and bh_ret20."""
    out = frame.copy().sort_values("date").reset_index(drop=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    close = pd.to_numeric(out["close"], errors="raise")
    out["etf_ret20"] = close / close.shift(20) - 1.0
    out["rel_spread"] = pd.to_numeric(out["bh_ret20"], errors="raise") - out["etf_ret20"]
    spread = out["rel_spread"].to_numpy(dtype=float)
    out["rel_heat_exp"] = _percentile_last(spread, min_periods=252, window=None)
    out["rel_heat_504"] = _percentile_last(spread, min_periods=252, window=504)
    out["rel_heat_756"] = _percentile_last(spread, min_periods=252, window=756)
    for n in (20, 60):
        out[f"ma{n}"] = close.rolling(n, min_periods=n).mean()
    return out


def heat_column(rule: RelHeatRule) -> str:
    if rule.window == "roll504":
        return "rel_heat_504"
    if rule.window == "roll756":
        return "rel_heat_756"
    return "rel_heat_exp"


def apply_rel_heat_rule(frame: pd.DataFrame, rule: RelHeatRule) -> pd.DataFrame:
    out = frame.copy().reset_index(drop=True)
    heat = pd.to_numeric(out[heat_column(rule)], errors="coerce")
    if rule.mode == "binary":
        entry = heat <= rule.buy_th
        exit_hit = heat > rule.buy_th
    else:
        entry = heat <= rule.buy_th
        exit_hit = heat >= rule.sell_th

    if rule.require_above_ma is not None:
        ma = out[f"ma{rule.require_above_ma}"]
        entry = entry & (out["close"] > ma)
    if rule.exit_ignore_if_above_ma is not None:
        ma = out[f"ma{rule.exit_ignore_if_above_ma}"]
        exit_hit = exit_hit & ~(out["close"] > ma)

    out["entry_signal"] = entry.fillna(False)
    out["exit_signal"] = exit_hit.fillna(False)
    out["min_hold_days"] = int(rule.min_hold_days)
    out["rule_name"] = rule.name
    out["rel_heat"] = heat
    return out


def baseline_rel_heat_rules() -> list[RelHeatRule]:
    return [
        RelHeatRule(name="exp_hyst_30_70", window="expanding", mode="hyst", buy_th=30, sell_th=70),
        RelHeatRule(name="exp_hyst_40_60", window="expanding", mode="hyst", buy_th=40, sell_th=60),
        RelHeatRule(name="exp_bin_50", window="expanding", mode="binary", buy_th=50, sell_th=50),
    ]


def iter_rel_heat_hypotheses() -> Iterator[RelHeatRule]:
    cores = [
        dict(window="expanding", mode="hyst", buy=30, sell=70),
        dict(window="expanding", mode="hyst", buy=25, sell=75),
        dict(window="expanding", mode="hyst", buy=20, sell=80),
        dict(window="expanding", mode="hyst", buy=40, sell=60),
        dict(window="expanding", mode="binary", buy=40, sell=40),
        dict(window="expanding", mode="binary", buy=50, sell=50),
        dict(window="expanding", mode="binary", buy=60, sell=60),
        dict(window="expanding", mode="binary", buy=70, sell=70),
        dict(window="roll756", mode="hyst", buy=30, sell=70),
        dict(window="roll756", mode="binary", buy=50, sell=50),
        dict(window="roll504", mode="hyst", buy=30, sell=70),
        dict(window="roll504", mode="binary", buy=50, sell=50),
    ]
    addons = [
        {},
        {"h": 5},
        {"h": 10},
        {"ma_exit": 60},
        {"ma_exit": 20},
        {"ma_entry": 60},
        {"peak_dd": 0.10},
        {"h": 5, "ma_exit": 60},
    ]
    seen: set[str] = set()
    for core, addon in product(cores, addons):
        name = (
            f"{core['window']}|{core['mode']}|b{core['buy']}|s{core['sell']}"
            f"|h{addon.get('h', 0)}|xe{addon.get('ma_exit')}|ae{addon.get('ma_entry')}"
            f"|dd{addon.get('peak_dd')}"
        )
        if name in seen:
            continue
        seen.add(name)
        yield RelHeatRule(
            name=name,
            window=core["window"],
            mode=core["mode"],
            buy_th=float(core["buy"]),
            sell_th=float(core["sell"]),
            min_hold_days=int(addon.get("h", 0)),
            exit_ignore_if_above_ma=addon.get("ma_exit"),
            require_above_ma=addon.get("ma_entry"),
            peak_dd_exit=addon.get("peak_dd"),
        )
