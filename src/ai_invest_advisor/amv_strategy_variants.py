"""Explainable 0AMV gate rule variants for beating buy-and-hold exposure gap."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ai_invest_advisor.amv_index_data import ENTRY_TWO_DAY_SUM, EXIT_THRESHOLD


@dataclass(frozen=True)
class GateRule:
    """Close-confirmed gate; all fields are explainable thresholds/filters."""

    name: str
    exit_threshold: float = EXIT_THRESHOLD
    entry_two_day_sum: float = ENTRY_TWO_DAY_SUM
    # Exit only if AMV close is below its MA.
    exit_require_amv_below_ma: int | None = None
    # Ignore exit when index close is above its MA (trend protection).
    exit_ignore_if_index_above_ma: int | None = None
    # Extra entry: AMV close crosses above its MA.
    entry_amv_ma_cross: int | None = None
    # Extra entry: AMV close above its MA (level filter).
    entry_if_amv_above_ma: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def apply_gate_rule(frame: pd.DataFrame, rule: GateRule) -> pd.DataFrame:
    """Rebuild entry/exit signals on an aligned index+AMV frame."""
    out = frame.copy()
    if "amv_close" not in out.columns or "amv_ret_1d" not in out.columns:
        raise ValueError("frame must contain amv_close and amv_ret_1d")
    if "amv_ret_2d_sum" not in out.columns:
        out["amv_ret_2d_sum"] = out["amv_ret_1d"] + out["amv_ret_1d"].shift(1)

    exit_hit = out["amv_ret_1d"] <= rule.exit_threshold
    if rule.exit_require_amv_below_ma is not None:
        amv_ma = _ma(out["amv_close"], rule.exit_require_amv_below_ma)
        exit_hit = exit_hit & (out["amv_close"] < amv_ma)
    if rule.exit_ignore_if_index_above_ma is not None:
        idx_ma = _ma(out["close"], rule.exit_ignore_if_index_above_ma)
        exit_hit = exit_hit & ~(out["close"] > idx_ma)

    entry_hit = out["amv_ret_2d_sum"] > rule.entry_two_day_sum
    if rule.entry_amv_ma_cross is not None:
        amv_ma = _ma(out["amv_close"], rule.entry_amv_ma_cross)
        cross = (out["amv_close"] > amv_ma) & (out["amv_close"].shift(1) <= amv_ma.shift(1))
        entry_hit = entry_hit | cross.fillna(False)
    if rule.entry_if_amv_above_ma is not None:
        amv_ma = _ma(out["amv_close"], rule.entry_if_amv_above_ma)
        entry_hit = entry_hit | (out["amv_close"] > amv_ma)

    out["exit_signal"] = exit_hit.fillna(False)
    out["entry_signal"] = entry_hit.fillna(False)
    out["rule_name"] = rule.name
    return out


def candidate_rules() -> list[GateRule]:
    """Small explainable grid aimed at raising exposure without killing timing."""
    return [
        GateRule(name="baseline"),
        GateRule(name="entry_sum_3pct", entry_two_day_sum=0.03),
        GateRule(name="entry_sum_35pct", entry_two_day_sum=0.035),
        GateRule(name="exit_neg3pct", exit_threshold=-0.03),
        GateRule(name="exit_neg25pct", exit_threshold=-0.025),
        GateRule(
            name="exit_neg23_below_amv_ma5",
            exit_require_amv_below_ma=5,
        ),
        GateRule(
            name="exit_neg23_below_amv_ma10",
            exit_require_amv_below_ma=10,
        ),
        GateRule(
            name="exit_protect_idx_ma60",
            exit_ignore_if_index_above_ma=60,
        ),
        GateRule(
            name="exit_protect_idx_ma20",
            exit_ignore_if_index_above_ma=20,
        ),
        GateRule(
            name="reentry_ma5_cross",
            entry_amv_ma_cross=5,
        ),
        GateRule(
            name="reentry_ma10_cross",
            entry_amv_ma_cross=10,
        ),
        GateRule(
            name="high_exp_entry3_exit_ma5",
            entry_two_day_sum=0.03,
            exit_require_amv_below_ma=5,
            entry_amv_ma_cross=5,
        ),
        GateRule(
            name="trend_protect_entry3",
            entry_two_day_sum=0.03,
            exit_ignore_if_index_above_ma=60,
            entry_amv_ma_cross=5,
        ),
        GateRule(
            name="balanced_v1",
            exit_threshold=-0.025,
            entry_two_day_sum=0.03,
            exit_require_amv_below_ma=5,
            exit_ignore_if_index_above_ma=60,
            entry_amv_ma_cross=5,
        ),
        GateRule(
            name="balanced_v2",
            exit_threshold=-0.023,
            entry_two_day_sum=0.035,
            exit_require_amv_below_ma=10,
            exit_ignore_if_index_above_ma=20,
            entry_amv_ma_cross=5,
        ),
        GateRule(
            name="stay_long_soft_exit",
            exit_threshold=-0.03,
            entry_two_day_sum=0.03,
            exit_require_amv_below_ma=5,
            entry_if_amv_above_ma=20,
        ),
    ]


def get_rule(name: str) -> GateRule:
    for rule in candidate_rules():
        if rule.name == name:
            return rule
    raise KeyError(f"unknown gate rule: {name}")


def recommended_rule() -> GateRule:
    """OOS-selected rule: keep crash exit, but don't sell while index > MA20."""
    return get_rule("exit_protect_idx_ma20")
