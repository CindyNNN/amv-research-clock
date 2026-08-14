"""Read Compass (指南针) local cache for proprietary index 0AMV (活跃市值).

This does not call the live Compass process or any private network API.
It parses on-disk day.vdat / min15 files written by the desktop client.

Data source: local Compass install cache
Risk: values are proprietary Compass constructs; stale until the client syncs.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

SYMBOL_UI = "0AMV"
SYMBOL_FILE = "Z_SK0AMV"
DEFAULT_COMPASS_ROOT = Path(r"C:\Softwares\compass")
DAY_BLOCK_NAME = SYMBOL_FILE.encode("ascii")
DAY_RECORD_SIZE = 28
DAY_NAME_WIDTH = 32
DAY_BARS_PER_BLOCK = 250
MIN15_MAGIC = b"SKXM"
MIN15_HEADER_SIZE = 16


class CompassBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompassPaths:
    root: Path = DEFAULT_COMPASS_ROOT

    @property
    def day_vdat(self) -> Path:
        return self.root / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"

    @property
    def min15(self) -> Path:
        return (
            self.root
            / "ANALYSE"
            / "Data"
            / "ChinaStk"
            / "Min1Data"
            / f"{SYMBOL_FILE}.min15"
        )


def _yyyymmdd_to_date(value: int) -> date:
    year, rem = divmod(value, 10000)
    month, day = divmod(rem, 100)
    return date(year, month, day)


def _parse_day_record(raw: bytes) -> tuple[date, float, float, float, float, float, float] | None:
    if len(raw) < DAY_RECORD_SIZE:
        return None
    ymd, open_, high, low, close, volume, amount = struct.unpack_from("<I6f", raw, 0)
    if not (19900101 <= ymd <= 20991231):
        return None
    if not (0.0 < open_ < 1e7 and 0.0 < high < 1e7 and 0.0 < low < 1e7 and 0.0 < close < 1e7):
        return None
    try:
        dt = _yyyymmdd_to_date(ymd)
    except ValueError:
        return None
    return dt, open_, high, low, close, volume, amount


def read_0amv_daily(
    *,
    compass_root: Path | str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
) -> pd.DataFrame:
    """Load 活跃市值 (0AMV) daily OHLCV from Compass day.vdat cache."""
    paths = CompassPaths(root=Path(compass_root) if compass_root else DEFAULT_COMPASS_ROOT)
    path = paths.day_vdat
    if not path.exists():
        raise CompassBridgeError(f"找不到日线缓存: {path}（请先打开指南针并浏览 0AMV）")

    blob = path.read_bytes()
    positions = [
        match.start()
        for match in re.finditer(re.escape(DAY_BLOCK_NAME + b"\x00"), blob)
        if match.start() > 1_000_000
    ]
    if not positions:
        raise CompassBridgeError(f"{path} 中未找到 {SYMBOL_FILE} 日线块")

    rows: list[dict[str, object]] = []
    for pos in positions:
        offset = pos + DAY_NAME_WIDTH
        for _ in range(DAY_BARS_PER_BLOCK):
            parsed = _parse_day_record(blob[offset : offset + DAY_RECORD_SIZE])
            if parsed is None:
                break
            dt, open_, high, low, close, volume, amount = parsed
            rows.append(
                {
                    "date": dt,
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                    "amount": float(amount),
                }
            )
            offset += DAY_RECORD_SIZE

    if not rows:
        raise CompassBridgeError(f"解析 {path} 失败：无有效 0AMV 日线")

    frame = pd.DataFrame(rows).drop_duplicates(subset=["date"], keep="last")
    frame = frame.sort_values("date").reset_index(drop=True)
    frame["symbol"] = SYMBOL_UI
    frame["symbol_file"] = SYMBOL_FILE
    frame["source"] = "compass_local_day_vdat"
    frame["source_path"] = str(path)
    frame["file_mtime"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat(
        timespec="seconds"
    )

    start_d = pd.to_datetime(start).date() if start else None
    end_d = pd.to_datetime(end).date() if end else None
    if start_d is not None:
        frame = frame[frame["date"] >= start_d]
    if end_d is not None:
        frame = frame[frame["date"] <= end_d]
    return frame.reset_index(drop=True)


def read_0amv_min15(
    *,
    compass_root: Path | str | None = None,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> pd.DataFrame:
    """Load 活跃市值 (0AMV) 15-minute bars from Compass .min15 cache."""
    paths = CompassPaths(root=Path(compass_root) if compass_root else DEFAULT_COMPASS_ROOT)
    path = paths.min15
    if not path.exists():
        raise CompassBridgeError(f"找不到15分钟缓存: {path}（请先打开指南针并浏览 0AMV）")

    blob = path.read_bytes()
    if len(blob) < MIN15_HEADER_SIZE or blob[:4] != MIN15_MAGIC:
        raise CompassBridgeError(f"无法识别 min15 文件头: {path}")

    _magic, recsize, count, start_yyyymmdd = struct.unpack_from("<4sIII", blob, 0)
    if recsize != DAY_RECORD_SIZE:
        raise CompassBridgeError(f"意外的 min15 记录长度: {recsize}")
    expected = MIN15_HEADER_SIZE + count * recsize
    if len(blob) < expected:
        raise CompassBridgeError(f"min15 文件长度不足: need {expected}, got {len(blob)}")

    first_ts = struct.unpack_from("<I", blob, MIN15_HEADER_SIZE)[0]
    # Absolute Compass minute counters overflow datetime epochs; anchor relatively.
    # Empirically first bar is 09:45; lunch/overnight gaps (+105/+1125) match A-share sessions.
    start_day = _yyyymmdd_to_date(start_yyyymmdd)
    first_bar = datetime(start_day.year, start_day.month, start_day.day, 9, 45)

    rows: list[dict[str, object]] = []
    for index in range(count):
        offset = MIN15_HEADER_SIZE + index * recsize
        ts, open_, high, low, close, volume, amount = struct.unpack_from("<I6f", blob, offset)
        bar_time = first_bar + timedelta(minutes=(ts - first_ts))
        rows.append(
            {
                "datetime": bar_time,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
                "amount": float(amount),
                "ts_raw": int(ts),
            }
        )

    frame = pd.DataFrame(rows)
    frame["symbol"] = SYMBOL_UI
    frame["symbol_file"] = SYMBOL_FILE
    frame["source"] = "compass_local_min15"
    frame["source_path"] = str(path)
    frame["file_mtime"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat(
        timespec="seconds"
    )
    frame["time_anchor_note"] = "relative_to_header_date_0945"

    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    if start_ts is not None:
        frame = frame[frame["datetime"] >= start_ts]
    if end_ts is not None:
        frame = frame[frame["datetime"] <= end_ts]
    return frame.reset_index(drop=True)


Freq = Literal["day", "min15"]


def fetch_0amv(
    freq: Freq = "day",
    *,
    compass_root: Path | str | None = None,
    start: str | date | datetime | None = None,
    end: str | date | datetime | None = None,
) -> pd.DataFrame:
    if freq == "day":
        return read_0amv_daily(compass_root=compass_root, start=start, end=end)
    if freq == "min15":
        return read_0amv_min15(compass_root=compass_root, start=start, end=end)
    raise CompassBridgeError(f"不支持的频率: {freq}")


def cache_status(compass_root: Path | str | None = None) -> dict[str, object]:
    paths = CompassPaths(root=Path(compass_root) if compass_root else DEFAULT_COMPASS_ROOT)
    status: dict[str, object] = {
        "compass_root": str(paths.root),
        "root_exists": paths.root.exists(),
        "day_vdat_exists": paths.day_vdat.exists(),
        "min15_exists": paths.min15.exists(),
        "symbol": SYMBOL_UI,
        "symbol_file": SYMBOL_FILE,
    }
    if paths.day_vdat.exists():
        status["day_vdat_mtime"] = datetime.fromtimestamp(
            paths.day_vdat.stat().st_mtime
        ).isoformat(timespec="seconds")
        status["day_vdat_size"] = paths.day_vdat.stat().st_size
    if paths.min15.exists():
        status["min15_mtime"] = datetime.fromtimestamp(
            paths.min15.stat().st_mtime
        ).isoformat(timespec="seconds")
        status["min15_size"] = paths.min15.stat().st_size
    return status
