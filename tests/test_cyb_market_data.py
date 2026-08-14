from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ai_invest_advisor.cyb_market_data import (
    MarketDataPaths,
    MarketDataError,
    add_indicators,
    build_index_url,
    load_complete_history,
    parse_breadth_payload,
    parse_index_payload,
    select_completed_index_rows,
    select_index_rows,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_index_url_uses_requested_end_date():
    url = build_index_url(date(2026, 7, 17))

    assert "2026-07-17" in url
    assert "sz399006" in url
    assert ",2000,qfq" in url


def test_empty_list_data_is_reported_as_market_data_error():
    with pytest.raises(MarketDataError, match="接口返回异常"):
        parse_index_payload({"code": 0, "data": []})


def test_parse_index_payload_returns_numeric_unique_rows():
    payload = {
        "code": 0,
        "data": {
            "sz399006": {
                "day": [
                    ["2026-07-16", "100", "101", "102", "99", "1000"],
                    ["2026-07-17", "101", "103", "104", "100", "1200"],
                ]
            }
        },
    }

    result = parse_index_payload(payload)

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-07-16",
        "2026-07-17",
    ]
    assert result["close"].tolist() == [101.0, 103.0]
    assert result["pct_chg"].iloc[-1] == pytest.approx(1.980198)


def test_breadth_uses_quoted_universe_denominator():
    distribution = [0] * 63
    distribution[0] = 90
    distribution[31] = 10
    distribution[32] = 900

    row = parse_breadth_payload(
        date(2026, 7, 17),
        {
            "status_code": 0,
            "result": {
                "distribution": distribution,
                "limit_up": 5,
                "limit_down": 2,
                "last_update_time": "2026-07-17 15:10:00",
            },
        },
    )

    assert row["emotion"] == 9.0
    assert row["quoted_total"] == 1000
    assert row["advancers"] == 90
    assert row["decliners"] == 900


@pytest.fixture
def sample_ohlc_breadth():
    dates = pd.bdate_range("2026-06-15", periods=25)
    close = pd.Series(range(100, 125), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "close": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "pct_chg": close.pct_change().fillna(0) * 100,
            "emotion": [30.0] * 25,
            "advancers": [1500] * 25,
            "unchanged": [100] * 25,
            "decliners": [3400] * 25,
            "source_timestamp": [
                f"{day:%Y-%m-%d} 15:10:00" for day in dates
            ],
        }
    )


def test_add_indicators_matches_expected_columns(sample_ohlc_breadth):
    result = add_indicators(sample_ohlc_breadth)

    assert {"ma20", "k", "d", "j", "kdj_dead_cross"} <= set(result.columns)
    assert result["ma20"].iloc[-1] == pytest.approx(
        result["close"].tail(20).mean()
    )


def test_before_1515_excludes_current_calendar_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )

    result = select_completed_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 30, tzinfo=SHANGHAI),
    )

    assert result["date"].max().date() == date(2026, 7, 16)


def test_intraday_mode_keeps_current_dynamic_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )

    result = select_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        mode="intraday",
    )

    assert result["date"].max().date() == date(2026, 7, 17)


def test_close_mode_before_1515_excludes_current_dynamic_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )

    result = select_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        mode="close",
    )

    assert result["date"].max().date() == date(2026, 7, 16)


def test_after_1515_keeps_current_calendar_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )

    result = select_completed_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 15, 20, tzinfo=SHANGHAI),
    )

    assert result["date"].max().date() == date(2026, 7, 17)


def test_invalid_breadth_distribution_is_rejected():
    with pytest.raises(MarketDataError):
        parse_breadth_payload(
            date(2026, 7, 17),
            {"status_code": 0, "result": {"distribution": [1, 2]}},
        )


