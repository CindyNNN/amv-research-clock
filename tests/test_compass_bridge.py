from __future__ import annotations

import struct
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ai_invest_advisor.compass_bridge import (
    CompassBridgeError,
    fetch_0amv,
    read_0amv_daily,
    read_0amv_min15,
)


def _write_min15(path: Path, start_yyyymmdd: int, bars: list[tuple[int, float, float, float, float]]) -> None:
    header = struct.pack("<4sIII", b"SKXM", 28, len(bars), start_yyyymmdd)
    body = b"".join(
        struct.pack("<I6f", ts, o, h, l, c, 1.0, 2.0) for ts, o, h, l, c in bars
    )
    path.write_bytes(header + body)


def _write_day_vdat(path: Path, bars: list[tuple[int, float, float, float, float]]) -> None:
    # pad so symbol search ignores early catalog hits
    prefix = b"\x00" * 1_000_100
    name = b"Z_SK0AMV" + b"\x00" * 24
    records = b"".join(
        struct.pack("<I6f", ymd, o, h, l, c, 10.0, 20.0) for ymd, o, h, l, c in bars
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(prefix + name + records)


def test_read_0amv_daily_from_fixture(tmp_path: Path) -> None:
    root = tmp_path / "compass"
    day = root / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"
    _write_day_vdat(
        day,
        [
            (20260102, 100.0, 110.0, 99.0, 105.0),
            (20260103, 105.0, 120.0, 104.0, 118.0),
        ],
    )
    frame = read_0amv_daily(compass_root=root)
    assert list(frame["date"]) == [date(2026, 1, 2), date(2026, 1, 3)]
    assert frame.iloc[-1]["close"] == pytest.approx(118.0)
    assert frame.iloc[0]["symbol"] == "0AMV"


def test_read_0amv_min15_relative_anchor(tmp_path: Path) -> None:
    root = tmp_path / "compass"
    path = root / "ANALYSE" / "Data" / "ChinaStk" / "Min1Data" / "Z_SK0AMV.min15"
    path.parent.mkdir(parents=True, exist_ok=True)
    # 8 morning bars then lunch gap +105 minutes to 13:00/13:15 style
    base = 1_000_000
    bars = [(base + i * 15, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(8)]
    bars.append((base + 7 * 15 + 105, 110.0, 111.0, 109.0, 110.5))
    _write_min15(path, 20250703, bars)
    frame = read_0amv_min15(compass_root=root)
    assert frame.iloc[0]["datetime"].strftime("%Y-%m-%d %H:%M") == "2025-07-03 09:45"
    assert frame.iloc[7]["datetime"].strftime("%H:%M") == "11:30"
    assert frame.iloc[8]["datetime"].strftime("%H:%M") == "13:15"


def test_fetch_0amv_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(CompassBridgeError):
        fetch_0amv("day", compass_root=tmp_path / "missing")


def test_read_real_compass_cache_if_present() -> None:
    root = Path(r"C:\Softwares\compass")
    day = root / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"
    if not day.exists():
        pytest.skip("本机未安装指南针缓存")
    frame = read_0amv_daily(compass_root=root, start="2026-01-01")
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) > 0
    assert frame["close"].iloc[-1] > 0
