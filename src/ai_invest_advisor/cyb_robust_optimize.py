"""Anti-overfit refinement of AMV + emotion ChiNext rules.

Design goals
------------
- Keep a locked economic skeleton (AMV momentum entry + emotion/AMV risk exit).
- Add only a few explainable overlays (trend / vol / volume / RSI / trailing risk).
- Prefer walk-forward stability and cost stress over raw in-sample excess.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterator

import numpy as np
import pandas as pd

from ai_invest_advisor.amv_index_backtest import run_index_backtest
from ai_invest_advisor.cyb_emotion_amv_combo import CombinedRule, apply_combined_rule, build_combined_frame


# Selection folds: expanding train implied by evaluating only on test windows.
# Holdout year is never used for picking the winner.
WF_FOLDS: list[tuple[str, str, str | None]] = [
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
]
HOLDOUT = ("2026", "2026-01-01", None)
COST_PRIMARY = 0.001
COST_STRESS = (0.0015, 0.002)


@dataclass(frozen=True)
class RobustRule:
    name: str
    amv_entry_two_day: float = 0.03
    amv_exit_threshold: float | None = -0.023
    emotion_exit_min: float | None = 70.0
    exit_ignore_if_above_ma: int | None = 20
    min_hold_days: int = 10
    # entry overlays
    require_above_ma: int | None = None
    max_atr_pctile: float | None = None
    min_vol_ratio: float | None = None
    # exit overlays
    rsi_exit_min: float | None = None
    emotion_requires_rsi: bool = False
    peak_dd_exit: float | None = None
    atr_trail_mult: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def n_optional(self) -> int:
        flags = [
            self.require_above_ma is not None,
            self.max_atr_pctile is not None,
            self.min_vol_ratio is not None,
            self.rsi_exit_min is not None,
            self.emotion_requires_rsi,
            self.peak_dd_exit is not None,
            self.atr_trail_mult is not None,
        ]
        return int(sum(flags))


def enrich_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Causal indicators for overlays (no future peek)."""
    out = frame.copy().sort_values("date").reset_index(drop=True)
    close = out["close"]
    high = out["high"]
    low = out["low"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(14, min_periods=14).mean()
    out["atr_pct"] = out["atr14"] / close.replace(0, np.nan)
    # Past-only percentile rank of ATR% over 60 sessions.
    out["atr_pctile60"] = (
        out["atr_pct"]
        .rolling(60, min_periods=30)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )
    for n in (20, 60, 120):
        out[f"ma{n}"] = close.rolling(n, min_periods=n).mean()
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)
    # Wilder RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi14"] = 100.0 - (100.0 / (1.0 + rs))
    return out


def apply_robust_rule(frame: pd.DataFrame, rule: RobustRule) -> pd.DataFrame:
    if rule.emotion_exit_min is not None and rule.amv_exit_threshold is not None:
        exit_mode = "amv_or_emotion"
    elif rule.emotion_exit_min is not None:
        exit_mode = "emotion"
    elif rule.amv_exit_threshold is not None:
        exit_mode = "amv"
    else:
        exit_mode = "amv"  # unused; path exits may still fire

    base = CombinedRule(
        name=rule.name,
        entry_mode="amv",
        emotion_entry_max=None,
        j_entry_max=None,
        amv_entry_two_day=rule.amv_entry_two_day,
        exit_mode=exit_mode,
        amv_exit_threshold=rule.amv_exit_threshold,
        emotion_exit_min=rule.emotion_exit_min,
        exit_ignore_if_above_ma=rule.exit_ignore_if_above_ma,
        min_hold_days=rule.min_hold_days,
    )
    out = apply_combined_rule(frame, base)

    entry = out["entry_signal"].astype(bool)
    if rule.require_above_ma is not None:
        ma = out[f"ma{rule.require_above_ma}"]
        entry = entry & (out["close"] > ma)
    if rule.max_atr_pctile is not None:
        entry = entry & (out["atr_pctile60"] <= rule.max_atr_pctile)
    if rule.min_vol_ratio is not None:
        entry = entry & (out["vol_ratio"] >= rule.min_vol_ratio)
    out["entry_signal"] = entry.fillna(False)

    exit_hit = out["exit_signal"].astype(bool)
    if rule.emotion_requires_rsi and rule.emotion_exit_min is not None:
        emo = out["emotion"] >= rule.emotion_exit_min
        rsi_ok = out["rsi14"] >= (
            rule.rsi_exit_min if rule.rsi_exit_min is not None else 70.0
        )
        amv_leg = pd.Series(False, index=out.index)
        if rule.amv_exit_threshold is not None:
            amv_leg = out["amv_ret_1d"] <= rule.amv_exit_threshold
        exit_hit = amv_leg | (emo & rsi_ok)
        if rule.exit_ignore_if_above_ma is not None:
            ma = out["close"].rolling(
                rule.exit_ignore_if_above_ma,
                min_periods=rule.exit_ignore_if_above_ma,
            ).mean()
            exit_hit = exit_hit & ~(out["close"] > ma)
    elif rule.rsi_exit_min is not None:
        rsi_leg = out["rsi14"] >= rule.rsi_exit_min
        if rule.exit_ignore_if_above_ma is not None:
            ma = out["close"].rolling(
                rule.exit_ignore_if_above_ma,
                min_periods=rule.exit_ignore_if_above_ma,
            ).mean()
            rsi_leg = rsi_leg & ~(out["close"] > ma)
        exit_hit = exit_hit | rsi_leg

    out["exit_signal"] = exit_hit.fillna(False)
    out["min_hold_days"] = int(rule.min_hold_days)
    return out


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if start:
        data = data[data["date"] >= pd.Timestamp(start)]
    if end:
        data = data[data["date"] <= pd.Timestamp(end)]
    return data.reset_index(drop=True)


