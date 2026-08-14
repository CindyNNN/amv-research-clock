from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from ai_invest_advisor.amv_index_backtest import (
    run_index_backtest,
    simulate_amv_gate,
)
from ai_invest_advisor.amv_index_data import (
    ENTRY_TWO_DAY_SUM,
    EXIT_THRESHOLD,
    IndexSpec,
    align_index_with_amv,
    build_amv_signals,
    parse_tencent_index_payload,
)


def _dates(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def test_build_amv_signals_thresholds() -> None:
    # Construct closes so day3 ret=-2.3%, and day5+day4 sum > 4%.
    closes = [100.0]
    # day1: +1%
    closes.append(closes[-1] * 1.01)
    # day2: +1%
    closes.append(closes[-1] * 1.01)
    # day3: -2.3%
    closes.append(closes[-1] * (1.0 + EXIT_THRESHOLD))
    # day4: +2.1%
    closes.append(closes[-1] * 1.021)
    # day5: +2.0%  -> two-day sum 4.1% > 4%
    closes.append(closes[-1] * 1.02)
    amv = pd.DataFrame({"date": _dates(len(closes)), "close": closes})
    signals = build_amv_signals(amv)
    assert bool(signals.loc[3, "exit_signal"]) is True
    assert bool(signals.loc[5, "entry_signal"]) is True
    assert float(signals.loc[5, "amv_ret_2d_sum"]) > ENTRY_TWO_DAY_SUM


def test_simulate_entry_exit_next_open_and_ignore_entry_while_long() -> None:
    """Entry on two-day sum, exit on -2.3%; entry ignored while long."""
    dates = _dates(8)
    # Index flat prices for easy fills; AMV drives signals via columns.
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [10, 11, 12, 13, 14, 15, 16, 17],
            "close": [10, 11, 12, 13, 14, 15, 16, 17],
            "high": [10, 11, 12, 13, 14, 15, 16, 17],
            "low": [10, 11, 12, 13, 14, 15, 16, 17],
            "entry_signal": [False, False, True, False, True, False, False, False],
            "exit_signal": [False, False, False, False, False, True, False, False],
            "code": ["x"] * 8,
            "name": ["测试"] * 8,
            "tencent_symbol": ["test"] * 8,
        }
    )
    daily, trades = simulate_amv_gate(frame, cost=0.0)
    # Signal day index 2 -> buy open index 3 at 13
    assert daily.iloc[2]["action"] == "schedule_entry"
    assert daily.iloc[3]["action"] == "entry"
    assert daily.iloc[3]["position"] == 1
    # Entry signal on index 4 while long must not schedule another buy
    assert daily.iloc[4]["action"] == "hold"
    # Exit signal index 5 -> sell open index 6 at 16
    assert daily.iloc[5]["action"] == "schedule_exit"
    assert daily.iloc[6]["action"] == "exit"
    assert len(trades) == 1
    assert float(trades.iloc[0]["entry_price"]) == 13.0
    assert float(trades.iloc[0]["exit_price"]) == 16.0
    assert float(trades.iloc[0]["net_return"]) == pytest.approx(16 / 13 - 1)


def test_cost_applied_both_sides() -> None:
    dates = _dates(4)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [100.0, 100.0, 110.0, 110.0],
            "close": [100.0, 100.0, 110.0, 110.0],
            "entry_signal": [True, False, False, False],
            "exit_signal": [False, True, False, False],
        }
    )
    _daily, trades = simulate_amv_gate(frame, cost=0.001)
    assert len(trades) == 1
    entry = 100.0 * 1.001
    exit_ = 110.0 * 0.999
    assert float(trades.iloc[0]["entry_price"]) == pytest.approx(entry)
    assert float(trades.iloc[0]["exit_price"]) == pytest.approx(exit_)
    assert float(trades.iloc[0]["net_return"]) == pytest.approx(exit_ / entry - 1)


def test_parse_tencent_index_payload() -> None:
    payload = {
        "code": 0,
        "data": {
            "sh000001": {
                "day": [
                    ["2024-01-02", "3000", "3010", "3020", "2990", "100"],
                    ["2024-01-03", "3010", "3030", "3040", "3000", "120"],
                ]
            }
        },
    }
    frame = parse_tencent_index_payload(payload, "sh000001")
    assert list(frame["close"]) == [3010.0, 3030.0]


def test_align_and_run_smoke() -> None:
    amv = pd.DataFrame(
        {
            "date": _dates(6),
            "close": [100, 103, 106.5, 104, 108, 105],
        }
    )
    signals = build_amv_signals(amv)
    index = pd.DataFrame(
        {
            "date": _dates(6),
            "open": [10, 10.2, 10.4, 10.1, 10.5, 10.3],
            "close": [10.1, 10.3, 10.5, 10.2, 10.6, 10.4],
            "high": [10.2, 10.4, 10.6, 10.3, 10.7, 10.5],
            "low": [9.9, 10.1, 10.3, 10.0, 10.4, 10.2],
            "volume": [1] * 6,
        }
    )
    spec = IndexSpec("000001", "上证指数", "sh000001")
    aligned = align_index_with_amv(index, signals, spec=spec)
    daily, trades, benchmark, summary = run_index_backtest(aligned, cost=0.001)
    assert len(daily) == 6
    assert summary["tencent_symbol"] == "sh000001"
    assert "total_return" in summary
    assert len(benchmark) == 6


def test_exit_protect_ignores_exit_above_ma20() -> None:
    from ai_invest_advisor.amv_strategy_variants import GateRule, apply_gate_rule

    n = 30
    dates = _dates(n)
    # Index steadily above its MA20; AMV crash on last day.
    idx_close = [100 + i for i in range(n)]
    amv_close = [100.0] * (n - 1) + [100.0 * (1 - 0.03)]
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": idx_close,
            "close": idx_close,
            "high": idx_close,
            "low": idx_close,
            "amv_close": amv_close,
            "amv_ret_1d": [0.0] * (n - 1) + [-0.03],
            "amv_ret_2d_sum": [0.0] * n,
        }
    )
    baseline = apply_gate_rule(frame, GateRule(name="baseline"))
    protected = apply_gate_rule(
        frame, GateRule(name="exit_protect_idx_ma20", exit_ignore_if_index_above_ma=20)
    )
    assert bool(baseline.iloc[-1]["exit_signal"]) is True
    assert bool(protected.iloc[-1]["exit_signal"]) is False
