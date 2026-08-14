from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from ai_invest_advisor.compass_manual_input import (
    ManualAmvInput,
    apply_manual_bar,
    close_from_ret_pct,
    export_manual_amv_bundle,
    prompt_and_export_amv,
    prompt_manual_amv_dialog,
    _parse_ret_pct,
)


def test_parse_ret_pct() -> None:
    assert _parse_ret_pct("1.25") == 1.25
    assert _parse_ret_pct("+1.25%") == 1.25
    assert _parse_ret_pct("-2.3") == -2.3
    assert _parse_ret_pct("99") is None
    assert _parse_ret_pct("") is None


def test_close_from_ret_pct() -> None:
    assert abs(close_from_ret_pct(200000.0, 1.0) - 202000.0) < 1e-6
    assert abs(close_from_ret_pct(200000.0, -2.5) - 195000.0) < 1e-6


def test_apply_manual_bar_replaces_same_day() -> None:
    base = pd.DataFrame(
        {
            "date": [date(2026, 8, 4), date(2026, 8, 5)],
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [0.0, 0.0],
            "amount": [0.0, 0.0],
        }
    )
    manual = ManualAmvInput(
        as_of=date(2026, 8, 5),
        close=210000.0,
        open=209000.0,
        high=211000.0,
        low=208000.0,
        ret_pct=5.0,
    )
    out = apply_manual_bar(base, manual)
    assert len(out) == 2
    assert out.iloc[-1]["close"] == 210000.0
    assert out.iloc[-1]["source"] == "manual_user_input"


def test_export_manual_bundle(tmp_path: Path) -> None:
    hist = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-07-01", periods=25).date,
            "open": range(100, 125),
            "high": range(101, 126),
            "low": range(99, 124),
            "close": range(100, 125),
            "volume": [1.0] * 25,
            "amount": [1.0] * 25,
        }
    )
    hist.to_csv(tmp_path / "0amv_daily.csv", index=False, encoding="utf-8-sig")
    manual = ManualAmvInput(
        as_of=date(2026, 8, 6),
        close=130.0,
        open=130.0,
        high=130.0,
        low=130.0,
        ret_pct=4.0,
    )
    _d, _i, snap, daily_path, ind_path, snap_path = export_manual_amv_bundle(
        manual, out_dir=tmp_path, start="2026-07-01"
    )
    assert daily_path.exists()
    assert ind_path.exists()
    assert snap_path.exists()
    assert snap["as_of"] == "2026-08-06"
    assert snap["source"] == "manual_user_input"
    assert abs(float(snap["close"]) - 130.0) < 1e-9


def test_prompt_uses_close_env(monkeypatch) -> None:
    monkeypatch.delenv("CYB_AMV_MANUAL_RET_PCT", raising=False)
    monkeypatch.setenv("CYB_AMV_MANUAL_CLOSE", "205432.5")
    monkeypatch.setenv("CYB_AMV_MANUAL_DATE", "2026-08-06")
    got = prompt_manual_amv_dialog(expected_as_of=date(2026, 8, 6), previous_close=200000.0)
    assert got is not None
    assert got.close == 205432.5
    assert got.as_of == date(2026, 8, 6)
    assert got.ret_pct is not None
    assert abs(got.ret_pct - 2.71625) < 1e-6


def test_prompt_uses_ret_pct_env_fallback(monkeypatch) -> None:
    monkeypatch.delenv("CYB_AMV_MANUAL_CLOSE", raising=False)
    monkeypatch.setenv("CYB_AMV_MANUAL_RET_PCT", "2.5")
    got = prompt_manual_amv_dialog(
        expected_as_of=date(2026, 8, 6), previous_close=200000.0
    )
    assert got is not None
    assert abs(got.close - 205000.0) < 1e-6
    assert got.ret_pct == 2.5


def test_prompt_and_export_via_close_env(monkeypatch, tmp_path: Path) -> None:
    hist = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-07-01", periods=25).date,
            "open": range(100, 125),
            "high": range(101, 126),
            "low": range(99, 124),
            "close": range(100, 125),
            "volume": [1.0] * 25,
            "amount": [1.0] * 25,
        }
    )
    hist.to_csv(tmp_path / "0amv_daily.csv", index=False, encoding="utf-8-sig")
    prev = float(hist["close"].iloc[-1])
    monkeypatch.delenv("CYB_AMV_MANUAL_RET_PCT", raising=False)
    monkeypatch.setenv("CYB_AMV_MANUAL_CLOSE", str(prev * 1.01))
    snap, *_paths = prompt_and_export_amv(
        expected_as_of=date(2026, 8, 6),
        out_dir=tmp_path,
        start="2026-07-01",
    )
    assert snap["as_of"] == "2026-08-06"
    assert abs(float(snap["close"]) - prev * 1.01) < 1e-6
    assert abs(float(snap["ret_1d"]) - 0.01) < 1e-9
