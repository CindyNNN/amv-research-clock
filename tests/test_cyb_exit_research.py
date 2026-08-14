import math
import sys
from types import SimpleNamespace
import warnings

import numpy as np
import pandas as pd
import pytest

from ai_invest_advisor.cyb_exit_research import (
    ExitSpec,
    build_exit_specs,
    build_research_frame,
    leave_one_year_out,
    rank_candidates,
    run_grid,
    summarize_run,
    simulate_exit,
)


def _write_inputs(tmp_path, index, breadth):
    tmp_path.mkdir(parents=True, exist_ok=True)
    index_csv = tmp_path / "index.csv"
    breadth_csv = tmp_path / "breadth.csv"
    index.to_csv(index_csv, index=False)
    breadth.to_csv(breadth_csv, index=False)
    return index_csv, breadth_csv


def _frames(periods=260):
    dates = pd.bdate_range("2019-01-02", periods=periods)
    close = pd.Series(range(100, 100 + periods), dtype=float)
    index = pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "close": close,
            "high": close + 1.0,
            "low": close - 1.0,
        }
    )
    breadth = pd.DataFrame(
        {"date": dates, "advancers": 200, "decliners": 800}
    )
    return index, breadth


def test_build_research_frame_sorts_one_row_per_session_and_computes_emotion(
    tmp_path,
):
    index, breadth = _frames(3)
    index = index.iloc[[2, 0, 1]].copy()
    breadth = breadth.iloc[[1, 2, 0]].copy()
    breadth.loc[breadth.index[0], ["advancers", "decliners"]] = [25, 75]
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)

    result = build_research_frame(
        index_csv, breadth_csv, "2019-01-02", "2019-01-04"
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2019-01-02",
        "2019-01-03",
        "2019-01-04",
    ]
    assert result["date"].is_unique
    assert result["emotion"].tolist() == [20.0, 25.0, 20.0]
    assert result["session_id"].tolist() == [0, 1, 2]
    assert result["row_id"].tolist() == [0, 1, 2]


def test_ma250_warmup_is_allowed_but_cannot_signal(tmp_path):
    index, breadth = _frames(255)
    breadth["advancers"] = 10
    breadth["decliners"] = 90
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)

    result = build_research_frame(
        index_csv,
        breadth_csv,
        index["date"].iloc[0],
        index["date"].iloc[-1],
    )

    assert result["ma250"].iloc[:249].isna().all()
    assert result["ma250"].iloc[249:].notna().all()
    assert not result.loc[result["ma250"].isna(), "signal_day"].any()


def test_atr_and_kdj_are_finite_after_warmup_and_use_no_future_rows(tmp_path):
    index, breadth = _frames(30)
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)
    baseline = build_research_frame(
        index_csv,
        breadth_csv,
        index["date"].iloc[0],
        index["date"].iloc[-1],
    )

    changed = index.copy()
    changed.loc[changed.index[-1], ["high", "low", "close"]] = [999, 1, 500]
    changed_index_csv, _ = _write_inputs(tmp_path / "changed", changed, breadth)
    changed_result = build_research_frame(
        changed_index_csv,
        breadth_csv,
        index["date"].iloc[0],
        index["date"].iloc[-1],
    )

    assert baseline["atr14"].iloc[13:].map(math.isfinite).all()
    assert baseline[["k", "d", "j"]].iloc[8:].map(math.isfinite).all().all()
    pd.testing.assert_frame_equal(
        baseline.loc[:28, ["true_range", "atr14", "k", "d", "j"]],
        changed_result.loc[:28, ["true_range", "atr14", "k", "d", "j"]],
    )


@pytest.mark.parametrize("kind", ["duplicate", "nonpositive", "zero_breadth", "missing"])
def test_invalid_input_is_rejected(tmp_path, kind):
    index, breadth = _frames(3)
    if kind == "duplicate":
        index = pd.concat([index, index.iloc[[0]]], ignore_index=True)
    elif kind == "nonpositive":
        index.loc[0, "open"] = 0
    elif kind == "zero_breadth":
        breadth.loc[0, ["advancers", "decliners"]] = [0, 0]
    else:
        breadth.loc[1, "advancers"] = None
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)

    with pytest.raises(ValueError):
        build_research_frame(
            index_csv, breadth_csv, "2019-01-02", "2019-01-04"
        )


