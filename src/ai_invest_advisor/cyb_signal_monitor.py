"""ChiNext daily signal state machine: AMV entry + emotion/AMV exit + MA60 protect.

Live rule (walk-forward score winner from cyb_robust_optimize):
- Entry (flat): 0AMV two-day return sum > 3%
- Exit (long): (0AMV daily ret <= -3.5% OR emotion >= 60) AND close NOT above MA60
- Close confirm; next-open execution is the investor's responsibility
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd


Signal = Literal["BUY", "SELL", "HOLD", "FLAT"]

# Locked live thresholds (得分冠军)
AMV_ENTRY_TWO_DAY = 0.03
AMV_EXIT_DAILY = -0.035
EMOTION_EXIT_MIN = 60.0
STRATEGY_LABEL = "AMV入场+情绪/急跌离场+MA60保护"
STRATEGY_CODE = "amv_emo60_aout35_ma60"


class StateFileError(ValueError):
    pass


@dataclass(frozen=True)
class ModelState:
    holding: bool = False
    entry_signal_date: str | None = None
    entry_signal_close: float | None = None
    peak_close: float | None = None
    peak_close_date: str | None = None
    processed_date: str | None = None
    last_signal: Signal = "FLAT"
    last_reasons: tuple[str, ...] = ()
    strategy_code: str = STRATEGY_CODE

    @classmethod
    def flat(cls) -> "ModelState":
        return cls()


@dataclass(frozen=True)
class MarketSnapshot:
    date: date
    close: float
    pct_change: float
    emotion: float
    advancers: int
    unchanged: int
    decliners: int
    k: float
    d: float
    j: float
    ma20: float
    ma60: float
    kdj_dead_cross: bool
    amv_ret_1d: float
    amv_ret_2d_sum: float
    source_timestamp: str


@dataclass(frozen=True)
class SignalDecision:
    signal: Signal
    reasons: tuple[str, ...]
    repeated_check: bool
    trailing_line: float | None
    peak_drawdown: float | None
    snapshot: MarketSnapshot
    state_before: ModelState
    state_after: ModelState
    suppressed_exits: tuple[str, ...] = ()


def _decision(
    *,
    signal: Signal,
    reasons: tuple[str, ...],
    repeated_check: bool,
    trailing_line: float | None,
    peak_drawdown: float | None,
    snapshot: MarketSnapshot,
    before: ModelState,
    after: ModelState,
    suppressed_exits: tuple[str, ...] = (),
) -> tuple[ModelState, SignalDecision]:
    return after, SignalDecision(
        signal=signal,
        reasons=reasons,
        repeated_check=repeated_check,
        trailing_line=trailing_line,
        peak_drawdown=peak_drawdown,
        snapshot=snapshot,
        state_before=before,
        state_after=after,
        suppressed_exits=suppressed_exits,
    )


def entry_hit(snapshot: MarketSnapshot) -> bool:
    return snapshot.amv_ret_2d_sum > AMV_ENTRY_TWO_DAY


def raw_exit_reasons(snapshot: MarketSnapshot) -> list[str]:
    reasons: list[str] = []
    if snapshot.amv_ret_1d <= AMV_EXIT_DAILY:
        reasons.append("AMV_DAILY_RET_LE_MINUS_3_5PCT")
    if snapshot.emotion >= EMOTION_EXIT_MIN:
        reasons.append("EMOTION_GE_60")
    return reasons


def above_ma60_protect(snapshot: MarketSnapshot) -> bool:
    return snapshot.close > snapshot.ma60


def evaluate_snapshot(
    state: ModelState,
    snapshot: MarketSnapshot,
) -> tuple[ModelState, SignalDecision]:
    day = snapshot.date.isoformat()
    if state.processed_date == day:
        return _decision(
            signal=state.last_signal,
            reasons=state.last_reasons,
            repeated_check=True,
            trailing_line=(
                state.peak_close * 0.92
                if state.holding and state.peak_close is not None
                else None
            ),
            peak_drawdown=(
                snapshot.close / state.peak_close - 1.0
                if state.holding and state.peak_close
                else None
            ),
            snapshot=snapshot,
            before=state,
            after=state,
        )
    if state.processed_date and day < state.processed_date:
        raise ValueError(
            f"指标日期 {day} 早于已处理日期 {state.processed_date}"
        )

    if not state.holding:
        if entry_hit(snapshot):
            reasons = ("AMV_TWO_DAY_SUM_GT_3PCT",)
            after = ModelState(
                holding=True,
                entry_signal_date=day,
                entry_signal_close=snapshot.close,
                peak_close=snapshot.close,
                peak_close_date=day,
                processed_date=day,
                last_signal="BUY",
                last_reasons=reasons,
                strategy_code=STRATEGY_CODE,
            )
            return _decision(
                signal="BUY",
                reasons=reasons,
                repeated_check=False,
                trailing_line=snapshot.close * 0.92,
                peak_drawdown=0.0,
                snapshot=snapshot,
                before=state,
                after=after,
            )
        after = replace(
            state,
            processed_date=day,
            last_signal="FLAT",
            last_reasons=(),
            strategy_code=STRATEGY_CODE,
        )
        return _decision(
            signal="FLAT",
            reasons=(),
            repeated_check=False,
            trailing_line=None,
            peak_drawdown=None,
            snapshot=snapshot,
            before=state,
            after=after,
        )

    if state.peak_close is None:
        raise ValueError("持仓状态缺少最高收盘")
    peak_close = max(state.peak_close, snapshot.close)
    peak_date = day if snapshot.close > state.peak_close else state.peak_close_date
    trailing_line = peak_close * 0.92
    peak_drawdown = snapshot.close / peak_close - 1.0
    exits = raw_exit_reasons(snapshot)
    protected = above_ma60_protect(snapshot)

    if exits and not protected:
        reason_tuple = tuple(exits)
        after = ModelState(
            processed_date=day,
            last_signal="SELL",
            last_reasons=reason_tuple,
            strategy_code=STRATEGY_CODE,
        )
        return _decision(
            signal="SELL",
            reasons=reason_tuple,
            repeated_check=False,
            trailing_line=trailing_line,
            peak_drawdown=peak_drawdown,
            snapshot=snapshot,
            before=state,
            after=after,
        )

    after = replace(
        state,
        peak_close=peak_close,
        peak_close_date=peak_date,
        processed_date=day,
        last_signal="HOLD",
        last_reasons=(),
        strategy_code=STRATEGY_CODE,
    )
    return _decision(
        signal="HOLD",
        reasons=(),
        repeated_check=False,
        trailing_line=trailing_line,
        peak_drawdown=peak_drawdown,
        snapshot=snapshot,
        before=state,
        after=after,
        suppressed_exits=tuple(exits) if exits and protected else (),
    )


def snapshot_from_row(row: pd.Series) -> MarketSnapshot:
    timestamp = pd.Timestamp(row["date"])
    return MarketSnapshot(
        date=timestamp.date(),
        close=float(row["close"]),
        pct_change=float(row.get("pct_chg", row.get("pct_change", 0.0))),
        emotion=float(row["emotion"]),
        advancers=int(row["advancers"]),
        unchanged=int(row["unchanged"]),
        decliners=int(row["decliners"]),
        k=float(row["k"]),
        d=float(row["d"]),
        j=float(row["j"]),
        ma20=float(row["ma20"]),
        ma60=float(row["ma60"]),
        kdj_dead_cross=bool(row["kdj_dead_cross"]),
        amv_ret_1d=float(row["amv_ret_1d"]),
        amv_ret_2d_sum=float(row["amv_ret_2d_sum"]),
        source_timestamp=str(row.get("source_timestamp", timestamp.date())),
    )


def replay_history(
    frame: pd.DataFrame,
) -> tuple[ModelState, SignalDecision]:
    required = {
        "date",
        "close",
        "emotion",
        "advancers",
        "unchanged",
        "decliners",
        "k",
        "d",
        "j",
        "ma20",
        "ma60",
        "kdj_dead_cross",
        "amv_ret_1d",
        "amv_ret_2d_sum",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"历史数据缺少字段: {sorted(missing)}")
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"])
    if data["date"].duplicated().any():
        raise ValueError("历史数据存在重复日期")
    # Skip rows without AMV/MA60 (warm-up / calendar gaps)
    data = data.dropna(subset=["amv_ret_1d", "amv_ret_2d_sum", "ma60"])
    data = data.sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError("历史数据为空（或缺少可用的0AMV/MA60）")

    state = ModelState.flat()
    decision: SignalDecision | None = None
    for _, row in data.iterrows():
        state, decision = evaluate_snapshot(state, snapshot_from_row(row))
    assert decision is not None
    return state, decision


def save_state_atomic(path: Path, state: ModelState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_state(path: Path) -> ModelState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("状态根节点不是对象")
        if not isinstance(payload.get("holding"), bool):
            raise TypeError("holding 不是布尔值")
        last_signal = payload.get("last_signal", "FLAT")
        if last_signal not in {"BUY", "SELL", "HOLD", "FLAT"}:
            raise TypeError("last_signal 无效")
        reasons = payload.get("last_reasons", [])
        if not isinstance(reasons, list) or not all(
            isinstance(value, str) for value in reasons
        ):
            raise TypeError("last_reasons 无效")
        numeric_fields = ("entry_signal_close", "peak_close")
        for field in numeric_fields:
            value = payload.get(field)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise TypeError(f"{field} 无效")
        strategy_code = payload.get("strategy_code")
        if strategy_code != STRATEGY_CODE:
            raise TypeError(
                f"状态文件策略码为 {strategy_code!r}，当前策略为 {STRATEGY_CODE!r}"
            )
        state = ModelState(
            holding=payload["holding"],
            entry_signal_date=payload.get("entry_signal_date"),
            entry_signal_close=payload.get("entry_signal_close"),
            peak_close=payload.get("peak_close"),
            peak_close_date=payload.get("peak_close_date"),
            processed_date=payload.get("processed_date"),
            last_signal=last_signal,
            last_reasons=tuple(reasons),
            strategy_code=strategy_code,
        )
        if state.holding and (
            state.entry_signal_date is None
            or state.entry_signal_close is None
            or state.peak_close is None
        ):
            raise TypeError("持仓状态字段不完整")
        return state
    except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise StateFileError(f"状态文件无效: {path}") from exc