def test_intraday_history_uses_live_row_without_writing_formal_caches(tmp_path):
    dates = pd.bdate_range(end="2026-07-17", periods=80)
    index_rows = []
    for offset, day in enumerate(dates):
        close = 100.0 + offset
        index_rows.append(
            [
                day.strftime("%Y-%m-%d"),
                str(close - 0.5),
                str(close),
                str(close + 1.0),
                str(close - 1.0),
                "1000",
            ]
        )
    index_payload = {
        "code": 0,
        "data": {"sz399006": {"day": index_rows}},
    }

    distribution = [0] * 63
    distribution[0] = 100
    distribution[31] = 10
    distribution[32] = 890

    def fake_fetch_json(url):
        if "fqkline" in url:
            return index_payload
        requested = url.rsplit("=", 1)[-1]
        return {
            "status_code": 0,
            "result": {
                "distribution": distribution,
                "last_update_time": f"{requested} 14:40:00",
            },
        }

    paths = MarketDataPaths(
        index_cache=tmp_path / "index.csv",
        breadth_cache=tmp_path / "breadth.csv",
        legacy_cache=tmp_path / "legacy.csv",
    )
    cached = pd.DataFrame(
        {
            "date": dates[:-1],
            "advancers": 100,
            "unchanged": 10,
            "decliners": 890,
            "quoted_total": 1000,
            "emotion": 10.0,
            "limit_up": 0,
            "limit_down": 0,
            "last_update_time": "2026-07-16 15:10:00",
            "source": "test",
        }
    )
    cached.to_csv(paths.breadth_cache, index=False)
    original_breadth = paths.breadth_cache.read_bytes()
    pd.DataFrame(
        {
            "date": ["2021-12-31"],
            "advancers": [100],
            "unchanged": [10],
            "decliners": [890],
            "emotion": [10.0],
        }
    ).to_csv(paths.legacy_cache, index=False)
    amv_path = tmp_path / "0amv.csv"
    pd.DataFrame(
        {"date": dates, "close": [1000.0 + i for i in range(len(dates))]}
    ).to_csv(amv_path, index=False)

    result = load_complete_history(
        paths=paths,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        fetch_json=fake_fetch_json,
        mode="intraday",
        amv_path=amv_path,
    )

    assert result["date"].max().date() == date(2026, 7, 17)
    assert "amv_ret_2d_sum" in result.columns
    assert pd.notna(result.iloc[-1]["ma60"])
    assert not paths.index_cache.exists()
    assert paths.breadth_cache.read_bytes() == original_breadth


def test_close_history_persists_completed_index_and_breadth(tmp_path):
    dates = pd.bdate_range(end="2026-07-17", periods=80)
    index_payload = {
        "code": 0,
        "data": {
            "sz399006": {
                "day": [
                    [
                        day.strftime("%Y-%m-%d"),
                        str(99.5 + offset),
                        str(100.0 + offset),
                        str(101.0 + offset),
                        str(99.0 + offset),
                        "1000",
                    ]
                    for offset, day in enumerate(dates)
                ]
            }
        },
    }
    distribution = [0] * 63
    distribution[0] = 100
    distribution[31] = 10
    distribution[32] = 890

    def fake_fetch_json(url):
        if "fqkline" in url:
            return index_payload
        return {
            "status_code": 0,
            "result": {
                "distribution": distribution,
                "last_update_time": "2026-07-17 15:10:00",
            },
        }

    paths = MarketDataPaths(
        index_cache=tmp_path / "index.csv",
        breadth_cache=tmp_path / "breadth.csv",
        legacy_cache=tmp_path / "legacy.csv",
    )
    pd.DataFrame(
        columns=[
            "date",
            "advancers",
            "unchanged",
            "decliners",
            "quoted_total",
            "emotion",
            "limit_up",
            "limit_down",
            "last_update_time",
            "source",
        ]
    ).to_csv(paths.breadth_cache, index=False)
    pd.DataFrame(
        {
            "date": ["2021-12-31"],
            "advancers": [100],
            "unchanged": [10],
            "decliners": [890],
            "emotion": [10.0],
        }
    ).to_csv(paths.legacy_cache, index=False)
    amv_path = tmp_path / "0amv.csv"
    pd.DataFrame(
        {"date": dates, "close": [1000.0 + i for i in range(len(dates))]}
    ).to_csv(amv_path, index=False)

    result = load_complete_history(
        paths=paths,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 15, 20, tzinfo=SHANGHAI),
        fetch_json=fake_fetch_json,
        mode="close",
        amv_path=amv_path,
    )

    assert result["date"].max().date() == date(2026, 7, 17)
    assert paths.index_cache.exists()
    saved_breadth = pd.read_csv(paths.breadth_cache)
    assert "2026-07-17" in set(saved_breadth["date"].astype(str))
    saved_combined = pd.read_csv(tmp_path / "all_a_breadth_combined.csv")
    assert "emotion" in saved_combined.columns
    assert "2026-07-17" in set(pd.to_datetime(saved_combined["date"]).dt.strftime("%Y-%m-%d"))
