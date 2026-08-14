"""ChiNext-focused gate rules: beat buy-and-hold by capturing more bull exposure."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ai_invest_advisor.amv_index_data import ENTRY_TWO_DAY_SUM, EXIT_THRESHOLD
from ai_invest_advisor.amv_strategy_variants import GateRule, _ma, apply_gate_rule


@dataclass(frozen=True)
class CybGateRule(GateRule):
    """Extra ChiNext knobs on top of the shared GateRule fields."""

    # Exit if 2-day AMV sum is very negative (crash cluster).
    exit_two_day_sum: float | None = None
    # Require index below MA before allowing AMV crash exit.
    exit_require_index_below_ma: int | None = None
    # Minimum holding days before an exit can trigger.
    min_hold_days: int = 0


def apply_cyb_gate_rule(frame: pd.DataFrame, rule: CybGateRule) -> pd.DataFrame:
    out = apply_gate_rule(frame, rule)
    exit_hit = out["exit_signal"].astype(bool).copy()

    if rule.exit_two_day_sum is not None:
        exit_hit = exit_hit | (out["amv_ret_2d_sum"] <= rule.exit_two_day_sum)
    if rule.exit_require_index_below_ma is not None:
        idx_ma = _ma(out["close"], rule.exit_require_index_below_ma)
        exit_hit = exit_hit & (out["close"] < idx_ma)

    out["exit_signal"] = exit_hit.fillna(False)
    out["min_hold_days"] = int(rule.min_hold_days)
    out["rule_name"] = rule.name
    return out


def cyb_candidate_rules() -> list[CybGateRule]:
    """Rules aimed at ChiNext: keep bear protection, raise bull exposure."""
    return [
        CybGateRule(name="baseline"),
        CybGateRule(name="exit_protect_ma20", exit_ignore_if_index_above_ma=20),
        CybGateRule(name="exit_protect_ma60", exit_ignore_if_index_above_ma=60),
        CybGateRule(
            name="exit_only_below_ma60",
            exit_require_index_below_ma=60,
        ),
        CybGateRule(
            name="exit_only_below_ma20",
            exit_require_index_below_ma=20,
        ),
        CybGateRule(name="exit_neg3", exit_threshold=-0.03),
        CybGateRule(name="exit_neg35", exit_threshold=-0.035),
        CybGateRule(name="exit_neg4", exit_threshold=-0.04),
        CybGateRule(
            name="exit_neg3_below_ma60",
            exit_threshold=-0.03,
            exit_require_index_below_ma=60,
        ),
        CybGateRule(
            name="exit_neg23_or_2d_neg4_below_ma60",
            exit_threshold=-0.023,
            exit_two_day_sum=-0.04,
            exit_require_index_below_ma=60,
        ),
        CybGateRule(
            name="fast_reentry_sum3_protect_ma60",
            entry_two_day_sum=0.03,
            exit_ignore_if_index_above_ma=60,
        ),
        CybGateRule(
            name="fast_reentry_ma5_protect_ma60",
            entry_two_day_sum=0.03,
            entry_amv_ma_cross=5,
            exit_ignore_if_index_above_ma=60,
        ),
        CybGateRule(
            name="bull_capture_v1",
            entry_two_day_sum=0.03,
            entry_amv_ma_cross=5,
            exit_threshold=-0.03,
            exit_require_index_below_ma=60,
        ),
        CybGateRule(
            name="bull_capture_v2",
            entry_two_day_sum=0.035,
            entry_amv_ma_cross=10,
            exit_threshold=-0.035,
            exit_require_index_below_ma=60,
            min_hold_days=5,
        ),
        CybGateRule(
            name="bull_capture_v3",
            entry_two_day_sum=0.03,
            entry_if_amv_above_ma=20,
            exit_threshold=-0.03,
            exit_require_index_below_ma=60,
        ),
        CybGateRule(
            name="crash_only_neg4_below_ma60",
            exit_threshold=-0.04,
            exit_require_index_below_ma=60,
            entry_two_day_sum=0.03,
            entry_amv_ma_cross=5,
        ),
        CybGateRule(
            name="min_hold_10_protect_ma20",
            exit_ignore_if_index_above_ma=20,
            min_hold_days=10,
        ),
        CybGateRule(
            name="min_hold_5_exit_neg3_below_ma60",
            exit_threshold=-0.03,
            exit_require_index_below_ma=60,
            entry_two_day_sum=0.03,
            min_hold_days=5,
        ),
    ]


def get_cyb_rule(name: str) -> CybGateRule:
    for rule in cyb_candidate_rules():
        if rule.name == name:
            return rule
    raise KeyError(name)