def eval_rule(
    frame: pd.DataFrame,
    rule: RobustRule,
    *,
    start: str | None,
    end: str | None,
    cost: float = COST_PRIMARY,
) -> dict:
    gated = apply_robust_rule(_slice(frame, start, end), rule)
    if gated.empty or (not gated["entry_signal"].any() and not gated["exit_signal"].any()):
        return {
            "rule": rule.name,
            "total_return": 0.0,
            "benchmark_total_return": float("nan"),
            "excess_return": float("nan"),
            "max_drawdown": 0.0,
            "exposure": 0.0,
            "trades": 0,
            "sharpe": 0.0,
            "skipped": True,
            "n_optional": rule.n_optional,
            **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
        }
    daily, trades, _bench, summary = run_index_backtest(
        gated,
        cost=cost,
        peak_dd_exit=rule.peak_dd_exit,
        atr_trail_mult=rule.atr_trail_mult,
    )
    excess = float(summary["total_return"]) - float(summary["benchmark_total_return"])
    return {
        "rule": rule.name,
        "total_return": float(summary["total_return"]),
        "benchmark_total_return": float(summary["benchmark_total_return"]),
        "excess_return": excess,
        "max_drawdown": float(summary["max_drawdown"]),
        "exposure": float(summary["exposure"]),
        "trades": int(summary["trades"]),
        "sharpe": float(summary["sharpe"]),
        "annualized_return": float(summary["annualized_return"]),
        "skipped": False,
        "n_optional": rule.n_optional,
        **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
    }


def baseline_robust_rules() -> list[RobustRule]:
    return [
        RobustRule(
            name="amv_min10_protect_ma20",
            amv_entry_two_day=0.04,
            amv_exit_threshold=-0.023,
            emotion_exit_min=None,
            exit_ignore_if_above_ma=20,
            min_hold_days=10,
        ),
        RobustRule(
            name="amv_emo70_ma60",
            amv_entry_two_day=0.03,
            amv_exit_threshold=None,
            emotion_exit_min=70.0,
            exit_ignore_if_above_ma=60,
            min_hold_days=0,
        ),
        RobustRule(
            name="amv_emo70_ma20_h10",
            amv_entry_two_day=0.03,
            amv_exit_threshold=None,
            emotion_exit_min=70.0,
            exit_ignore_if_above_ma=20,
            min_hold_days=10,
        ),
        RobustRule(
            name="amv_or_emo70_ma20_h10",
            amv_entry_two_day=0.03,
            amv_exit_threshold=-0.023,
            emotion_exit_min=70.0,
            exit_ignore_if_above_ma=20,
            min_hold_days=10,
        ),
    ]


