"""Download index OHLC and align with Compass 0AMV gate signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ai_invest_advisor.http_fetch import HttpFetchError, curl_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = ROOT / "data" / "backtests" / "amv_index_gate"
DEFAULT_AMV_PATH = ROOT / "data" / "compass" / "0amv_daily.csv"

EXIT_THRESHOLD = -0.023
ENTRY_TWO_DAY_SUM = 0.04

FetchJson = Callable[[str], dict[str, Any]]


class AmvIndexDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexSpec:
    code: str
    name: str
    tencent_symbol: str


INDEX_UNIVERSE: tuple[IndexSpec, ...] = (
    IndexSpec("000001", "上证指数", "sh000001"),
    IndexSpec("000300", "沪深300", "sh000300"),
    IndexSpec("399006", "创业板指", "sz399006"),
    IndexSpec("000688", "科创50", "sh000688"),
    IndexSpec("000852", "中证1000", "sh000852"),
)


def _curl_json(url: str) -> dict[str, Any]:
    try:
        return curl_json(url)
    except HttpFetchError as exc:
        raise AmvIndexDataError(str(exc)) from exc


def build_tencent_index_url(
    symbol: str,
    *,
    start: date,
    end: date,
    limit: int = 2000,
) -> str:
    return (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,{start.isoformat()},{end.isoformat()},{limit},qfq"
    )


def parse_tencent_index_payload(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    data = payload.get("data")
    block = data.get(symbol, {}) if isinstance(data, dict) else None
    rows = None
    if isinstance(block, dict):
        rows = block.get("day") or block.get("qfqday")
    if payload.get("code") != 0 or not isinstance(rows, list) or not rows:
        raise AmvIndexDataError(
            f"{symbol} 腾讯接口返回异常: code={payload.get('code')}"
        )
    try:
        frame = pd.DataFrame(
            [row[:6] for row in rows],
            columns=["date", "open", "close", "high", "low", "volume"],
        )
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
        for column in ("open", "close", "high", "low", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except (TypeError, ValueError) as exc:
        raise AmvIndexDataError(f"{symbol} 行情字段无法解析") from exc
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    if (
        (frame["high"] < frame[["open", "close"]].max(axis=1)).any()
        or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
    ):
        raise AmvIndexDataError(f"{symbol} OHLC 关系无效")
    if (frame[["open", "close", "high", "low"]] <= 0).any().any():
        raise AmvIndexDataError(f"{symbol} 含非正价格")
    return frame.reset_index(drop=True)


def download_index_daily(
    spec: IndexSpec,
    *,
    start: date,
    end: date,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    fetch_json: FetchJson = _curl_json,
    force: bool = False,
) -> pd.DataFrame:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"index_{spec.tencent_symbol}_daily.csv"
    if path.exists() and not force:
        frame = pd.read_csv(path, parse_dates=["date"])
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        return frame.sort_values("date").reset_index(drop=True)

    # Tencent day bars are returned in yearly slices. Years before an ETF
    # listing come back empty; skip those rather than aborting the download.
    chunks: list[pd.DataFrame] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year, 12, 31), end)
        url = build_tencent_index_url(spec.tencent_symbol, start=cursor, end=chunk_end)
        payload = fetch_json(url)
        try:
            chunks.append(parse_tencent_index_payload(payload, spec.tencent_symbol))
        except AmvIndexDataError:
            pass
        cursor = date(chunk_end.year + 1, 1, 1)

    if not chunks:
        raise AmvIndexDataError(f"{spec.tencent_symbol} 无数据")
    frame = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
    frame = frame.reset_index(drop=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return frame


def load_amv_daily(path: Path | str = DEFAULT_AMV_PATH) -> pd.DataFrame:
    amv_path = Path(path)
    if not amv_path.exists():
        raise AmvIndexDataError(f"找不到 0AMV 日线: {amv_path}")
    frame = pd.read_csv(amv_path)
    required = {"date", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise AmvIndexDataError(f"0AMV CSV 缺列: {sorted(missing)}")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["close"].isna().any():
        raise AmvIndexDataError("0AMV close 含无效值")
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame.reset_index(drop=True)


def build_amv_signals(amv: pd.DataFrame) -> pd.DataFrame:
    """Compute 0AMV gate signals on the AMV calendar (before index join)."""
    frame = amv[["date", "close"]].copy()
    frame = frame.rename(columns={"close": "amv_close"})
    frame["amv_ret_1d"] = frame["amv_close"].pct_change()
    frame["amv_ret_2d_sum"] = frame["amv_ret_1d"] + frame["amv_ret_1d"].shift(1)
    frame["exit_signal"] = frame["amv_ret_1d"] <= EXIT_THRESHOLD
    frame["entry_signal"] = frame["amv_ret_2d_sum"] > ENTRY_TWO_DAY_SUM
    return frame


def align_index_with_amv(
    index: pd.DataFrame,
    amv_signals: pd.DataFrame,
    *,
    spec: IndexSpec,
) -> pd.DataFrame:
    left = index.copy()
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    right = amv_signals.copy()
    right["date"] = pd.to_datetime(right["date"]).dt.normalize()
    merged = left.merge(right, on="date", how="inner")
    if merged.empty:
        raise AmvIndexDataError(f"{spec.tencent_symbol} 与 0AMV 无共同交易日")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["code"] = spec.code
    merged["name"] = spec.name
    merged["tencent_symbol"] = spec.tencent_symbol
    # Recompute signals after join so NaNs at edges stay consistent.
    merged["exit_signal"] = merged["amv_ret_1d"] <= EXIT_THRESHOLD
    merged["entry_signal"] = merged["amv_ret_2d_sum"] > ENTRY_TWO_DAY_SUM
    return merged


def prepare_research_frames(
    *,
    start: date | str = "2019-01-01",
    end: date | str | None = None,
    amv_path: Path | str = DEFAULT_AMV_PATH,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    fetch_json: FetchJson = _curl_json,
    force_download: bool = False,
) -> dict[str, pd.DataFrame]:
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date() if end else datetime.now().date()
    amv = load_amv_daily(amv_path)
    signals = build_amv_signals(amv)
    out: dict[str, pd.DataFrame] = {}
    for spec in INDEX_UNIVERSE:
        index = download_index_daily(
            spec,
            start=start_d,
            end=end_d,
            cache_dir=cache_dir,
            fetch_json=fetch_json,
            force=force_download,
        )
        out[spec.tencent_symbol] = align_index_with_amv(index, signals, spec=spec)
    return out
