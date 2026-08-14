"""Create a reproducible local CYB exit-rule research bundle.

The script is deliberately offline: it consumes the approved CSV snapshots,
does not contact brokers or send signals, and writes only the requested bundle.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.cyb_exit_research import (  # noqa: E402
    ExitSpec,
    build_exit_specs,
    build_research_frame,
    leave_one_year_out,
    rank_candidates,
    simulate_exit,
    summarize_run,
)


INDEX_CSV = ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "cyb_399006_daily.csv"
BREADTH_CSV = ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "all_a_breadth_combined.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "backtests" / "cyb_exit_rule_research"
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


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit-specs must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("limit-specs must be a positive integer")
    return number


def _finite_cost(value: str) -> float:
    try:
        cost = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("cost must be a finite number in [0, 1)") from exc
    if not math.isfinite(cost) or not 0.0 <= cost < 1.0:
        raise argparse.ArgumentTypeError("cost must be a finite number in [0, 1)")
    return cost


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--primary-cost", type=_finite_cost, default=0.001)
    parser.add_argument("--sensitivity-cost", type=_finite_cost, default=0.0015)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit-specs", type=_positive_integer)
    args = parser.parse_args(argv)
    try:
        start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    except (TypeError, ValueError) as exc:
        parser.error("date range must contain valid ISO dates")
        raise AssertionError("argparse exits") from exc
    if pd.isna(start) or pd.isna(end) or start > end:
        parser.error("date range is invalid: start must not be after end")
    if args.primary_cost == args.sensitivity_cost:
        parser.error("primary-cost and sensitivity-cost must differ")
    return args


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    _atomic_bytes(path, frame.to_csv(index=False, encoding="utf-8").encode("utf-8"))


def _atomic_text(path: Path, content: str) -> None:
    _atomic_bytes(path, content.encode("utf-8"))


def _atomic_figure(figure: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".png", dir=path.parent)
    os.close(descriptor)
    try:
        figure.savefig(temporary_name, dpi=180, bbox_inches="tight")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    finally:
        plt.close(figure)


def _run_selected_grid(
    frame: pd.DataFrame, specs: list[ExitSpec], primary_cost: float, sensitivity_cost: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    for spec in specs:
        for cost in sorted({0.0, primary_cost, sensitivity_cost}):
            daily, trades = simulate_exit(frame, spec, cost)
            summary_rows.append(summarize_run(daily, trades, spec, cost))
            if not trades.empty:
                annotated = trades.copy()
                annotated.insert(0, "cost", cost)
                annotated.insert(0, "family", spec.family)
                annotated.insert(0, "spec_name", spec.name)
                trade_frames.append(annotated)
    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)
    trades = (
        pd.concat(trade_frames, ignore_index=True).reindex(columns=TRADE_COLUMNS)
        if trade_frames else pd.DataFrame(columns=TRADE_COLUMNS)
    )
    if not trades.empty:
        trades = trades.sort_values(["spec_name", "cost", "entry_index", "exit_index"], kind="stable").reset_index(drop=True)
    return summary, trades


def _format_percent(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else f"{number:.2%}"


def _source_identity(path: Path) -> dict[str, str]:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "modified_at": modified.isoformat()}


def _yearly_performance(daily: pd.DataFrame) -> list[tuple[int, float]]:
    """Return calendar-year equity returns including each year's first session."""

    data = daily.copy().sort_values("date", kind="stable")
    data["year"] = pd.to_datetime(data["date"]).dt.year
    rows: list[tuple[int, float]] = []
    previous_year_end_equity = 1.0
    for year, group in data.groupby("year", sort=True):
        ending_equity = float(group["equity"].iloc[-1])
        rows.append((int(year), ending_equity / previous_year_end_equity - 1.0))
        previous_year_end_equity = ending_equity
    return rows


