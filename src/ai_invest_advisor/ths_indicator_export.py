from __future__ import annotations

from pathlib import Path

import pandas as pd

from ai_invest_advisor.cyb_signal_monitor import (
    ModelState,
    evaluate_snapshot,
    snapshot_from_row,
)


SUBCHART_COLUMNS = [
    "date",
    "close",
    "emotion",
    "j",
    "signal",
    "holding",
]


def build_subchart_frame(history: pd.DataFrame) -> pd.DataFrame:
    data = history.copy()
    data["date"] = pd.to_datetime(data["date"])
    if data.empty:
        raise ValueError("历史数据为空")
    if data["date"].duplicated().any():
        raise ValueError("历史数据存在重复日期")
    data = data.sort_values("date").reset_index(drop=True)

    state = ModelState.flat()
    records: list[dict[str, object]] = []
    for _, row in data.iterrows():
        state, decision = evaluate_snapshot(state, snapshot_from_row(row))
        records.append(
            {
                "date": decision.snapshot.date.isoformat(),
                "close": decision.snapshot.close,
                "emotion": decision.snapshot.emotion,
                "j": decision.snapshot.j,
                "signal": decision.signal,
                "holding": int(state.holding),
            }
        )
    return pd.DataFrame(records, columns=SUBCHART_COLUMNS)


def write_subchart_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    if frame.columns.tolist() != SUBCHART_COLUMNS:
        raise ValueError(f"副图字段不正确: {frame.columns.tolist()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)
