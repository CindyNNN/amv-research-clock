from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from ai_invest_advisor.amv_cloud import append_amv_close, duplicate_tail_dates, trusted_amv_last
from ai_invest_advisor.strategy_site import yearly_excess


def test_append_amv_close_and_duplicate_tail(tmp_path: Path) -> None:
    path = tmp_path / "0amv_daily.csv"
    first = append_amv_close(as_of=date(2026, 8, 12), close=210962.0, path=path)
    assert first["duplicate_close"] is False
    second = append_amv_close(as_of=date(2026, 8, 13), close=207502.5, path=path)
    assert second["duplicate_close"] is False
    third = append_amv_close(as_of=date(2026, 8, 14), close=207502.5, path=path)
    assert third["duplicate_close"] is True
    frame = pd.read_csv(path, parse_dates=["date"])
    assert trusted_amv_last(frame) == date(2026, 8, 13)
    assert duplicate_tail_dates(frame) == ["2026-08-14"]


def test_yearly_excess_ratio_method() -> None:
    strat = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-12-31", "2021-12-31"]),
            "nav": [1000.0, 1100.0, 1210.0],
        }
    )
    bench = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-12-31", "2021-12-31"]),
            "nav": [1000.0, 1000.0, 800.0],
        }
    )
    rows = {row["year"]: row for row in yearly_excess(strat, bench)}
    assert abs(rows[2021]["index_return"] - 0.10) < 1e-9
    assert abs(rows[2021]["benchmark_return"] - -0.20) < 1e-9
    assert abs(rows[2021]["excess"] - ((1.10 / 0.80) - 1.0)) < 1e-9


def test_parse_amv_issue_title_and_body():
    from ai_invest_advisor.amv_cloud import parse_amv_issue

    parsed = parse_amv_issue("0AMV 2026-08-14", "close: 207502.5\ndate: 2026-08-14\n")
    assert parsed["date"] == "2026-08-14"
    assert parsed["close"] == 207502.5
    from_title = parse_amv_issue("0AMV 2026-08-13 210000")
    assert from_title["date"] == "2026-08-13"
    assert from_title["close"] == 210000.0


def test_seed_cloud_prefers_compass_on_same_date(tmp_path: Path) -> None:
    from ai_invest_advisor.amv_cloud import seed_cloud_amv_from_local, write_cloud_amv

    cloud = tmp_path / "cloud.csv"
    local = tmp_path / "local.csv"
    write_cloud_amv(
        pd.DataFrame(
            {
                "date": ["2026-08-13", "2026-08-14"],
                "open": [207502.5, 207502.5],
                "high": [207502.5, 207502.5],
                "low": [207502.5, 207502.5],
                "close": [207502.5, 207502.5],
                "source": ["manual_user_input", "github_workflow"],
            }
        ),
        cloud,
    )
    pd.DataFrame(
        {
            "date": ["2026-08-13", "2026-08-14"],
            "open": [212962.3, 209709.8],
            "high": [216633.7, 211843.0],
            "low": [208493.2, 206864.2],
            "close": [208572.3, 207502.5],
            "volume": [1.0, 1.0],
            "amount": [1.0, 1.0],
        }
    ).to_csv(local, index=False)
    seed_cloud_amv_from_local(cloud_path=cloud, local_path=local)
    out = pd.read_csv(cloud, parse_dates=["date"])
    close_13 = float(out.loc[out["date"] == pd.Timestamp("2026-08-13"), "close"].iloc[0])
    close_14 = float(out.loc[out["date"] == pd.Timestamp("2026-08-14"), "close"].iloc[0])
    assert abs(close_13 - 208572.3) < 0.1
    assert abs(close_14 - 207502.5) < 0.1
