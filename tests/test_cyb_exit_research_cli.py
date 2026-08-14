from __future__ import annotations

import json
from datetime import datetime
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "research_cyb_exit_rules.py"
REQUIRED_OUTPUTS = (
    "rule_summary.csv",
    "rule_trades.csv",
    "qualified_rules.csv",
    "leave_one_year_out.csv",
    "report.md",
    "equity_comparison.png",
    "metadata.json",
)
SUMMARY_COLUMNS = [
    "spec_name", "family", "cost", "complexity", "trades", "total_return",
    "annualized_return", "max_drawdown", "calmar", "win_rate",
    "mean_net_trade_return", "median_net_trade_return", "best_net_trade_return",
    "worst_net_trade_return", "average_holding_days",
]
TRADE_COLUMNS = [
    "spec_name", "family", "cost", "entry_index", "entry_date", "entry_price",
    "exit_signal_index", "exit_index", "exit_date", "exit_price", "exit_reason",
    "holding_days", "gross_return", "net_return",
]
QUALIFIED_COLUMNS = [
    *SUMMARY_COLUMNS,
    "baseline_spec_name", "baseline_total_return", "excess_return_over_baseline",
    "years_with_trades", "profitable_years", "largest_yearly_profit_contribution",
    "annual_contributions", "neighbor_stable", "sensitivity_total_return", "rank",
]
LEAVE_ONE_YEAR_OUT_COLUMNS = [
    "excluded_year", "spec_name", "family", "cost", "complexity", "trades",
    "total_return", "annualized_return", "max_drawdown", "calmar", "rank",
]


def run_cli(output_dir: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output-dir",
            str(output_dir),
            *arguments,
        ],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def test_cli_writes_durable_research_bundle_with_required_schema(tmp_path: Path) -> None:
    completed = run_cli(tmp_path, "--limit-specs", "3")

    paths = json.loads(completed.stdout)
    assert set(paths) == {
        "equity_comparison", "leave_one_year_out", "leave_one_year_out_rows", "metadata",
        "qualified_rows", "qualified_rules", "report", "rule_summary", "rule_trades",
        "summary_rows", "trade_rows",
    }
    assert paths["summary_rows"] == 9
    assert paths["qualified_rows"] == 0
    assert paths["leave_one_year_out_rows"] == 21
    for name in REQUIRED_OUTPUTS:
        assert (tmp_path / name).is_file()
        assert (tmp_path / name).stat().st_size > 0

    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert set(metadata) == {
        "baseline", "candidate_count", "data_sources", "emotion_formula", "emotion_threshold",
        "end", "entry_definition", "execution_conventions", "generated_at", "ma_filter",
        "primary_cost", "qualification_convention", "script_identity", "sensitivity_cost", "start",
    }
    assert metadata["start"] == "2020-01-02"
    assert metadata["end"] == "2026-07-17"
    assert metadata["primary_cost"] == 0.001
    assert metadata["sensitivity_cost"] == 0.0015
    assert metadata["candidate_count"] == 3
    assert metadata["entry_definition"] == "emotion < 15 and signal-day close >= MA250; entry next trading-day open"
    assert metadata["emotion_formula"] == "advancers / (advancers + decliners) * 100"
    assert metadata["emotion_threshold"] == 15.0
    assert metadata["ma_filter"] == "close >= MA250"
    assert metadata["script_identity"] == {
        "path": "scripts/research_cyb_exit_rules.py", "module": "ai_invest_advisor.cyb_exit_research",
    }
    assert metadata["execution_conventions"] == {
        "conditional_exit": "close-confirmed, next trading-day open fill",
        "time_exit": "max-hold exit at the same close",
        "no_lookahead": "indicators are chronological; decisions only use that close or earlier information",
    }
    assert metadata["qualification_convention"] == "full daily marked-to-market equity return"
    assert metadata["baseline"]["spec_name"] == "time_hold_9"
    assert metadata["baseline"]["final_position_open"] is True
    assert datetime.fromisoformat(metadata["generated_at"]).tzinfo is not None
    for source_name, filename in (("index", "cyb_399006_daily.csv"), ("breadth", "all_a_breadth_combined.csv")):
        source = metadata["data_sources"][source_name]
        source_path = ROOT / source["path"]
        assert source_path.name == filename
        assert datetime.fromisoformat(source["modified_at"]).tzinfo is not None
        assert datetime.fromisoformat(source["modified_at"]).timestamp() == pytest.approx(source_path.stat().st_mtime, abs=0.001)

    summary = pd.read_csv(tmp_path / "rule_summary.csv")
    qualified = pd.read_csv(tmp_path / "qualified_rules.csv")
    leave_one_year_out = pd.read_csv(tmp_path / "leave_one_year_out.csv")
    trades = pd.read_csv(tmp_path / "rule_trades.csv")
    assert summary.columns.tolist() == SUMMARY_COLUMNS
    assert trades.columns.tolist() == TRADE_COLUMNS
    assert set(summary["cost"]) == {0.0, 0.001, 0.0015}
    assert len(summary) == 9
    assert qualified.columns.tolist() == QUALIFIED_COLUMNS
    assert leave_one_year_out.columns.tolist() == LEAVE_ONE_YEAR_OUT_COLUMNS
    assert len(leave_one_year_out) == 21

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "fixed 9-session baseline" in report
    assert "emotion < 15" in report
    assert "next trading-day open" in report
    assert "maximum drawdown" in report
    assert "same-close time exits" in report
    assert "Further questions" in report
    assert "RSV" in report
    assert "true range" in report

    with Image.open(tmp_path / "equity_comparison.png") as figure:
        assert figure.width >= 1000
        assert figure.height >= 700
        figure.verify()