def iter_hypothesis_rules() -> Iterator[RobustRule]:
    """Small hypothesis grid (~90): one overlay family at a time + a few pairs."""
    cores = [
        dict(ain=0.03, aout=-0.023, eout=70.0, ma=20, h=10),
        dict(ain=0.03, aout=-0.023, eout=70.0, ma=60, h=5),
        dict(ain=0.03, aout=None, eout=70.0, ma=20, h=10),
        dict(ain=0.03, aout=-0.035, eout=60.0, ma=60, h=0),
        dict(ain=0.04, aout=-0.023, eout=70.0, ma=20, h=10),
    ]
    addons: list[dict] = [
        {},
        {"above_ma": 60},
        {"above_ma": 20},
        {"max_atr_pctile": 0.85},
        {"max_atr_pctile": 0.90},
        {"min_vol_ratio": 1.0},
        {"min_vol_ratio": 1.2},
        {"rsi_exit": 75.0},
        {"rsi_exit": 80.0},
        {"emotion_requires_rsi": True, "rsi_exit": 70.0},
        {"peak_dd": 0.08},
        {"peak_dd": 0.10},
        {"atr_trail": 2.5},
        {"atr_trail": 3.0},
        {"above_ma": 60, "max_atr_pctile": 0.90},
        {"above_ma": 60, "peak_dd": 0.08},
        {"above_ma": 60, "rsi_exit": 75.0},
        {"max_atr_pctile": 0.85, "peak_dd": 0.08},
        {"min_vol_ratio": 1.0, "above_ma": 60},
        {"emotion_requires_rsi": True, "rsi_exit": 65.0, "above_ma": 60},
    ]

    seen: set[str] = set()
    for core, addon in product(cores, addons):
        # Skip incompatible: emotion_requires_rsi needs emotion exit.
        if addon.get("emotion_requires_rsi") and core["eout"] is None:
            continue
        name = (
            f"c{core['ain']}|a{core['aout']}|e{core['eout']}|ma{core['ma']}|h{core['h']}"
            f"|ab{addon.get('above_ma')}|atrp{addon.get('max_atr_pctile')}"
            f"|vr{addon.get('min_vol_ratio')}|rsi{addon.get('rsi_exit')}"
            f"|ers{addon.get('emotion_requires_rsi')}|dd{addon.get('peak_dd')}"
            f"|atr{addon.get('atr_trail')}"
        )
        if name in seen:
            continue
        seen.add(name)
        yield RobustRule(
            name=name,
            amv_entry_two_day=core["ain"],
            amv_exit_threshold=core["aout"],
            emotion_exit_min=core["eout"],
            exit_ignore_if_above_ma=core["ma"],
            min_hold_days=core["h"],
            require_above_ma=addon.get("above_ma"),
            max_atr_pctile=addon.get("max_atr_pctile"),
            min_vol_ratio=addon.get("min_vol_ratio"),
            rsi_exit_min=addon.get("rsi_exit"),
            emotion_requires_rsi=bool(addon.get("emotion_requires_rsi", False)),
            peak_dd_exit=addon.get("peak_dd"),
            atr_trail_mult=addon.get("atr_trail"),
        )


def score_walk_forward(fold_rows: list[dict], *, n_optional: int) -> dict:
    """Stability-first score; lower complexity preferred when close."""
    valid = [r for r in fold_rows if not r.get("skipped") and pd.notna(r.get("excess_return"))]
    if len(valid) < 3:
        return {
            "score": -1e9,
            "median_excess": float("nan"),
            "positive_fold_share": 0.0,
            "min_excess": float("nan"),
            "mean_trades": 0.0,
            "pass_stability": False,
        }
    excesses = np.array([float(r["excess_return"]) for r in valid], dtype=float)
    trades = np.array([int(r["trades"]) for r in valid], dtype=float)
    pos_share = float((excesses > 0).mean())
    median_ex = float(np.median(excesses))
    min_ex = float(np.min(excesses))
    mean_trades = float(np.mean(trades))
    # Prefer enough activity but penalize hyperactive churn.
    trade_pen = 0.0
    if mean_trades < 2:
        trade_pen += 0.15
    elif mean_trades > 20:
        trade_pen += 0.02 * (mean_trades - 20)
    complexity_pen = 0.03 * n_optional
    pass_stab = pos_share >= 0.75 and min_ex > -0.15
    score = median_ex - complexity_pen - trade_pen
    if not pass_stab:
        score -= 0.25
    return {
        "score": score,
        "median_excess": median_ex,
        "positive_fold_share": pos_share,
        "min_excess": min_ex,
        "mean_trades": mean_trades,
        "pass_stability": pass_stab,
    }


def build_research_frame(*, force_download: bool = False) -> tuple[pd.DataFrame, str, str]:
    frame, asset_name, symbol = build_combined_frame(
        prefer_etf=True, start="2020-01-01", force_download=force_download
    )
    return enrich_features(frame), asset_name, symbol
