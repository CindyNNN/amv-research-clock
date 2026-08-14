from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from ai_invest_advisor.dashboard.metrics import to_float


def add_moving_averages(frame: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    result = frame.copy()
    close = pd.to_numeric(result["close"], errors="coerce")
    for window in windows:
        result[f"ma{window}"] = close.rolling(window=window, min_periods=window).mean()
    return result


def _add_date_labels(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["date_label"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
    return result


def prepare_sentiment_daily_view(sentiment: pd.DataFrame) -> pd.DataFrame:
    if sentiment.empty:
        return pd.DataFrame(columns=["date", "date_label", "generated_at", "market_heat", "label"])

    frame = sentiment.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["generated_at_sort"] = pd.to_datetime(frame["generated_at"], errors="coerce")
    frame["market_heat"] = pd.to_numeric(frame["market_heat"], errors="coerce")
    frame = frame.dropna(subset=["date", "market_heat"])
    if frame.empty:
        return pd.DataFrame(columns=["date", "date_label", "generated_at", "market_heat", "label"])

    frame = frame.sort_values(["date", "generated_at_sort"]).groupby("date", as_index=False).tail(1)
    frame = frame.sort_values("date").reset_index(drop=True)
    frame["date_label"] = frame["date"].dt.strftime("%Y-%m-%d")
    return frame[["date", "date_label", "generated_at", "market_heat", "label"]]


def normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交额": "amount",
        "成交量": "volume",
    }
    frame = history.rename(columns=rename_map).copy()
    required = ["date", "open", "high", "low", "close"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"history data missing columns: {missing}")
    if "amount" not in frame.columns:
        frame["amount"] = 0.0
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close", "amount", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    return frame.sort_values("date").reset_index(drop=True)


def prepare_ohlc_view(history: pd.DataFrame, period: str) -> pd.DataFrame:
    frame = normalize_history(history)
    if period == "日线":
        return _add_date_labels(frame)
    if period != "周线":
        raise ValueError(f"unsupported OHLC period: {period}")

    weekly = (
        frame.set_index("date")
        .resample("W-FRI")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            amount=("amount", "sum"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return _add_date_labels(weekly)


def build_intraday_reference(history: pd.DataFrame, info: dict[str, Any]) -> pd.DataFrame:
    latest_close = to_float(history.iloc[-1].get("收盘价", history.iloc[-1].get("close", 0))) if not history.empty else 0.0
    open_price = to_float(info.get("今开"), latest_close)
    high = to_float(info.get("最高"), max(open_price, latest_close))
    low = to_float(info.get("最低"), min(open_price, latest_close))
    yesterday = to_float(info.get("昨收"), open_price)
    today = datetime.now().strftime("%Y-%m-%d")
    points = [
        ("09:30", open_price),
        ("10:15", max(open_price, low)),
        ("11:30", (open_price + latest_close) / 2),
        ("13:30", min(high, max(low, latest_close))),
        ("14:30", high if latest_close >= yesterday else low),
        ("15:00", latest_close),
    ]
    return pd.DataFrame(
        {
            "time": [f"{today} {time}" for time, _ in points],
            "price": [round(float(price), 3) for _, price in points],
            "reference": True,
        }
    )
