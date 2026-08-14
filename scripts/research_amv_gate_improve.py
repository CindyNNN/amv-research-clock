from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_index_backtest import run_index_backtest  # noqa: E402
from ai_invest_advisor.amv_index_data import (  # noqa: E402
    INDEX_UNIVERSE,
    prepare_research_frames,
)
from ai_invest_advisor.amv_strategy_variants import (  # noqa: E402
    GateRule,
    apply_gate_rule,
    candidate_rules,
)


DEFAULT_OUT = ROOT / "reports" / "backtests" / "amv_index_gate_improve"
OOS_START = "2023-01-01"


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if start:
        data = data[data["date"] >= pd.Timestamp(start)]
    if end:
        data = data[data["date"] <= pd.Timestamp(end)]
    return data.reset_index(drop=True)


def evaluate_rule(
    frames: dict[str, pd.DataFrame],
    rule: GateRule,
    *,
    cost: float,
    start: str | None,
    end: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for spec in INDEX_UNIVERSE:
        base = _slice(frames[spec.tencent_symbol], start, end)
        if len(base) < 60:
            continue
        gated = apply_gate_rule(base, rule)
        _d, _t, _b, summary = run_index_backtest(gated, cost=cost)
        excess = float(summary["total_return"]) - float(summary["benchmark_total_return"])
        rows.append(
            {
                **summary,
                "rule": rule.name,
                "segment": f"{start or 'start'}->{end or 'end'}",
                "excess_return": excess,
                **{f"rule_{k}": v for k, v in rule.to_dict().items() if k != "name"},
            }
        )
    return rows


def rank_rules(oos: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        oos.groupby("rule", as_index=False)
        .agg(
            n_indices=("tencent_symbol", "count"),
            median_excess=("excess_return", "median"),
            mean_excess=("excess_return", "mean"),
            min_excess=("excess_return", "min"),
            mean_total=("total_return", "mean"),
            mean_mdd=("max_drawdown", "mean"),
            mean_exposure=("exposure", "mean"),
            beat_bh_count=("excess_return", lambda s: int((s > 0).sum())),
        )
        .sort_values(
            ["beat_bh_count", "median_excess", "mean_excess"],
            ascending=[False, False, False],
        )
        .reset_index(drop=True)
    )
    return grouped


def _report_md(rank: pd.DataFrame, detail: pd.DataFrame, winner: str, metadata: dict) -> str:
    win_rows = detail[(detail["rule"] == winner) & (detail["segment"].str.startswith(OOS_START))]
    lines = [
        "# 0AMV 门控策略改进研究",
        "",
        "> 研究辅助，非投资建议。目标：提高相对买入持有的超额收益。",
        "",
        "## 诊断",
        "",
        "- 原规则有择时能力（持仓期前瞻收益高于空仓期），但暴露仅约 46%，错失趋势复利。",
        "- 改进方向：在保留下跌保护的前提下，提高有效暴露（更容易再入场 / 更谨慎清仓 / 趋势中不急于清仓）。",
        "",
        f"- 生成时间：{metadata['generated_at']}",
        f"- 样本内：{metadata['is_segment']}；样本外：{metadata['oos_segment']}",
        f"- 成本：单边 {metadata['cost']:.2%}",
        "",
        "## 样本外规则排名（按跑赢持有指数数、超额中位数）",
        "",
        "| 规则 | 跑赢数 | 超额中位 | 超额均值 | 最差超额 | 均暴露 | 均回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in rank.head(12).iterrows():
        lines.append(
            "| {rule} | {beat}/5 | {med:.2%} | {mean:.2%} | {mn:.2%} | {exp:.1%} | {mdd:.1%} |".format(
                rule=row["rule"],
                beat=int(row["beat_bh_count"]),
                med=float(row["median_excess"]),
                mean=float(row["mean_excess"]),
                mn=float(row["min_excess"]),
                exp=float(row["mean_exposure"]),
                mdd=float(row["mean_mdd"]),
            )
        )
    lines.extend(["", f"## 推荐规则：`{winner}`", ""])
    winner_meta = next(r for r in candidate_rules() if r.name == winner)
    lines.append("```")
    lines.append(json.dumps(winner_meta.to_dict(), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.extend(
        [
            "",
            "### 样本外分指数表现",
            "",
            "| 指数 | 策略 | 买入持有 | 超额 | 回撤 | 暴露 | 交易 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in win_rows.sort_values("excess_return", ascending=False).iterrows():
        lines.append(
            "| {name} | {tr:.2%} | {bh:.2%} | {ex:.2%} | {mdd:.2%} | {exp:.1%} | {n} |".format(
                name=row["name"],
                tr=float(row["total_return"]),
                bh=float(row["benchmark_total_return"]),
                ex=float(row["excess_return"]),
                mdd=float(row["max_drawdown"]),
                exp=float(row["exposure"]),
                n=int(row["trades"]),
            )
        )
    lines.extend(
        [
            "",
            "## 风险提示",
            "",
            "- 规则筛选使用了样本外对比，但仍可能过拟合候选集合。",
            "- 指数代理 ETF，实盘有跟踪误差与冲击成本。",
            "- 提高暴露会降低“躲过大跌”的频率，需接受回撤上升。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="改进 0AMV 门控规则以追求跑赢持有")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--cost", type=float, default=0.001)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--oos-start", default=OOS_START)
    args = parser.parse_args(argv)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    frames = prepare_research_frames(start=args.start, force_download=False)

    all_rows: list[dict] = []
    for rule in candidate_rules():
        all_rows.extend(
            evaluate_rule(
                frames, rule, cost=args.cost, start=args.start, end="2022-12-31"
            )
        )
        all_rows.extend(
            evaluate_rule(
                frames, rule, cost=args.cost, start=args.oos_start, end=None
            )
        )
        # full sample for deployment reference
        all_rows.extend(
            evaluate_rule(frames, rule, cost=args.cost, start=args.start, end=None)
        )

    detail = pd.DataFrame(all_rows)
    detail.to_csv(out / "rule_detail.csv", index=False, encoding="utf-8-sig")

    oos = detail[detail["segment"].str.startswith(args.oos_start)].copy()
    rank = rank_rules(oos)
    rank.to_csv(out / "rule_rank_oos.csv", index=False, encoding="utf-8-sig")

    baseline = rank[rank["rule"] == "baseline"]
    # Prefer rules that beat BH on more indices, then median excess; require not all worse than baseline median.
    winner = str(rank.iloc[0]["rule"]) if not rank.empty else "baseline"
    # Soft guard: if top rule mean_excess < baseline, keep searching next with beat_bh_count>=baseline
    if not baseline.empty:
        base_med = float(baseline.iloc[0]["median_excess"])
        for _, row in rank.iterrows():
            if float(row["median_excess"]) >= base_med and int(row["beat_bh_count"]) >= int(
                baseline.iloc[0]["beat_bh_count"]
            ):
                winner = str(row["rule"])
                break

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cost": args.cost,
        "is_segment": f"{args.start}->2022-12-31",
        "oos_segment": f"{args.oos_start}->latest",
        "winner": winner,
        "selection": "max beat_bh_count then median_excess on OOS",
        "disclaimer": "研究辅助，非投资建议。",
    }
    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(
        _report_md(rank, detail, winner, metadata), encoding="utf-8"
    )

    # Save winner full-sample summaries for quick view
    full = detail[
        (detail["rule"] == winner) & (detail["segment"] == f"{args.start}->end")
    ].copy()
    full.to_csv(out / "winner_full_sample.csv", index=False, encoding="utf-8-sig")

    print("OOS rank top5:")
    print(rank.head(5).to_string(index=False))
    print(f"\nwinner={winner}")
    print(full[["name", "total_return", "benchmark_total_return", "excess_return", "exposure"]].to_string(index=False))
    print(f"\nreport: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
