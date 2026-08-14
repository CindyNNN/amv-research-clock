import pandas as pd

from ai_invest_advisor.dashboard.chart_views import (
    add_moving_averages,
    build_intraday_reference,
    prepare_ohlc_view,
    prepare_sentiment_daily_view,
)


def test_prepare_ohlc_view_daily_keeps_rows():
    history = pd.DataFrame(
        {
            "日期": pd.date_range("2026-06-01", periods=5, freq="D"),
            "开盘价": [10, 11, 12, 13, 14],
            "最高价": [12, 13, 14, 15, 16],
            "最低价": [9, 10, 11, 12, 13],
            "收盘价": [11, 12, 13, 14, 15],
            "成交额": [100, 110, 120, 130, 140],
        }
    )

    result = prepare_ohlc_view(history, "日线")

    assert len(result) == 5
    assert result.iloc[-1]["close"] == 15
    assert list(result["date_label"]) == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]


def test_prepare_ohlc_view_weekly_resamples_ohlc():
    history = pd.DataFrame(
        {
            "日期": pd.date_range("2026-06-01", periods=10, freq="D"),
            "开盘价": list(range(10, 20)),
            "最高价": list(range(12, 22)),
            "最低价": list(range(8, 18)),
            "收盘价": list(range(11, 21)),
            "成交额": [100] * 10,
        }
    )

    result = prepare_ohlc_view(history, "周线")

    assert len(result) == 2
    assert result.iloc[0]["open"] == 10
    assert result.iloc[0]["high"] == 16
    assert result.iloc[0]["low"] == 8
    assert result.iloc[0]["close"] == 15
    assert result.iloc[0]["amount"] == 500
    assert list(result["date_label"]) == ["2026-06-05", "2026-06-12"]


def test_prepare_ohlc_view_uses_continuous_trading_labels_without_weekend_gap():
    history = pd.DataFrame(
        {
            "日期": ["2026-06-05", "2026-06-08", "2026-06-09"],
            "开盘价": [10, 11, 12],
            "最高价": [11, 12, 13],
            "最低价": [9, 10, 11],
            "收盘价": [10.5, 11.5, 12.5],
            "成交额": [100, 110, 120],
        }
    )

    result = prepare_ohlc_view(history, "日线")

    assert list(result["date_label"]) == ["2026-06-05", "2026-06-08", "2026-06-09"]
    assert "2026-06-06" not in set(result["date_label"])
    assert "2026-06-07" not in set(result["date_label"])


def test_add_moving_averages_uses_close_prices():
    frame = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6]})

    result = add_moving_averages(frame, [3, 5])

    assert pd.isna(result.loc[1, "ma3"])
    assert result.loc[2, "ma3"] == 2
    assert result.loc[4, "ma5"] == 3
    assert result.loc[5, "ma5"] == 4


def test_prepare_sentiment_daily_view_keeps_latest_snapshot_per_trade_date():
    sentiment = pd.DataFrame(
        [
            {
                "date": "2026-06-15",
                "generated_at": "2026-06-16 14:35:01",
                "market_heat": 68.2,
                "label": "中性震荡",
            },
            {
                "date": "2026-06-15",
                "generated_at": "2026-06-17 00:03:34",
                "market_heat": 60.54,
                "label": "中性震荡",
            },
            {
                "date": "2026-06-16",
                "generated_at": "2026-06-17 15:31:00",
                "market_heat": 72.0,
                "label": "偏强",
            },
        ]
    )

    result = prepare_sentiment_daily_view(sentiment)

    assert list(result["date_label"]) == ["2026-06-15", "2026-06-16"]
    assert list(result["market_heat"]) == [60.54, 72.0]
    assert list(result["generated_at"]) == ["2026-06-17 00:03:34", "2026-06-17 15:31:00"]


def test_build_intraday_reference_uses_info_points():
    info = {
        "今开": "100",
        "最高": "108",
        "最低": "96",
        "昨收": "98",
    }
    history = pd.DataFrame({"收盘价": [98, 101], "日期": ["2026-06-13", "2026-06-16"]})

    result = build_intraday_reference(history, info)

    assert list(result.columns) == ["time", "price", "reference"]
    assert result.iloc[0]["price"] == 100
    assert result.iloc[-1]["price"] == 101
    assert result["reference"].all()
