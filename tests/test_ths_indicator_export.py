from pathlib import Path

import pandas as pd

from ai_invest_advisor.ths_indicator_export import (
    build_subchart_frame,
    write_subchart_csv_atomic,
)


def sample_history() -> pd.DataFrame:
    common = {
        "pct_chg": 0.0,
        "advancers": 100,
        "unchanged": 20,
        "decliners": 900,
        "source_timestamp": "test",
        "k": 50.0,
        "d": 50.0,
        "j": 50.0,
        "ma20": 100.0,
        "kdj_dead_cross": False,
    }
    return pd.DataFrame(
        [
            {
                **common,
                "date": "2026-07-15",
                "close": 100.0,
                "emotion": 40.0,
                "ma60": 110.0,
                "amv_ret_1d": 0.02,
                "amv_ret_2d_sum": 0.05,
            },
            {
                **common,
                "date": "2026-07-16",
                "close": 102.0,
                "emotion": 40.0,
                "ma60": 110.0,
                "amv_ret_1d": 0.01,
                "amv_ret_2d_sum": 0.02,
            },
            {
                **common,
                "date": "2026-07-17",
                "close": 100.0,
                "emotion": 65.0,
                "ma60": 110.0,
                "amv_ret_1d": -0.01,
                "amv_ret_2d_sum": 0.0,
            },
        ]
    )


def test_build_subchart_frame_reuses_state_machine():
    result = build_subchart_frame(sample_history())

    assert result.columns.tolist() == [
        "date",
        "close",
        "emotion",
        "j",
        "signal",
        "holding",
    ]
    assert result["date"].tolist() == [
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    assert result["signal"].tolist() == ["BUY", "HOLD", "SELL"]
    assert result["holding"].tolist() == [1, 1, 0]


def test_write_subchart_csv_atomic(tmp_path: Path):
    path = tmp_path / "subchart.csv"

    write_subchart_csv_atomic(build_subchart_frame(sample_history()), path)

    assert path.read_text(encoding="utf-8").splitlines()[0] == (
        "date,close,emotion,j,signal,holding"
    )
    assert not path.with_suffix(".csv.tmp").exists()