def test_qualified_rule_fixture_serializes_and_renders_the_same_convention_contract(tmp_path: Path) -> None:
    module_spec = importlib.util.spec_from_file_location("cyb_exit_cli", SCRIPT)
    assert module_spec is not None and module_spec.loader is not None
    cli = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli)
    qualified = pd.DataFrame(
        [{
            "spec_name": "kdj_min_3_hold_9", "family": "kdj", "cost": 0.001,
            "complexity": 2, "trades": 29, "total_return": 0.9445585970127477,
            "annualized_return": 0.10707190722538124, "max_drawdown": -0.1747891200961219,
            "calmar": 0.6125776430849879, "win_rate": 0.5517241379310345,
            "mean_net_trade_return": 0.02456474080629608, "median_net_trade_return": 0.011170553210610779,
            "best_net_trade_return": 0.1646616838499626, "worst_net_trade_return": -0.04940850182410916,
            "average_holding_days": 8.241379310344827, "baseline_spec_name": "time_hold_9",
            "baseline_total_return": 0.8512036481733976, "excess_return_over_baseline": 0.09335494883935014,
            "years_with_trades": 5, "profitable_years": 4,
            "largest_yearly_profit_contribution": 0.4786321210973812,
            "annual_contributions": {2020: 0.4786321210973812}, "neighbor_stable": True,
            "sensitivity_total_return": 0.8889076762670098, "rank": 1,
        }],
        columns=QUALIFIED_COLUMNS,
    )
    serialized = qualified.copy()
    serialized["annual_contributions"] = serialized["annual_contributions"].map(json.dumps)
    csv_path = tmp_path / "qualified_rules.csv"
    cli._atomic_csv(serialized, csv_path)
    reloaded = pd.read_csv(csv_path)
    baseline = {
        "trades": 27, "total_return": 0.8512036481733976, "completed_trade_return": 1.0157260529952392,
        "annualized_return": 0.0988, "max_drawdown": -0.1543552561773296, "final_position_open": True,
    }
    daily = pd.DataFrame({"date": pd.to_datetime(["2020-01-02", "2026-07-17"]), "equity": [1.0, 1.9445585970127477]})
    report = cli._render_report(
        args=SimpleNamespace(start="2020-01-02", end="2026-07-17", primary_cost=0.001, sensitivity_cost=0.0015),
        baseline=baseline, qualified=qualified, leave_out=pd.DataFrame({"spec_name": ["kdj_min_3_hold_9"], "excluded_year": [2020], "rank": [1]}),
        curves={"kdj_min_3_hold_9": daily},
        metadata={"data_sources": {"index": {"path": "index.csv"}, "breadth": {"path": "breadth.csv"}}},
    )

    assert reloaded.columns.tolist() == QUALIFIED_COLUMNS
    assert reloaded["rank"].tolist() == [1]
    assert reloaded["baseline_spec_name"].eq("time_hold_9").all()
    assert (reloaded["total_return"] > reloaded["baseline_total_return"]).all()
    assert (reloaded["excess_return_over_baseline"] > 0.0).all()
    assert (reloaded["max_drawdown"] >= -0.20).all()
    assert (reloaded["sensitivity_total_return"] > 0.0).all()
    assert "kdj_min_3_hold_9" in report
    assert "85.12%" in report
    assert "101.57%" in report
    assert "final position is open" in report