def test_entry_is_exactly_next_session_at_that_sessions_open(tmp_path):
    index, breadth = _frames(252)
    breadth["advancers"] = 80
    breadth["decliners"] = 20
    signal_index = 250
    breadth.loc[signal_index, ["advancers", "decliners"]] = [10, 90]
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)

    result = build_research_frame(
        index_csv,
        breadth_csv,
        index["date"].iloc[0],
        index["date"].iloc[-1],
    )

    signal = result.iloc[signal_index]
    entry = result.iloc[signal_index + 1]
    assert signal["signal_day"]
    assert not signal["entry_day"]
    assert entry["entry_day"]
    assert entry["entry_signal_date"] == signal["date"]
    assert entry["entry_price"] == pytest.approx(index["open"].iloc[signal_index + 1])
    assert entry["entry_price"] != signal["close"]


def make_frame(opens, closes, entry_signals, ma5=None):
    size = len(opens)
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=size),
            "open": opens,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "entry_signal": entry_signals,
            "ma5": ma5 if ma5 is not None else close - 1,
            "ma10": close - 1,
            "ma20": close - 1,
            "atr14": 2.0,
            "k": 40.0,
            "d": 30.0,
            "kdj_dead_cross": False,
        }
    )


def test_conditional_ma_exit_fills_next_open_and_ignores_overlapping_entries():
    frame = make_frame(
        opens=[100, 100, 102, 104, 103, 105],
        closes=[100, 101, 103, 102, 104, 106],
        entry_signals=[True, True, False, False, False, False],
        ma5=[99, 99, 100, 103, 103, 104],
    )

    _, trades = simulate_exit(
        frame,
        ExitSpec(name="ma5", family="ma", max_hold=5, ma_period=5, min_hold=1),
        cost=0.0,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["entry_index"] == 1
    assert trade["exit_signal_index"] == 3
    assert trade["exit_index"] == 4
    assert trade["exit_price"] == 103
    assert trade["exit_reason"] == "below_ma5"


def test_time_exit_uses_scheduled_close_costs_and_trade_product_equity():
    frame = make_frame(
        opens=[100, 101, 102, 103, 104],
        closes=[100, 102, 104, 106, 108],
        entry_signals=[True, False, False, False, False],
    )

    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="hold3", family="time", max_hold=3),
        cost=0.001,
    )

    expected = 106 / 101 * 0.999**2 - 1
    trade = trades.iloc[0]
    assert trade["exit_index"] == 3
    assert trade["exit_price"] == 106
    assert trade["holding_days"] == 3
    assert trade["net_return"] == pytest.approx(expected)
    assert daily["equity"].iloc[-1] == pytest.approx(1 + expected)


def test_minimum_holding_period_defers_conditional_exit():
    frame = make_frame(
        opens=[100, 100, 90, 89, 88],
        closes=[100, 90, 89, 88, 87],
        entry_signals=[True, False, False, False, False],
    )

    _, trades = simulate_exit(
        frame,
        ExitSpec(name="stop", family="threshold", max_hold=5, stop_loss=0.05, min_hold=2),
        cost=0.0,
    )

    trade = trades.iloc[0]
    assert trade["exit_signal_index"] == 2
    assert trade["exit_index"] == 3
    assert trade["exit_reason"] == "stop_loss"


def test_conditional_exit_priority_is_deterministic():
    frame = make_frame(
        opens=[100, 100, 90, 90],
        closes=[100, 90, 89, 89],
        entry_signals=[True, False, False, False],
        ma5=[99, 95, 95, 95],
    )

    _, trades = simulate_exit(
        frame,
        ExitSpec(
            name="priority",
            family="combo",
            max_hold=5,
            stop_loss=0.05,
            take_profit=0.01,
            trail_pct=0.01,
            atr_multiple=1.0,
            ma_period=5,
            kdj_dead_cross=True,
        ),
        cost=0.0,
    )

    assert trades.iloc[0]["exit_reason"] == "stop_loss"


