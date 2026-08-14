from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from ai_invest_advisor.compass_autoupdate import (
    compute_0amv_indicators,
    expected_as_of_date,
    export_0amv_bundle,
    last_weekday_on_or_before,
    latest_indicator_snapshot,
)


def test_expected_as_of_before_close_uses_previous_weekday() -> None:
    # Friday 10:00 -> Thursday
    got = expected_as_of_date(datetime(2026, 7, 24, 10, 0, 0))
    assert got == date(2026, 7, 23)
    # Friday 16:00 -> Friday
    got2 = expected_as_of_date(datetime(2026, 7, 24, 16, 0, 0))
    assert got2 == date(2026, 7, 24)
    # Monday 10:00 -> previous Friday
    got3 = expected_as_of_date(datetime(2026, 7, 20, 10, 0, 0))
    assert got3 == date(2026, 7, 17)


def test_last_weekday_on_or_before() -> None:
    assert last_weekday_on_or_before(date(2026, 7, 19)) == date(2026, 7, 17)  # Sun->Fri


def test_compute_0amv_indicators_and_snapshot() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=30, freq="B").date,
            "open": range(100, 130),
            "high": range(101, 131),
            "low": range(99, 129),
            "close": range(100, 130),
            "volume": [1.0] * 30,
            "amount": [1.0] * 30,
            "symbol": ["0AMV"] * 30,
        }
    )
    indicators = compute_0amv_indicators(daily)
    assert "ma5" in indicators.columns
    assert "ma5_regime" in indicators.columns
    snap = latest_indicator_snapshot(indicators)
    assert snap["symbol"] == "0AMV"
    assert snap["close"] == 129


def test_close_compass_returns_true_when_not_running(monkeypatch) -> None:
    from ai_invest_advisor import compass_autoupdate as mod

    monkeypatch.setattr(mod, "is_compass_running", lambda exe_name="WavMain.exe": False)
    assert mod.close_compass() is True


def test_close_compass_kills_running_process(monkeypatch) -> None:
    from ai_invest_advisor import compass_autoupdate as mod

    calls: list[list[str]] = []
    states = iter([True, True, False])

    monkeypatch.setattr(
        mod,
        "is_compass_running",
        lambda exe_name="WavMain.exe": next(states, False),
    )

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    assert mod.close_compass() is True
    assert calls and calls[0][:3] == ["taskkill", "/IM", "WavMain.exe"]
    assert "/F" in calls[0]


def test_export_bundle_with_real_cache_if_present(tmp_path: Path) -> None:
    root = Path(r"C:\Softwares\compass")
    day = root / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"
    if not day.exists():
        pytest.skip("本机无指南针缓存")
    _d, ind, snap, daily_path, ind_path, snap_path = export_0amv_bundle(
        compass_root=root,
        out_dir=tmp_path,
        start="2026-01-01",
    )
    assert daily_path.exists()
    assert ind_path.exists()
    assert snap_path.exists()
    assert len(ind) > 0
    assert snap["close"] > 0
