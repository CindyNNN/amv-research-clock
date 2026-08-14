"""Combine market emotion + KDJ + 0AMV gates for ChiNext research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Iterator

import pandas as pd

from ai_invest_advisor.amv_index_data import (
    DEFAULT_AMV_PATH,
    IndexSpec,
    align_index_with_amv,
    build_amv_signals,
    download_index_daily,
    load_amv_daily,
)
from ai_invest_advisor.cyb_market_data import add_indicators

ROOT = Path(__file__).resolve().parents[2]
EMOTION_COMBINED = (
    ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "all_a_breadth_combined.csv"
)
INDEX_CACHE = ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "cyb_399006_daily.csv"
ETF_SPEC = IndexSpec("159915", "创业板ETF", "sz159915")
INDEX_SPEC = IndexSpec("399006", "创业板指", "sz399006")


@dataclass(frozen=True)
class CombinedRule:
    name: str
    # entry
    entry_mode: str  # emotion | amv | and | or
    emotion_entry_max: float | None = 15.0
    j_entry_max: float | None = 30.0
    amv_entry_two_day: float | None = 0.04
    # exit
    exit_mode: str = "amv"  # amv | emotion | kdj | amv_or_emotion | amv_or_kdj | any
    amv_exit_threshold: float | None = -0.023
    emotion_exit_min: float | None = None
    # filters
    exit_ignore_if_above_ma: int | None = None
    min_hold_days: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def load_emotion_frame(path: Path | str = EMOTION_COMBINED) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["emotion"] = pd.to_numeric(frame["emotion"], errors="coerce")
    return frame.dropna(subset=["emotion"]).sort_values("date").drop_duplicates("date")


def load_cyb_price_frame(
    *,
    prefer_etf: bool = True,
    start: str = "2020-01-01",
    force_download: bool = False,
) -> tuple[pd.DataFrame, str, str]:
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp("today").date()
    cache = ROOT / "data" / "backtests" / "amv_index_gate"
    if prefer_etf:
        try:
            px = download_index_daily(
                ETF_SPEC,
                start=start_d,
                end=end_d,
                cache_dir=cache,
                force=force_download,
            )
            return px, "创业板ETF(159915)", ETF_SPEC.tencent_symbol
        except Exception:
            pass
    if INDEX_CACHE.exists() and not force_download:
        px = pd.read_csv(INDEX_CACHE, parse_dates=["date"])
        px["date"] = pd.to_datetime(px["date"]).dt.normalize()
        px = px[px["date"] >= pd.Timestamp(start)].reset_index(drop=True)
        return px, "创业板指(399006)", INDEX_SPEC.tencent_symbol
    px = download_index_daily(
        INDEX_SPEC, start=start_d, end=end_d, cache_dir=cache, force=force_download
    )
    return px, "创业板指(399006)", INDEX_SPEC.tencent_symbol


def build_combined_frame(
    *,
    prefer_etf: bool = True,
    start: str = "2020-01-01",
    force_download: bool = False,
) -> tuple[pd.DataFrame, str, str]:
    price, asset_name, symbol = load_cyb_price_frame(
        prefer_etf=prefer_etf, start=start, force_download=force_download
    )
    emotion = load_emotion_frame()
    amv = build_amv_signals(load_amv_daily(DEFAULT_AMV_PATH))
    spec = ETF_SPEC if "159915" in symbol else INDEX_SPEC
    aligned = align_index_with_amv(price, amv, spec=spec)
    # align_index_with_amv already set baseline entry/exit; keep AMV features.
    frame = aligned.merge(emotion[["date", "emotion"]], on="date", how="inner")
    frame = add_indicators(frame)
    frame = frame.sort_values("date").reset_index(drop=True)
    return frame, asset_name, symbol


def apply_combined_rule(frame: pd.DataFrame, rule: CombinedRule) -> pd.DataFrame:
    out = frame.copy()
    emo = out["emotion"]
    j = out["j"] if "j" in out.columns else pd.Series(False, index=out.index)
    dead = out["kdj_dead_cross"] if "kdj_dead_cross" in out.columns else False

    emo_entry = pd.Series(True, index=out.index)
    if rule.emotion_entry_max is not None:
        emo_entry &= emo < rule.emotion_entry_max
    if rule.j_entry_max is not None:
        emo_entry &= j < rule.j_entry_max

    amv_entry = pd.Series(True, index=out.index)
    if rule.amv_entry_two_day is not None:
        amv_entry &= out["amv_ret_2d_sum"] > rule.amv_entry_two_day

    if rule.entry_mode == "emotion":
        entry = emo_entry
    elif rule.entry_mode == "amv":
        entry = amv_entry
    elif rule.entry_mode == "and":
        entry = emo_entry & amv_entry
    elif rule.entry_mode == "or":
        entry = emo_entry | amv_entry
    else:
        raise ValueError(f"unknown entry_mode: {rule.entry_mode}")

    legs = []
    if rule.exit_mode in {"amv", "amv_or_emotion", "amv_or_kdj", "any"}:
        if rule.amv_exit_threshold is not None:
            legs.append(out["amv_ret_1d"] <= rule.amv_exit_threshold)
    if rule.exit_mode in {"emotion", "amv_or_emotion", "any"}:
        if rule.emotion_exit_min is not None:
            legs.append(emo >= rule.emotion_exit_min)
    if rule.exit_mode in {"kdj", "amv_or_kdj", "any"}:
        legs.append(dead.astype(bool))

    if not legs:
        exit_hit = pd.Series(False, index=out.index)
    elif rule.exit_mode in {"amv", "emotion", "kdj"}:
        exit_hit = legs[0]
    else:
        exit_hit = legs[0]
        for leg in legs[1:]:
            exit_hit = exit_hit | leg

    if rule.exit_ignore_if_above_ma is not None:
        ma = out["close"].rolling(rule.exit_ignore_if_above_ma, min_periods=rule.exit_ignore_if_above_ma).mean()
        exit_hit = exit_hit & ~(out["close"] > ma)

    out["entry_signal"] = entry.fillna(False)
    out["exit_signal"] = exit_hit.fillna(False)
    out["min_hold_days"] = int(rule.min_hold_days)
    out["rule_name"] = rule.name
    return out


def baseline_rules() -> list[CombinedRule]:
    return [
        CombinedRule(
            name="emotion_kdj_classic",
            entry_mode="emotion",
            emotion_entry_max=15.0,
            j_entry_max=30.0,
            amv_entry_two_day=None,
            exit_mode="kdj",
            amv_exit_threshold=None,
            emotion_exit_min=None,
        ),
        CombinedRule(
            name="amv_baseline",
            entry_mode="amv",
            emotion_entry_max=None,
            j_entry_max=None,
            amv_entry_two_day=0.04,
            exit_mode="amv",
            amv_exit_threshold=-0.023,
        ),
        CombinedRule(
            name="amv_min10_protect_ma20",
            entry_mode="amv",
            emotion_entry_max=None,
            j_entry_max=None,
            amv_entry_two_day=0.04,
            exit_mode="amv",
            amv_exit_threshold=-0.023,
            exit_ignore_if_above_ma=20,
            min_hold_days=10,
        ),
        CombinedRule(
            name="emotion_and_amv_strict",
            entry_mode="and",
            emotion_entry_max=15.0,
            j_entry_max=30.0,
            amv_entry_two_day=0.04,
            exit_mode="amv_or_kdj",
            amv_exit_threshold=-0.023,
            exit_ignore_if_above_ma=20,
            min_hold_days=10,
        ),
    ]


def iter_grid_rules() -> Iterator[CombinedRule]:
    """Structured large grid (~a few thousand) for emotion×0AMV research."""
    entry_modes = ("emotion", "amv", "and", "or")
    emotion_entry_maxs = (12.0, 15.0, 18.0, 20.0, 25.0)
    j_entry_maxs = (30.0, None)
    amv_entry_ths = (0.03, 0.04, 0.05)
    exit_modes = ("amv", "kdj", "amv_or_kdj", "amv_or_emotion", "emotion")
    amv_exit_ths = (-0.023, -0.03, -0.035)
    emotion_exit_mins = (50.0, 60.0, 70.0)
    protect_mas = (None, 20, 60)
    min_holds = (0, 5, 10)

    for entry_mode, exit_mode, protect, min_hold in product(
        entry_modes, exit_modes, protect_mas, min_holds
    ):
        e_vals = emotion_entry_maxs if entry_mode != "amv" else (None,)
        j_vals = j_entry_maxs if entry_mode != "amv" else (None,)
        a_in_vals = amv_entry_ths if entry_mode != "emotion" else (None,)

        if exit_mode in {"amv", "amv_or_kdj", "amv_or_emotion"}:
            a_out_vals = amv_exit_ths
        else:
            a_out_vals = (None,)

        if exit_mode in {"emotion", "amv_or_emotion"}:
            e_out_vals = emotion_exit_mins
        else:
            e_out_vals = (None,)

        if exit_mode == "kdj":
            a_out_vals = (None,)
            e_out_vals = (None,)

        for e_in, j_in, a_in, a_out, e_out in product(
            e_vals, j_vals, a_in_vals, a_out_vals, e_out_vals
        ):
            name = (
                f"{entry_mode}|e{e_in}|j{j_in}|ain{a_in}|"
                f"{exit_mode}|aout{a_out}|eout{e_out}|ma{protect}|h{min_hold}"
            )
            yield CombinedRule(
                name=name,
                entry_mode=entry_mode,
                emotion_entry_max=e_in,
                j_entry_max=j_in,
                amv_entry_two_day=a_in,
                exit_mode=exit_mode,
                amv_exit_threshold=a_out,
                emotion_exit_min=e_out,
                exit_ignore_if_above_ma=protect,
                min_hold_days=min_hold,
            )