def test_final_row_conditional_exit_remains_open_without_invented_next_open_fill():
    frame = make_frame(
        opens=[100, 100, 90],
        closes=[100, 100, 90],
        entry_signals=[True, False, False],
    )

    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="stop", family="threshold", max_hold=5, stop_loss=0.05),
        cost=0.001,
    )

    assert trades.empty
    assert daily.iloc[-1]["position"] == 1
    assert daily.iloc[-1]["equity"] == pytest.approx(90 / 100 * 0.999)


def test_pending_exit_suppresses_entry_signals():
    frame = make_frame(
        opens=[100, 100, 90, 89, 89],
        closes=[100, 90, 89, 89, 89],
        entry_signals=[True, False, True, False, False],
    )

    _, trades = simulate_exit(
        frame,
        ExitSpec(name="stop", family="threshold", max_hold=5, stop_loss=0.05),
        cost=0.0,
    )

    assert len(trades) == 1
    assert trades.iloc[0]["exit_index"] == 2


def test_empty_input_returns_schema_stable_daily_and_trade_tables():
    frame = pd.DataFrame(columns=["date", "open", "close", "entry_signal"])

    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="hold", family="time", max_hold=3),
        cost=0.0,
    )

    assert daily.empty
    assert daily.columns.tolist() == ["date", "equity", "position", "action"]
    assert trades.empty
    assert trades.columns.tolist() == [
        "entry_index",
        "entry_date",
        "entry_price",
        "exit_signal_index",
        "exit_index",
        "exit_date",
        "exit_price",
        "exit_reason",
        "holding_days",
        "gross_return",
        "net_return",
    ]


def test_no_trade_input_returns_daily_rows_and_empty_trade_schema():
    frame = make_frame(
        opens=[100, 101],
        closes=[100, 101],
        entry_signals=[False, False],
    )

    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="hold", family="time", max_hold=3),
        cost=0.0,
    )

    assert daily.to_dict("records") == [
        {"date": frame.iloc[0]["date"], "equity": 1.0, "position": 0, "action": "flat"},
        {"date": frame.iloc[1]["date"], "equity": 1.0, "position": 0, "action": "flat"},
    ]
    assert trades.empty
    assert trades.columns.tolist()[0] == "entry_index"


def test_non_kdj_rule_can_hold_and_exit_without_kdj_column():
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=3),
            "open": [100.0, 100.0, 105.0],
            "close": [100.0, 105.0, 110.0],
            "entry_signal": [True, False, False],
        }
    )

    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="hold2", family="time", max_hold=2),
        cost=0.0,
    )

    assert daily["position"].tolist() == [0, 1, 0]
    assert trades[["entry_index", "exit_index", "exit_reason"]].to_dict("records") == [
        {"entry_index": 1, "exit_index": 2, "exit_reason": "max_hold"}
    ]


@pytest.mark.parametrize("column", ["date", "open", "close", "entry_signal"])
def test_required_base_columns_raise_clear_value_error(column):
    frame = make_frame(
        opens=[100, 100],
        closes=[100, 100],
        entry_signals=[False, False],
    ).drop(columns=column)

    with pytest.raises(ValueError, match=column):
        simulate_exit(frame, ExitSpec(name="hold", family="time", max_hold=3), cost=0.0)


@pytest.mark.parametrize(
    ("spec", "missing_column"),
    [
        (ExitSpec(name="ma", family="ma", max_hold=3, ma_period=5), "ma5"),
        (ExitSpec(name="atr", family="atr", max_hold=3, atr_multiple=2.0), "atr14"),
        (ExitSpec(name="kdj", family="kdj", max_hold=3, kdj_dead_cross=True), "kdj_dead_cross"),
    ],
)
def test_rule_specific_required_columns_raise_clear_value_error(spec, missing_column):
    frame = make_frame(
        opens=[100, 100],
        closes=[100, 100],
        entry_signals=[False, False],
    ).drop(columns=missing_column)

    with pytest.raises(ValueError, match=missing_column):
        simulate_exit(frame, spec, cost=0.0)