def test_yearly_performance_includes_the_first_session_return_of_each_year() -> None:
    module_spec = importlib.util.spec_from_file_location("cyb_exit_cli_yearly", SCRIPT)
    assert module_spec is not None and module_spec.loader is not None
    cli = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli)
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-12-31", "2021-01-04", "2021-01-05"]),
            "equity": [1.20, 1.10, 1.21],
        }
    )

    yearly = dict(cli._yearly_performance(daily))

    assert yearly[2020] == pytest.approx(0.20)
    assert yearly[2021] == pytest.approx(1.21 / 1.20 - 1.0)


def test_report_uses_nondefault_closed_baseline_facts_without_default_leaks() -> None:
    module_spec = importlib.util.spec_from_file_location("cyb_exit_cli_nondefault", SCRIPT)
    assert module_spec is not None and module_spec.loader is not None
    cli = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli)
    baseline = {
        "trades": 2, "total_return": 0.1234, "completed_trade_return": 0.2345,
        "annualized_return": 0.25, "max_drawdown": -0.05, "final_position_open": False,
    }

    report = cli._render_report(
        args=SimpleNamespace(start="2025-01-01", end="2025-06-30", primary_cost=0.002, sensitivity_cost=0.003),
        baseline=baseline, qualified=pd.DataFrame(columns=QUALIFIED_COLUMNS),
        leave_out=pd.DataFrame(columns=["spec_name", "excluded_year", "rank"]), curves={},
        metadata={"data_sources": {"index": {"path": "index.csv"}, "breadth": {"path": "breadth.csv"}}},
    )

    assert "2 trades and ended flat" in report
    assert "| time_hold_9 | 2 | flat | 12.34% | 23.45%" in report
    assert "The final position is flat" in report
    assert "23.45% completed-trade product is not used as a hurdle" in report
    for default_fact in ("85.12%", "101.57%", "27 completed", "final position is open"):
        assert default_fact not in report


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--limit-specs", "0"), "limit-specs"),
        (("--start", "2026-07-17", "--end", "2020-01-02"), "date range"),
        (("--primary-cost", "1"), "primary-cost"),
    ],
)
def test_cli_rejects_invalid_inputs_clearly(
    tmp_path: Path, arguments: tuple[str, ...], message: str
) -> None:
    completed = run_cli(tmp_path, *arguments, check=False)

    assert completed.returncode != 0
    assert message in completed.stderr.lower()


def test_cli_deterministically_replaces_existing_outputs(tmp_path: Path) -> None:
    first = run_cli(tmp_path, "--limit-specs", "3")
    first_metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    (tmp_path / "stale.txt").write_text("keep", encoding="utf-8")

    second = run_cli(tmp_path, "--limit-specs", "3")
    second_metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))

    assert json.loads(first.stdout)["rule_summary"] == json.loads(second.stdout)["rule_summary"]
    assert first_metadata["candidate_count"] == second_metadata["candidate_count"] == 3
    assert (tmp_path / "stale.txt").read_text(encoding="utf-8") == "keep"
