import pandas as pd

from scripts.explore_cyb_emotion_strategies import Strategy, backtest


def test_backtest_handles_a_period_with_no_trades():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [100.0, 101.0],
            "close": [101.0, 100.0],
        }
    )
    strategy = Strategy(
        name="never_trade",
        label="never trade",
        family="test",
        entry=lambda row: False,
        exit=lambda row, state: False,
    )

    daily, trades, summary = backtest(
        frame,
        strategy,
        "2024-01-01",
        "2024-12-31",
    )

    assert trades.empty
    assert summary["closed_trades"] == 0
    assert summary["total_return"] == 0.0
    assert daily["equity"].tolist() == [1.0, 1.0]