def test_candidate_grid_is_exactly_the_approved_deterministic_space():
    specs = build_exit_specs()

    assert {spec.family for spec in specs} == {
        "time",
        "threshold",
        "trailing",
        "atr",
        "ma",
        "kdj",
        "combo",
    }
    assert len(specs) == 125
    assert [spec.name for spec in specs] == [spec.name for spec in build_exit_specs()]
    assert len({spec.name for spec in specs}) == len(specs)
    assert all(5 <= spec.max_hold <= 15 for spec in specs)
    assert {spec.max_hold for spec in specs if spec.family == "time"} == set(range(5, 16))
    assert not any(
        spec.take_profit is not None
        and spec.trail_pct is not None
        and spec.atr_multiple is not None
        for spec in specs
    )
    combos = [spec for spec in specs if spec.family == "combo"]
    assert len(combos) == 24
    assert all(spec.trail_pct == 0.08 for spec in combos)
    assert all(spec.kdj_dead_cross or spec.ma_period in {5, 10, 20} for spec in combos)
    assert {
        (spec.stop_loss, spec.max_hold)
        for spec in specs
        if spec.stop_loss is not None
    } == {(stop_loss, max_hold) for stop_loss in (0.04, 0.06, 0.08) for max_hold in (9, 12, 15)}
    assert {
        (spec.take_profit, spec.max_hold)
        for spec in specs
        if spec.take_profit is not None
    } == {
        (take_profit, max_hold)
        for take_profit in (0.05, 0.08, 0.10, 0.12)
        for max_hold in (9, 12, 15)
    }
    assert {
        (spec.trail_pct, spec.trail_activation, spec.max_hold)
        for spec in specs
        if spec.family == "trailing"
    } == {
        (trail_pct, activation, max_hold)
        for trail_pct in (0.04, 0.06, 0.08, 0.10)
        for activation in (0.0, 0.03, 0.05)
        for max_hold in (9, 12, 15)
    }
    assert {
        (spec.atr_multiple, spec.max_hold)
        for spec in specs
        if spec.family == "atr"
    } == {(multiple, max_hold) for multiple in (2.0, 2.5, 3.0) for max_hold in (9, 12, 15)}
    assert {
        (spec.ma_period, spec.min_hold, spec.max_hold)
        for spec in specs
        if spec.family == "ma"
    } == {
        (period, min_hold, max_hold)
        for period in (5, 10, 20)
        for min_hold in (2, 3)
        for max_hold in (9, 12, 15)
    }
    assert {
        (spec.min_hold, spec.max_hold)
        for spec in specs
        if spec.family == "kdj"
    } == {(min_hold, max_hold) for min_hold in (2, 3) for max_hold in (9, 12, 15)}
    assert {
        (spec.kdj_dead_cross, spec.ma_period, spec.min_hold, spec.max_hold, spec.trail_pct)
        for spec in combos
    } == {
        (True, None, min_hold, max_hold, 0.08)
        for min_hold in (2, 3)
        for max_hold in (9, 12, 15)
    } | {
        (False, period, min_hold, max_hold, 0.08)
        for period in (5, 10, 20)
        for min_hold in (2, 3)
        for max_hold in (9, 12, 15)
    }


def test_summarize_run_calculates_metrics_from_equity_and_completed_trades():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-07-02", "2025-01-01", "2025-01-01"]),
            "equity": [1.0, 1.1, 0.99, 1.2],
            "position": [0, 1, 0, 0],
            "action": ["flat", "hold", "exit", "flat"],
        }
    )
    trades = pd.DataFrame(
        {
            "net_return": [0.1, -0.1],
            "holding_days": [2, 4],
        }
    )
    spec = ExitSpec(name="hold5", family="time", max_hold=5)

    summary = summarize_run(daily, trades, spec, cost=0.001)

    assert summary["spec_name"] == "hold5"
    assert summary["family"] == "time"
    assert summary["cost"] == 0.001
    assert summary["complexity"] == 1
    assert summary["trades"] == 2
    assert summary["total_return"] == pytest.approx(0.2)
    assert summary["annualized_return"] == pytest.approx((1.2 ** (365.25 / 365)) - 1)
    assert summary["max_drawdown"] == pytest.approx(-0.1)
    assert summary["calmar"] == pytest.approx(summary["annualized_return"] / 0.1)
    assert summary["win_rate"] == pytest.approx(0.5)
    assert summary["mean_net_trade_return"] == pytest.approx(0.0)
    assert summary["median_net_trade_return"] == pytest.approx(0.0)
    assert summary["best_net_trade_return"] == pytest.approx(0.1)
    assert summary["worst_net_trade_return"] == pytest.approx(-0.1)
    assert summary["average_holding_days"] == pytest.approx(3.0)