def _rule_table(rows: pd.DataFrame) -> list[str]:
    if rows.empty:
        return ["No rules qualified under the stated constraints."]
    output = ["| Rank | Rule | Trades | Total return | Baseline hurdle | Excess return | Maximum drawdown | Sensitivity cost return | Neighbor stable |", "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for _, row in rows.iterrows():
        output.append(
            "| {rank} | {name} | {trades} | {total} | {baseline} | {excess} | {drawdown} | {sensitivity} | {stable} |".format(
                rank=int(row["rank"]), name=row["spec_name"], trades=int(row["trades"]),
                total=_format_percent(row["total_return"]), drawdown=_format_percent(row["max_drawdown"]),
                baseline=_format_percent(row["baseline_total_return"]),
                excess=_format_percent(row["excess_return_over_baseline"]),
                sensitivity=_format_percent(row.get("sensitivity_total_return")),
                stable="yes" if bool(row["neighbor_stable"]) else "no",
            )
        )
    return output


def _render_figure(curves: list[tuple[str, pd.DataFrame]], path: Path, window: str, cost: float) -> None:
    palette = ["#355C7D", "#6C5B7B", "#C06C84", "#8A6D3B"]
    styles = ["-", "--", "-.", ":"]
    figure, (equity_axis, drawdown_axis) = plt.subplots(2, 1, figsize=(13, 9), sharex=True, layout="constrained")
    for index, (label, daily) in enumerate(curves):
        color, style = palette[index], styles[index]
        dates = pd.to_datetime(daily["date"])
        equity = pd.to_numeric(daily["equity"])
        drawdown = equity / equity.cummax() - 1.0
        equity_axis.plot(dates, equity, label=label, color=color, linestyle=style, linewidth=1.8)
        drawdown_axis.plot(dates, drawdown, label=label, color=color, linestyle=style, linewidth=1.8)
    figure.suptitle("CYB exit-rule equity and drawdown comparison", fontsize=14, fontweight="semibold")
    figure.text(0.5, 0.945, f"{window}; primary one-way cost {cost:.2%}; close-confirmed conditional exits", ha="center", fontsize=9)
    equity_axis.set_ylabel("Net equity")
    drawdown_axis.set_ylabel("Drawdown")
    drawdown_axis.set_xlabel("Trading date")
    drawdown_axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    for axis in (equity_axis, drawdown_axis):
        axis.grid(alpha=0.18, linewidth=0.6)
        axis.legend(loc="best", fontsize=8)
    _atomic_figure(figure, path)


def _render_report(
    *, args: argparse.Namespace, baseline: dict[str, object], qualified: pd.DataFrame,
    leave_out: pd.DataFrame, curves: dict[str, pd.DataFrame], metadata: dict[str, object],
) -> str:
    selected = qualified.head(3).copy()
    primary_summary = {str(row["spec_name"]): row for _, row in selected.iterrows()}
    final_position_open = bool(baseline["final_position_open"])
    final_position_label = "open" if final_position_open else "flat"
    final_position_summary = (
        "plus one final open position"
        if final_position_open
        else "and ended flat"
    )
    reconciliation = (
        f"Both figures are correct but answer different questions: "
        f"{_format_percent(baseline['total_return'])} is the canonical full daily "
        f"marked-to-market return including the final open position at the {args.end} close, "
        f"while {_format_percent(baseline['completed_trade_return'])} is the realized product "
        f"of the {int(baseline['trades'])} completed trade returns. The final position is open. "
        f"The {_format_percent(baseline['completed_trade_return'])} completed-trade product is "
        f"not used as a hurdle; it is a disclosure."
        if final_position_open
        else f"Both figures are correct but answer different questions: "
        f"{_format_percent(baseline['total_return'])} is the canonical full daily "
        f"marked-to-market return through {args.end}, while "
        f"{_format_percent(baseline['completed_trade_return'])} is the realized product of the "
        f"{int(baseline['trades'])} completed trade returns. The final position is flat. "
        f"The {_format_percent(baseline['completed_trade_return'])} completed-trade product is "
        f"not used as a hurdle; it is a disclosure."
    )
    lines = [
        "# CYB Exit-Rule Research",
        "",
        "## Technical summary",
        "",
        f"This local backtest covers {args.start} through {args.end}. The fixed 9-session baseline completed {int(baseline['trades'])} trades {final_position_summary}; its full daily marked-to-market return was {_format_percent(baseline['total_return'])} after the {args.primary_cost:.2%} one-way primary cost, with maximum drawdown {_format_percent(baseline['max_drawdown'])}.",
        "The results describe this historical sample only and do not establish a causal effect or future outcome.",
        "",
        "## Fixed 9-session baseline at primary cost",
        "",
        "The baseline is `time_hold_9`: emotion < 15 and signal-day close >= MA250; entry is at the next trading-day open, and the time exit is at the ninth holding session close.",
        "",
        "| Rule | Completed trades | Final position | Full daily return | Completed-trade product | Annualized return | Maximum drawdown |",
        "|---|---:|---|---:|---:|---:|---:|",
        f"| time_hold_9 | {int(baseline['trades'])} | {final_position_label} | {_format_percent(baseline['total_return'])} | {_format_percent(baseline['completed_trade_return'])} | {_format_percent(baseline['annualized_return'])} | {_format_percent(baseline['max_drawdown'])} |",
        "",
        reconciliation,
        "",
        "## Qualified rules and direct answer",
        "",
    ]
    if qualified.empty:
        lines.extend([
            f"No rule in the selected grid beats the exact {_format_percent(baseline['total_return'])} full-daily `time_hold_9` baseline while keeping maximum drawdown within 20% and retaining a positive full-daily result at the sensitivity cost.",
            "未找到同时提高收益且满足回撤约束的卖出规则。",
        ])
    else:
        lines.append(f"The following rules strictly beat the exact {_format_percent(baseline['total_return'])} full-daily `time_hold_9` baseline while keeping maximum drawdown within 20% and retaining a positive full-daily sensitivity-cost result. The {_format_percent(baseline['completed_trade_return'])} completed-trade product is not used as a hurdle.")
    lines.extend(["", *_rule_table(qualified), "", "## Recommendation and alternatives", ""])
    if selected.empty:
        lines.append("No recommendation is made because no rule qualifies under the pre-specified threshold.")
    else:
        first = selected.iloc[0]
        lines.append(f"Recommended rule: `{first['spec_name']}` (rank 1; {int(first['trades'])} trades; {_format_percent(first['total_return'])} total return; {_format_percent(first['max_drawdown'])} maximum drawdown).")
        alternatives = selected.iloc[1:]
        if alternatives.empty:
            lines.append("No qualified alternative was selected.")
        else:
            lines.append("Alternatives: " + ", ".join(f"`{row['spec_name']}`" for _, row in alternatives.iterrows()) + ".")
    lines.extend(["", "## Yearly performance, leave-one-year-out, neighbor stability, and sensitivity cost", ""])
    for name, row in primary_summary.items():
        yearly = ", ".join(f"{year}: {_format_percent(result)}" for year, result in _yearly_performance(curves[name]))
        held_out = leave_out.loc[leave_out["spec_name"] == name, ["excluded_year", "rank"]]
        ranks = ", ".join(f"{int(item.excluded_year)}: {int(item.rank)}" for item in held_out.itertuples(index=False))
        contribution = row.get("annual_contributions", {})
        contribution_text = ", ".join(f"{year}: {_format_percent(value)}" for year, value in contribution.items()) or "none"
        lines.extend([
            f"### `{name}`",
            "",
            f"Yearly equity returns: {yearly}.",
            f"Completed-trade yearly contributions: {contribution_text}.",
            f"Leave-one-year-out ranks: {ranks}.",
            f"Neighbor stability: {'yes' if bool(row['neighbor_stable']) else 'no'}; sensitivity cost ({args.sensitivity_cost:.2%}) total return: {_format_percent(row['sensitivity_total_return'])}.",
            "",
        ])
    if selected.empty:
        lines.append("No qualified rule is available for yearly, leave-one-year-out, neighbor-stability, or sensitivity-cost comparison.")
        lines.append("")
    lines.extend([
        "## Scope, data, and definitions",
        "",
        f"Sources are `{metadata['data_sources']['index']['path']}` and `{metadata['data_sources']['breadth']['path']}`. Emotion is `advancers / (advancers + decliners) * 100`; its threshold is below 15. MA250 is a close-based 250-session filter. KDJ first calculates 9-session RSV as `100 * (close - lowest low) / (highest high - lowest low)`, then applies causal Chinese SMA(3,1) to K and again to D, each initialized at 50; J is `3K - 2D`, and a dead cross is `K < D` after `K >= D` on the preceding session. ATR14 is the simple 14-session average of true range, where true range is the maximum of high-low, absolute high minus prior close, and absolute low minus prior close.",
        "",
        "## Methodology and no-lookahead controls",
        "",
        "Each rule uses one position at a time. Entry conditions are evaluated after the signal-day close and filled at the next trading-day open. Conditional exits are confirmed at the close and filled at the next trading-day open; the pre-defined maximum-hold exit is filled at that same close. New entries are ignored while entry or exit is pending or a position is held. The leave-one-year-out exercise re-runs exactly the same selected specifications after each omitted year; it does not retune parameters.",
        "",
        "## Limitations",
        "",
        "Close-confirmed next-open fills can differ from achievable execution. same-close time exits are a modelling convention. The CYB index is a non-tradable proxy; slippage, market impact, and taxes are omitted. Breadth history is stitched across sources. The sample is small relative to the candidate grid, and multiple testing can overstate apparent performance. These results provide no guarantee of future performance.",
        "",
        "## Monitoring and next steps",
        "",
        "Keep this research bundle separate from the daily signal monitor. Before any practical use, review fresh data integrity, execution assumptions, costs, out-of-sample evidence, and the question of whether an investable proxy changes the result.",
        "",
        "## Further questions",
        "",
        "Would the conclusion persist with an investable proxy and observed bid-ask/impact costs? How sensitive is it to an independently sourced breadth history and to a fully out-of-sample rule lock? Which execution policy is appropriate for the final open position at a research-window boundary?",
        "",
    ])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, object]:
    specs = build_exit_specs()
    if args.limit_specs is not None:
        specs = specs[:args.limit_specs]
    if not specs:
        raise ValueError("limit-specs selected no candidate rules")
    config = SimpleNamespace(primary_cost=args.primary_cost, sensitivity_cost=args.sensitivity_cost)
    frame = build_research_frame(INDEX_CSV, BREADTH_CSV, args.start, args.end)
    summary, trades = _run_selected_grid(frame, specs, args.primary_cost, args.sensitivity_cost)
    baseline_spec = next(spec for spec in build_exit_specs() if spec.name == "time_hold_9")
    baseline_daily, baseline_trades = simulate_exit(frame, baseline_spec, args.primary_cost)
    baseline = summarize_run(baseline_daily, baseline_trades, baseline_spec, args.primary_cost)
    baseline["completed_trade_return"] = math.prod(
        1.0 + float(value) for value in baseline_trades["net_return"]
    ) - 1.0
    baseline["final_position_open"] = bool(int(baseline_daily.iloc[-1]["position"]))
    qualified = rank_candidates(summary, trades, config, baseline_daily=baseline_daily)
    sensitivity = summary.loc[summary["cost"] == args.sensitivity_cost, ["spec_name", "total_return"]].rename(columns={"total_return": "sensitivity_total_return"})
    qualified_for_report = qualified.merge(sensitivity, on="spec_name", how="left", validate="one_to_one")
    qualified_for_output = qualified_for_report.copy()
    qualified_for_output["annual_contributions"] = qualified_for_output["annual_contributions"].map(lambda value: json.dumps(value, sort_keys=True) if isinstance(value, dict) else "{}")
    qualified_for_output = qualified_for_output.reindex(
        columns=[
            *[column for column in qualified.columns if column != "rank"],
            "sensitivity_total_return",
            "rank",
        ]
    )
    leave_out = leave_one_year_out(frame, specs, config)

    selected_names = qualified["spec_name"].head(3).tolist()
    curves: dict[str, pd.DataFrame] = {"time_hold_9 (baseline)": baseline_daily}
    specs_by_name = {spec.name: spec for spec in specs}
    for name in selected_names:
        daily, _ = simulate_exit(frame, specs_by_name[name], args.primary_cost)
        curves[name] = daily

    output_dir = args.output_dir.resolve()
    metadata = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "start": str(pd.Timestamp(args.start).date()), "end": str(pd.Timestamp(args.end).date()),
        "primary_cost": args.primary_cost, "sensitivity_cost": args.sensitivity_cost,
        "entry_definition": "emotion < 15 and signal-day close >= MA250; entry next trading-day open",
        "emotion_formula": "advancers / (advancers + decliners) * 100", "emotion_threshold": 15.0,
        "ma_filter": "close >= MA250", "candidate_count": len(specs),
        "qualification_convention": "full daily marked-to-market equity return",
        "baseline": {
            "spec_name": "time_hold_9", "full_daily_total_return": baseline["total_return"],
            "completed_trade_return": baseline["completed_trade_return"],
            "final_position_open": baseline["final_position_open"],
        },
        "data_sources": {"index": _source_identity(INDEX_CSV), "breadth": _source_identity(BREADTH_CSV)},
        "script_identity": {"path": "scripts/research_cyb_exit_rules.py", "module": "ai_invest_advisor.cyb_exit_research"},
        "execution_conventions": {
            "conditional_exit": "close-confirmed, next trading-day open fill",
            "time_exit": "max-hold exit at the same close", "no_lookahead": "indicators are chronological; decisions only use that close or earlier information",
        },
    }
    paths = {name: output_dir / filename for name, filename in {
        "rule_summary": "rule_summary.csv", "rule_trades": "rule_trades.csv", "qualified_rules": "qualified_rules.csv",
        "leave_one_year_out": "leave_one_year_out.csv", "report": "report.md", "equity_comparison": "equity_comparison.png", "metadata": "metadata.json",
    }.items()}
    _atomic_csv(summary, paths["rule_summary"])
    _atomic_csv(trades, paths["rule_trades"])
    _atomic_csv(qualified_for_output, paths["qualified_rules"])
    _atomic_csv(leave_out, paths["leave_one_year_out"])
    _render_figure(list(curves.items()), paths["equity_comparison"], f"{args.start} to {args.end}", args.primary_cost)
    _atomic_text(paths["report"], _render_report(args=args, baseline=baseline, qualified=qualified_for_report, leave_out=leave_out, curves={name: curves[name] for name in selected_names}, metadata=metadata))
    _atomic_text(paths["metadata"], json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {name: str(path) for name, path in paths.items()} | {"summary_rows": len(summary), "trade_rows": len(trades), "qualified_rows": len(qualified_for_output), "leave_one_year_out_rows": len(leave_out)}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
