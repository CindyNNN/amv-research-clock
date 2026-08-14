"""Cloud-persisted 0AMV daily series for GitHub Actions / Pages.

Compass and TongDaXin cannot run on GitHub. The public site therefore treats
``data/amv/0amv_daily.csv`` as the source of truth. A trailing bar whose close
equals the previous close is stored but not treated as a live AMV signal.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.amv_index_data import DEFAULT_AMV_PATH, AmvIndexDataError, load_amv_daily

ROOT = Path(__file__).resolve().parents[2]
CLOUD_AMV_PATH = ROOT / "data" / "amv" / "0amv_daily.csv"
CLOUD_COLUMNS = ("date", "open", "high", "low", "close", "source")
CST = timezone(timedelta(hours=8))
DEFAULT_GITHUB_REPO = "CindyNNN/amv-research-clock"
_AMV_TITLE = re.compile(
    r"^0AMV(?:\s+(\d{4}-\d{2}-\d{2}))?(?:\s+([0-9]+(?:\.[0-9]+)?))?\s*$",
    re.IGNORECASE,
)
_CLOSE_LINE = re.compile(r"(?im)^\s*close\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_DATE_LINE = re.compile(r"(?im)^\s*date\s*[:=]\s*(\d{4}-\d{2}-\d{2})\s*$")


def github_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPO).strip() or DEFAULT_GITHUB_REPO


def pages_url(repo: str | None = None) -> str:
    owner, _, name = (repo or github_repo()).partition("/")
    if not owner or not name:
        return "https://cindynnn.github.io/amv-research-clock/"
    return f"https://{owner.lower()}.github.io/{name}/"


def parse_amv_issue(title: str, body: str | None = None) -> dict[str, Any]:
    """Parse a site-submitted GitHub issue into date + close."""
    title = (title or "").strip()
    body = body or ""
    matched = _AMV_TITLE.match(title)
    if not matched:
        raise AmvIndexDataError("Issue 标题需为：0AMV YYYY-MM-DD")
    date_s = matched.group(1)
    close_s = matched.group(2)
    date_line = _DATE_LINE.search(body)
    close_line = _CLOSE_LINE.search(body)
    if date_line:
        date_s = date_line.group(1)
    if close_line:
        close_s = close_line.group(1)
    if not date_s:
        date_s = beijing_today().isoformat()
    if not close_s:
        raise AmvIndexDataError("缺少 0AMV 收盘价 close")
    close = float(close_s)
    if close <= 0:
        raise AmvIndexDataError("0AMV close 必须为正数")
    return {"date": date_s, "close": close}


def beijing_today() -> date:
    return datetime.now(CST).date()


def sanitize_amv_frame(frame: pd.DataFrame, *, source: str = "cloud") -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=list(CLOUD_COLUMNS))
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    if "close" not in out.columns:
        raise AmvIndexDataError("0AMV CSV 缺 close 列")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if out["close"].isna().any():
        raise AmvIndexDataError("0AMV close 含无效值")
    for column in ("open", "high", "low"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").fillna(out["close"])
        else:
            out[column] = out["close"]
    if "source" not in out.columns:
        out["source"] = source
    out["source"] = out["source"].fillna(source).astype(str)
    out = out.sort_values("date").drop_duplicates("date", keep="last")
    return out.loc[:, list(CLOUD_COLUMNS)].reset_index(drop=True)


def write_cloud_amv(frame: pd.DataFrame, path: Path | str = CLOUD_AMV_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = sanitize_amv_frame(frame)
    clean["date"] = pd.to_datetime(clean["date"]).dt.strftime("%Y-%m-%d")
    clean.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def load_cloud_amv(path: Path | str = CLOUD_AMV_PATH) -> pd.DataFrame:
    amv_path = Path(path)
    if not amv_path.exists():
        raise AmvIndexDataError(f"找不到云端 0AMV 日线: {amv_path}")
    return sanitize_amv_frame(pd.read_csv(amv_path), source="cloud")


def trusted_amv_frame(amv: pd.DataFrame) -> pd.DataFrame:
    """Drop trailing bars whose close was copied from the prior day."""
    frame = sanitize_amv_frame(amv)
    while len(frame) >= 2 and float(frame["close"].iloc[-1]) == float(frame["close"].iloc[-2]):
        frame = frame.iloc[:-1]
    return frame.reset_index(drop=True)


def trusted_amv_last(amv: pd.DataFrame) -> date:
    trusted = trusted_amv_frame(amv)
    if trusted.empty:
        raise AmvIndexDataError("0AMV 序列为空")
    return pd.Timestamp(trusted["date"].iloc[-1]).date()


def duplicate_tail_dates(amv: pd.DataFrame) -> list[str]:
    frame = sanitize_amv_frame(amv)
    dropped: list[str] = []
    while len(frame) >= 2 and float(frame["close"].iloc[-1]) == float(frame["close"].iloc[-2]):
        dropped.append(str(pd.Timestamp(frame["date"].iloc[-1]).date()))
        frame = frame.iloc[:-1]
    return dropped


def append_amv_close(
    *,
    as_of: date,
    close: float,
    path: Path | str = CLOUD_AMV_PATH,
    source: str = "github_workflow",
) -> dict[str, Any]:
    if close <= 0:
        raise AmvIndexDataError("0AMV close 必须为正数")
    target = Path(path)
    if target.exists():
        frame = load_cloud_amv(target)
    else:
        frame = pd.DataFrame(columns=list(CLOUD_COLUMNS))
    as_of_ts = pd.Timestamp(as_of).normalize()
    prev = frame[frame["date"] < as_of_ts] if not frame.empty else frame
    prev_close = float(prev["close"].iloc[-1]) if not prev.empty else None
    duplicate = prev_close is not None and float(close) == prev_close
    row = {
        "date": as_of_ts,
        "open": float(close),
        "high": float(close),
        "low": float(close),
        "close": float(close),
        "source": source,
    }
    if frame.empty:
        frame = pd.DataFrame([row])
    else:
        frame = pd.concat(
            [frame[frame["date"] != as_of_ts], pd.DataFrame([row])],
            ignore_index=True,
        )
    write_cloud_amv(frame, target)
    ret_1d = None if prev_close in (None, 0) else float(close) / prev_close - 1.0
    return {
        "date": str(as_of),
        "close": float(close),
        "previous_close": prev_close,
        "ret_1d": ret_1d,
        "duplicate_close": duplicate,
        "path": str(target),
        "source": source,
        "rows": int(len(sanitize_amv_frame(frame))),
    }


def seed_cloud_amv_from_local(
    *,
    cloud_path: Path | str = CLOUD_AMV_PATH,
    local_path: Path | str = DEFAULT_AMV_PATH,
) -> Path:
    """Create or refresh the public CSV from the local Compass export if needed."""
    cloud = Path(cloud_path)
    local = Path(local_path)
    frames: list[pd.DataFrame] = []
    if cloud.exists():
        frames.append(load_cloud_amv(cloud))
    if local.exists():
        frames.append(sanitize_amv_frame(load_amv_daily(local), source="local_compass"))
    if not frames:
        raise AmvIndexDataError("既没有云端 0AMV，也没有本地 Compass 导出")
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    return write_cloud_amv(merged, cloud)