def test_summarize_run_empty_input_uses_schema_stable_zero_and_nan_conventions():
    spec = ExitSpec(name="hold5", family="time", max_hold=5)

    summary = summarize_run(
        pd.DataFrame(columns=["date", "equity", "position", "action"]),
        pd.DataFrame(columns=["net_return", "holding_days"]),
        spec,
        cost=0.0,
    )

    assert summary["trades"] == 0
    assert summary["total_return"] == 0.0
    assert summary["annualized_return"] == 0.0
    assert summary["max_drawdown"] == 0.0
    assert summary["calmar"] == 0.0
    assert summary["win_rate"] == 0.0
    for key in (
        "mean_net_trade_return",
        "median_net_trade_return",
        "best_net_trade_return",
        "worst_net_trade_return",
        "average_holding_days",
    ):
        assert math.isnan(summary[key])
    assert not any(math.isinf(value) for value in summary.values() if isinstance(value, float))


def test_summarize_run_caps_extreme_finite_equity_annualization_without_infinity():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "equity": [1.0, 1e308],
            "position": [0, 0],
            "action": ["flat", "flat"],
        }
    )

    summary = summarize_run(
        daily,
        pd.DataFrame(columns=["net_return", "holding_days"]),
        ExitSpec(name="hold5", family="time", max_hold=5),
        cost=0.0,
    )

    assert summary["annualized_return"] == sys.float_info.max
    assert math.isfinite(summary["annualized_return"])
    assert math.isfinite(summary["calmar"])


def test_real_research_frame_provides_causal_ma_and_kdj_columns_for_full_grid(tmp_path):
    index, breadth = _frames(260)
    breadth["advancers"] = 10
    breadth["decliners"] = 90
    index_csv, breadth_csv = _write_inputs(tmp_path, index, breadth)

    frame = build_research_frame(
        index_csv, breadth_csv, index["date"].iloc[0], index["date"].iloc[-1]
    )
    specs = build_exit_specs()
    ma_spec = next(spec for spec in specs if spec.family == "ma")
    kdj_spec = next(spec for spec in specs if spec.family == "kdj")

    assert {"ma5", "ma10", "ma20", "kdj_dead_cross"}.issubset(frame.columns)
    simulate_exit(frame, ma_spec, cost=0.0)
    simulate_exit(frame, kdj_spec, cost=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        summary, _ = run_grid(
            frame, SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015)
        )
    assert len(summary) == 125 * 3
    assert {ma_spec.name, kdj_spec.name}.issubset(summary["spec_name"])


def test_run_grid_has_stable_ordered_schemas_cost_monotonicity_and_no_input_mutation():
    size = 40
    close = pd.Series(100.0 + np.arange(size) * 0.5)
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=size),
            "open": close - 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "entry_signal": [True] + [False] * (size - 1),
            "ma5": close - 1.0,
            "ma10": close - 1.0,
            "ma20": close - 1.0,
            "atr14": 2.0,
            "k": 40.0,
            "d": 30.0,
            "kdj_dead_cross": False,
        }
    )
    original = frame.copy(deep=True)

    summary, trades = run_grid(
        frame, SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015)
    )

    assert len(summary) == 125 * 3
    assert summary.columns.tolist() == [
        "spec_name",
        "family",
        "cost",
        "complexity",
        "trades",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "calmar",
        "win_rate",
        "mean_net_trade_return",
        "median_net_trade_return",
        "best_net_trade_return",
        "worst_net_trade_return",
        "average_holding_days",
    ]
    assert trades.columns.tolist() == [
        "spec_name",
        "family",
        "cost",
        "entry_index",
        "entry_date",
        "entry_price",
        "exit_signal_index",
        "exit_index",
        "exit_date",
        "exit_price",
        "exit_reason",
        "holding_days",
        "gross_return",
        "net_return",
    ]
    assert summary[["spec_name", "cost"]].duplicated().sum() == 0
    assert summary[["spec_name", "cost"]].equals(
        summary.sort_values(["spec_name", "cost"], kind="stable")[["spec_name", "cost"]]
    )
    pivot = summary.pivot(index="spec_name", columns="cost", values="total_return")
    assert (pivot[0.0] >= pivot[0.001]).all()
    assert (pivot[0.001] >= pivot[0.0015]).all()
    pd.testing.assert_frame_equal(frame, original)


