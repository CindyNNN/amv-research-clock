from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from ai_invest_advisor.http_fetch import HttpFetchError, curl_json


ROOT = Path(__file__).resolve().parents[2]
SHANGHAI_CLOSE_BUFFER = clock_time(15, 15)
BREADTH_URL = (
    "https://dq.10jqka.com.cn/fuyao/ext_quote_uplimit_down/"
    "extquote_updown/v1/distribution?date={date}"
)
FetchJson = Callable[[str], dict[str, Any]]
RunMode = Literal["intraday", "close"]


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketDataPaths:
    index_cache: Path = (
        ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "cyb_399006_daily.csv"
    )
    breadth_cache: Path = (
        ROOT
        / "data"
        / "backtests"
        / "cyb_emotion_kdj"
        / "all_a_breadth_ths_2022_present.csv"
    )
    legacy_cache: Path = (
        ROOT
        / "data"
        / "backtests"
        / "cyb_emotion_kdj"
        / "all_a_breadth_legacy_2020_2022.csv"
    )


def build_index_url(as_of: date) -> str:
    return (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param=sz399006,day,2019-01-01,{as_of.isoformat()},2000,qfq"
    )


def parse_index_payload(payload: dict[str, Any]) -> pd.DataFrame:
    data = payload.get("data")
    rows = (
        data.get("sz399006", {}).get("day")
        if isinstance(data, dict)
        else None
    )
    if payload.get("code") != 0 or not isinstance(rows, list) or not rows:
        raise MarketDataError(
            f"创业板指接口返回异常: code={payload.get('code')}"
        )
    try:
        frame = pd.DataFrame(
            [row[:6] for row in rows],
            columns=["date", "open", "close", "high", "low", "volume"],
        )
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        for column in ("open", "close", "high", "low", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except (ValueError, TypeError) as exc:
        raise MarketDataError("创业板指行情字段无法解析") from exc
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    if (
        (frame["high"] < frame[["open", "close"]].max(axis=1)).any()
        or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
    ):
        raise MarketDataError("创业板指 OHLC 关系无效")
    frame["pct_chg"] = frame["close"].pct_change() * 100.0
    return frame.reset_index(drop=True)


def parse_breadth_payload(day: date, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status_code") != 0:
        raise MarketDataError(f"{day} 同花顺涨跌分布接口返回异常")
    result = payload.get("result") or {}
    distribution = result.get("distribution")
    if not isinstance(distribution, list) or len(distribution) != 63:
        raise MarketDataError(f"{day} 同花顺涨跌分布长度不是63")
    try:
        values = [int(value) for value in distribution]
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"{day} 同花顺涨跌分布包含无效值") from exc
    advancers = sum(values[:31])
    unchanged = values[31]
    decliners = sum(values[32:])
    quoted_total = advancers + unchanged + decliners
    if quoted_total <= 0:
        raise MarketDataError(f"{day} 有效报价股票总数为0")
    return {
        "date": pd.Timestamp(day),
        "advancers": advancers,
        "unchanged": unchanged,
        "decliners": decliners,
        "quoted_total": quoted_total,
        "emotion": advancers / quoted_total * 100.0,
        "limit_up": int(result.get("limit_up", 0)),
        "limit_down": int(result.get("limit_down", 0)),
        "last_update_time": str(result.get("last_update_time") or ""),
        "source": "同花顺涨跌分布",
    }


def select_index_rows(
    frame: pd.DataFrame,
    *,
    as_of: date,
    now: datetime,
    mode: RunMode,
) -> pd.DataFrame:
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported run mode: {mode}")
    result = frame.loc[pd.to_datetime(frame["date"]).dt.date <= as_of].copy()
    if (
        mode == "close"
        and as_of == now.date()
        and now.timetz().replace(tzinfo=None) < SHANGHAI_CLOSE_BUFFER
    ):
        result = result.loc[pd.to_datetime(result["date"]).dt.date < as_of]
    if result.empty:
        raise MarketDataError("指定日期之前没有完整创业板指日线")
    return result.reset_index(drop=True)


def select_completed_index_rows(
    frame: pd.DataFrame,
    *,
    as_of: date,
    now: datetime,
) -> pd.DataFrame:
    return select_index_rows(
        frame,
        as_of=as_of,
        now=now,
        mode="close",
    )


def chinese_sma(
    values: pd.Series,
    n: int,
    initial: float = 50.0,
) -> pd.Series:
    previous = float(initial)
    output: list[float] = []
    for value in values.to_numpy(dtype=float):
        if not math.isnan(value):
            previous = ((n - 1) * previous + value) / n
        output.append(previous)
    return pd.Series(output, index=values.index, dtype=float)


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values("date").reset_index(drop=True)
    result["ma20"] = result["close"].rolling(20, min_periods=20).mean()
    lowest = result["low"].rolling(9, min_periods=9).min()
    highest = result["high"].rolling(9, min_periods=9).max()
    spread = highest - lowest
    result["rsv"] = (
        100.0
        * (result["close"] - lowest)
        / spread.replace(0, np.nan)
    )
    result["k"] = chinese_sma(result["rsv"], 3)
    result["d"] = chinese_sma(result["k"], 3)
    result["j"] = 3.0 * result["k"] - 2.0 * result["d"]
    result["kdj_dead_cross"] = (
        (result["k"] < result["d"])
        & (result["k"].shift(1) >= result["d"].shift(1))
    )
    return result


def _curl_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return curl_json(url, timeout=30, attempts=1)
        except HttpFetchError as exc:
            last_error = exc
            time.sleep(attempt)
    raise MarketDataError(f"网络请求连续3次失败: {url}") from last_error


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def _fetch_missing_breadth(
    dates: list[pd.Timestamp],
    *,
    fetch_json: FetchJson,
    workers: int,
) -> pd.DataFrame:
    if not dates:
        return pd.DataFrame()

    def fetch(day: pd.Timestamp) -> dict[str, Any]:
        payload = fetch_json(
            BREADTH_URL.format(date=day.strftime("%Y%m%d"))
        )
        return parse_breadth_payload(day.date(), payload)

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch, day): day for day in dates}
        for future in as_completed(futures):
            records.append(future.result())
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def load_complete_history(
    *,
    paths: MarketDataPaths | None = None,
    as_of: date,
    now: datetime,
    fetch_json: FetchJson = _curl_json,
    workers: int = 4,
    mode: RunMode = "close",
    amv_path: Path | None = None,
) -> pd.DataFrame:
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported run mode: {mode}")
    paths = paths or MarketDataPaths()
    index = parse_index_payload(fetch_json(build_index_url(as_of)))
    index = select_index_rows(
        index,
        as_of=as_of,
        now=now,
        mode=mode,
    )
    if mode == "close":
        _write_csv_atomic(index, paths.index_cache)

    if paths.breadth_cache.exists():
        breadth = pd.read_csv(paths.breadth_cache, parse_dates=["date"])
    else:
        breadth = pd.DataFrame()
    cached_dates = (
        set(pd.to_datetime(breadth["date"]))
        if not breadth.empty
        else set()
    )
    post_2022_dates = index.loc[
        index["date"] >= pd.Timestamp("2022-01-01"), "date"
    ].tolist()
    missing = [day for day in post_2022_dates if day not in cached_dates]
    downloaded = _fetch_missing_breadth(
        missing,
        fetch_json=fetch_json,
        workers=workers,
    )
    if not downloaded.empty:
        breadth = (
            downloaded.copy()
            if breadth.empty
            else pd.concat([breadth, downloaded], ignore_index=True)
        )
        breadth["date"] = pd.to_datetime(breadth["date"])
        breadth = (
            breadth.sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        if mode == "close":
            _write_csv_atomic(breadth, paths.breadth_cache)

    if not paths.legacy_cache.exists():
        raise MarketDataError(
            f"缺少2020–2021历史情绪缓存: {paths.legacy_cache}"
        )
    legacy = pd.read_csv(paths.legacy_cache, parse_dates=["date"])
    legacy = legacy.loc[legacy["date"] < pd.Timestamp("2022-01-01")]
    combined = pd.concat([legacy, breadth], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (
        combined.sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    if "last_update_time" not in combined:
        combined["last_update_time"] = ""
    combined["source_timestamp"] = combined["last_update_time"].fillna("")
    empty_timestamp = combined["source_timestamp"].astype(str).str.len() == 0
    combined.loc[empty_timestamp, "source_timestamp"] = (
        combined.loc[empty_timestamp, "date"].dt.strftime("%Y-%m-%d")
        + " historical-close"
    )
    if "quoted_total" not in combined.columns:
        combined["quoted_total"] = (
            combined["advancers"] + combined["unchanged"] + combined["decliners"]
        )
    if mode == "close":
        combined_path = paths.breadth_cache.parent / "all_a_breadth_combined.csv"
        export_cols = [
            column
            for column in (
                "date",
                "advancers",
                "unchanged",
                "decliners",
                "quoted_total",
                "emotion",
                "source",
                "limit_up",
                "limit_down",
                "last_update_time",
            )
            if column in combined.columns
        ]
        _write_csv_atomic(combined[export_cols], combined_path)

    index = add_indicators(index)
    index = index.loc[index["date"] >= pd.Timestamp("2020-01-01")]
    breadth_columns = [
        "date",
        "emotion",
        "advancers",
        "unchanged",
        "decliners",
        "source_timestamp",
    ]
    merged = index.merge(
        combined[breadth_columns],
        on="date",
        how="left",
        validate="one_to_one",
    )
    latest = merged.iloc[-1]
    required = [
        "close",
        "emotion",
        "advancers",
        "unchanged",
        "decliners",
        "ma20",
        "k",
        "d",
        "j",
    ]
    if latest[required].isna().any():
        missing_fields = latest[required].index[latest[required].isna()].tolist()
        raise MarketDataError(
            f"最新交易日 {latest['date']:%Y-%m-%d} 数据不完整: {missing_fields}"
        )
    if not merged["date"].is_unique:
        raise MarketDataError("合并数据存在重复日期")
    valid_emotion = merged["emotion"].dropna().between(0.0, 100.0)
    if not valid_emotion.all():
        raise MarketDataError("市场情绪超出0–100范围")
    return enrich_with_amv_strategy_fields(
        merged.reset_index(drop=True),
        amv_path=amv_path,
    )


def enrich_with_amv_strategy_fields(
    frame: pd.DataFrame,
    *,
    amv_path: Path | None = None,
) -> pd.DataFrame:
    """Attach MA60 and Compass 0AMV returns required by the live gate strategy."""
    from ai_invest_advisor.amv_index_data import (
        DEFAULT_AMV_PATH,
        AmvIndexDataError,
        build_amv_signals,
        load_amv_daily,
    )

    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["ma60"] = out["close"].rolling(60, min_periods=60).mean()
    path = Path(amv_path) if amv_path is not None else DEFAULT_AMV_PATH
    try:
        amv = build_amv_signals(load_amv_daily(path))
    except (OSError, ValueError, AmvIndexDataError) as exc:
        raise MarketDataError(f"无法加载指南针0AMV日线（{path}）：{exc}") from exc
    amv = amv[["date", "amv_close", "amv_ret_1d", "amv_ret_2d_sum"]].copy()
    amv["date"] = pd.to_datetime(amv["date"]).dt.normalize()
    out = out.merge(amv, on="date", how="left")
    if out.empty:
        raise MarketDataError("合并0AMV后数据为空")
    latest = out.iloc[-1]
    missing = [
        name
        for name in ("amv_ret_1d", "amv_ret_2d_sum", "ma60")
        if pd.isna(latest[name])
    ]
    if missing:
        raise MarketDataError(
            f"最新交易日 {latest['date']:%Y-%m-%d} 缺少 {missing}；"
            "请先同步指南针0AMV缓存（scripts/sync_compass_0amv.py）后再发信号邮件。"
        )
    return out.reset_index(drop=True)