def _candidate_summary(rows):
    """Build concise, finite candidate metrics for robustness tests."""

    return pd.DataFrame(
        [
            {
                "family": "time",
                "complexity": 1,
                "trades": 3,
                "annualized_return": 0.2,
                "max_drawdown": -0.1,
                "calmar": 2.0,
                **row,
            }
            for row in rows
        ]
    )


def _baseline_daily(total_return: float = 1.0157) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2026-07-17"]),
            "equity": [1.0, 1.0 + total_return],
            "position": [0, 1],
            "action": ["flat", "hold"],
        }
    )


def test_rank_candidates_uses_full_daily_baseline_and_exposes_auditable_hurdle():
    summary = _candidate_summary(
        [
            {"spec_name": "below_baseline", "cost": 0.001, "total_return": 0.85},
            {"spec_name": "above_baseline", "cost": 0.001, "total_return": 0.90},
            {"spec_name": "above_baseline", "cost": 0.0015, "total_return": 0.80},
        ]
    )
    baseline_daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2026-07-17"]),
            "equity": [1.0, 1.8512036481733976],
            "position": [0, 1],
            "action": ["flat", "hold"],
        }
    )

    ranked = rank_candidates(
        summary,
        pd.DataFrame(),
        SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=baseline_daily,
    )

    assert ranked["spec_name"].tolist() == ["above_baseline"]
    assert ranked.loc[0, "baseline_spec_name"] == "time_hold_9"
    assert ranked.loc[0, "baseline_total_return"] == pytest.approx(0.8512036481733976)
    assert ranked.loc[0, "excess_return_over_baseline"] == pytest.approx(0.0487963518266024)


def test_rank_candidates_applies_strict_primary_drawdown_and_sensitivity_rules():
    summary = _candidate_summary(
        [
            {"spec_name": "high_dd", "cost": 0.001, "total_return": 2.0, "max_drawdown": -0.21},
            {"spec_name": "at_baseline", "cost": 0.001, "total_return": 1.0157},
            {"spec_name": "qualified", "cost": 0.001, "total_return": 1.1},
            {"spec_name": "qualified", "cost": 0.0015, "total_return": 0.1},
            {"spec_name": "sensitivity_loss", "cost": 0.001, "total_return": 1.2},
            {"spec_name": "sensitivity_loss", "cost": 0.0015, "total_return": 0.0},
        ]
    )

    ranked = rank_candidates(
        summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    assert ranked["spec_name"].tolist() == ["qualified"]


def test_rank_candidates_rejects_missing_sensitivity_and_nonfinite_primary_metrics():
    summary = _candidate_summary(
        [
            {"spec_name": "missing_sensitivity", "cost": 0.001, "total_return": 1.1},
            {"spec_name": "nan_return", "cost": 0.001, "total_return": float("nan")},
            {"spec_name": "nan_drawdown", "cost": 0.001, "total_return": 1.1, "max_drawdown": float("nan")},
            {"spec_name": "nan_calmar", "cost": 0.001, "total_return": 1.1, "calmar": float("nan")},
            {"spec_name": "infinite_calmar", "cost": 0.001, "total_return": 1.1, "calmar": float("inf")},
        ]
        + [
            {"spec_name": name, "cost": 0.0015, "total_return": 0.1}
            for name in ("nan_return", "nan_drawdown", "nan_calmar", "infinite_calmar")
        ]
    )

    ranked = rank_candidates(
        summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    assert ranked.empty
    assert ranked.columns.tolist() == [
        "spec_name", "family", "cost", "complexity", "trades", "total_return",
        "annualized_return", "max_drawdown", "calmar", "win_rate",
        "mean_net_trade_return", "median_net_trade_return", "best_net_trade_return",
        "worst_net_trade_return", "average_holding_days", "baseline_spec_name",
        "baseline_total_return", "excess_return_over_baseline", "years_with_trades",
        "profitable_years", "largest_yearly_profit_contribution", "annual_contributions",
        "neighbor_stable", "rank",
    ]


def test_rank_candidates_uses_yearly_completed_trade_contributions_and_deterministic_ties():
    summary = _candidate_summary(
        [
            {"spec_name": "time_hold_5", "cost": 0.001, "total_return": 1.2, "calmar": 2.0, "max_drawdown": -0.1},
            {"spec_name": "time_hold_6", "cost": 0.001, "total_return": 1.2, "calmar": 2.0, "max_drawdown": -0.1},
            {"spec_name": "time_hold_5", "cost": 0.0015, "total_return": 0.1},
            {"spec_name": "time_hold_6", "cost": 0.0015, "total_return": 0.1},
        ]
    )
    trades = pd.DataFrame(
        {
            "spec_name": ["time_hold_5", "time_hold_5", "time_hold_5"],
            "cost": [0.001, 0.001, 0.001],
            "exit_date": pd.to_datetime(["2021-01-05", "2021-03-05", "2022-01-05"]),
            "net_return": [0.1, -0.05, 0.2],
        }
    )

    ranked = rank_candidates(
        summary, trades, SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    assert ranked["spec_name"].tolist() == ["time_hold_5", "time_hold_6"]
    first = ranked.iloc[0]
    assert first["years_with_trades"] == 2
    assert first["profitable_years"] == 2
    assert first["annual_contributions"] == {2021: pytest.approx(0.045), 2022: pytest.approx(0.2)}
    assert first["largest_yearly_profit_contribution"] == pytest.approx(0.2)
    assert bool(first["neighbor_stable"])
    assert bool(ranked.iloc[1]["neighbor_stable"])
    assert ranked["rank"].tolist() == [1, 2]


def test_rank_candidates_derives_neighbor_stability_from_exit_specs_not_names():
    summary = _candidate_summary(
        [
            {"spec_name": "time_hold_5", "cost": 0.001, "total_return": 1.2},
            {"spec_name": "time_hold_6", "cost": 0.001, "total_return": 1.3},
            {"spec_name": "pretend_time_hold_7", "cost": 0.001, "total_return": 1.4},
            {"spec_name": "time_hold_5", "cost": 0.0015, "total_return": 0.1},
            {"spec_name": "time_hold_6", "cost": 0.0015, "total_return": 0.1},
            {"spec_name": "pretend_time_hold_7", "cost": 0.0015, "total_return": 0.1},
        ]
    )

    ranked = rank_candidates(
        summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    stable = ranked.set_index("spec_name")["neighbor_stable"].to_dict()
    assert stable == {"pretend_time_hold_7": False, "time_hold_5": True, "time_hold_6": True}


def test_rank_candidates_keeps_kdj_and_ma_combo_domains_distinct():
    names = [
        "combo_kdj_trail_08_min_2_hold_9",
        "combo_ma_5_trail_08_min_2_hold_9",
    ]
    summary = _candidate_summary(
        [
            {"spec_name": name, "cost": cost, "total_return": total_return}
            for name in names
            for cost, total_return in ((0.001, 1.2), (0.0015, 0.1))
        ]
    )

    ranked = rank_candidates(
        summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    assert ranked["spec_name"].tolist() == names
    assert not ranked["neighbor_stable"].any()


def test_rank_candidates_finds_normalized_neighbors_in_every_approved_family():
    neighbor_pairs = [
        ("time_hold_5", "time_hold_6"),
        ("stop_0_04_hold_9", "stop_0_04_hold_12"),
        ("trail_0_04_activate_0_hold_9", "trail_0_04_activate_0_hold_12"),
        ("atr_2_hold_9", "atr_2_hold_12"),
        ("ma_5_min_2_hold_9", "ma_5_min_2_hold_12"),
        ("kdj_min_2_hold_9", "kdj_min_2_hold_12"),
        ("combo_kdj_trail_08_min_2_hold_9", "combo_kdj_trail_08_min_2_hold_12"),
        ("combo_ma_5_trail_08_min_2_hold_9", "combo_ma_5_trail_08_min_2_hold_12"),
    ]
    names = [name for pair in neighbor_pairs for name in pair]
    summary = _candidate_summary(
        [
            {"spec_name": name, "cost": cost, "total_return": total_return}
            for name in names
            for cost, total_return in ((0.001, 1.2), (0.0015, 0.1))
        ]
    )

    ranked = rank_candidates(
        summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015),
        baseline_daily=_baseline_daily(),
    )

    assert set(ranked["spec_name"]) == set(names)
    assert ranked["neighbor_stable"].all()


def test_leave_one_year_out_keeps_every_spec_parameters_and_restarts_ranks_per_year():
    frame = make_frame(
        opens=[100, 100, 101, 102, 103],
        closes=[100, 101, 102, 103, 104],
        entry_signals=[True, False, False, False, False],
    )
    frame["date"] = pd.to_datetime(["2020-01-02", "2020-01-03", "2022-01-04", "2024-01-02", "2026-01-02"])
    specs = [
        ExitSpec("time_hold_2", "time", max_hold=2),
        ExitSpec("time_hold_3", "time", max_hold=3),
    ]
    original = frame.copy(deep=True)

    result = leave_one_year_out(frame, specs, SimpleNamespace(primary_cost=0.001))

    assert len(result) == 7 * len(specs)
    assert set(result["excluded_year"]) == set(range(2020, 2027))
    assert result.groupby("excluded_year")["spec_name"].apply(list).map(set).eq({spec.name for spec in specs}).all()
    assert all(
        sorted(ranks) == [1, 2]
        for ranks in result.groupby("excluded_year")["rank"].apply(list)
    )
    pd.testing.assert_frame_equal(frame, original)


def test_leave_one_year_out_never_bridges_pending_entry_across_removed_year_gap():
    frame = make_frame(
        opens=[100, 100, 110, 111],
        closes=[100, 101, 110, 111],
        entry_signals=[True, False, False, False],
    )
    frame["date"] = pd.to_datetime(["2020-12-30", "2020-12-31", "2022-01-03", "2022-01-04"])

    result = leave_one_year_out(
        frame,
        [ExitSpec("time_hold_2", "time", max_hold=2)],
        SimpleNamespace(primary_cost=0.0),
    )

    assert result.loc[result["excluded_year"] == 2021, "trades"].item() == 0


def test_leave_one_year_out_discards_open_position_marks_at_removed_year_boundaries():
    frame = make_frame(
        opens=[100, 100, 110, 111, 112],
        closes=[100, 101, 110, 111, 112],
        entry_signals=[True, False, False, False, False],
    )
    frame["date"] = pd.to_datetime(
        ["2020-12-30", "2020-12-31", "2021-01-04", "2022-01-03", "2022-01-04"]
    )

    result = leave_one_year_out(
        frame,
        [ExitSpec("time_hold_3", "time", max_hold=3)],
        SimpleNamespace(primary_cost=0.0),
    )

    held_out = result.loc[result["excluded_year"] == 2021].iloc[0]
    assert held_out["trades"] == 0
    assert held_out["total_return"] == pytest.approx(0.0)
    assert held_out["max_drawdown"] == pytest.approx(0.0)


def test_leave_one_year_out_and_rank_candidates_have_stable_empty_schemas_without_mutation():
    empty_frame = pd.DataFrame(columns=["date", "open", "close", "entry_signal"])
    empty_summary = pd.DataFrame(columns=["spec_name", "cost", "total_return", "max_drawdown", "calmar"])
    original_frame = empty_frame.copy(deep=True)
    original_summary = empty_summary.copy(deep=True)

    ranked = rank_candidates(empty_summary, pd.DataFrame(), SimpleNamespace(primary_cost=0.001, sensitivity_cost=0.0015))
    held_out = leave_one_year_out(empty_frame, [], SimpleNamespace(primary_cost=0.001))

    assert ranked.empty
    assert held_out.empty
    assert held_out.columns.tolist() == [
        "excluded_year", "spec_name", "family", "cost", "complexity", "trades",
        "total_return", "annualized_return", "max_drawdown", "calmar", "rank",
    ]
    pd.testing.assert_frame_equal(empty_frame, original_frame)
    pd.testing.assert_frame_equal(empty_summary, original_summary)
