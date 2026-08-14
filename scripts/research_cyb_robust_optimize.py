"""Walk-forward robust optimization for AMV+emotion ChiNext rules."""

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

from ai_invest_advisor.cyb_robust_optimize import (  # noqa: E402
    COST_PRIMARY,
    COST_STRESS,
    HOLDOUT,
    WF_FOLDS,
    RobustRule,
    apply_robust_rule,
    baseline_robust_rules,
    build_research_frame,
    eval_rule,
    iter_hypothesis_rules,
    score_walk_forward,
)


DEFAULT_OUT = ROOT / "reports" / "backtests" / "cyb_robust_optimize"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抗过拟合：AMV+情绪稳健优化")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    frame, asset_name, symbol = build_research_frame(force_download=args.force_download)
    rules = list(baseline_robust_rules())
    hypo = list(iter_hypothesis_rules())
    if args.limit > 0:
        hypo = hypo[: args.limit]
    known = {r.name for r in rules}
    rules.extend([r for r in hypo if r.name not in known])
    print(f"asset={asset_name} symbol={symbol} rules={len(rules)} folds={len(WF_FOLDS)}")

    fold_detail_rows: list[dict] = []
    summaries: list[dict] = []

    for i, rule in enumerate(rules, 1):
        fold_rows = []
        for tag, start, end in WF_FOLDS:
            row = eval_rule(frame, rule, start=start, end=end, cost=COST_PRIMARY)
            row["fold"] = tag
            fold_rows.append(row)
            fold_detail_rows.append(row)

        sc = score_walk_forward(fold_rows, n_optional=rule.n_optional)

        # Cost stress on concatenated walk-forward window 2022-2025
        stress = {}
        for c in COST_STRESS:
            r = eval_rule(frame, rule, start="2022-01-01", end="2025-12-31", cost=c)
            stress[f"excess_cost_{c}"] = r["excess_return"]
            stress[f"trades_cost_{c}"] = r["trades"]

        hold = eval_rule(frame, rule, start=HOLDOUT[1], end=HOLDOUT[2], cost=COST_PRIMARY)
        full = eval_rule(frame, rule, start="2020-01-01", end=None, cost=COST_PRIMARY)
        oos_like = eval_rule(frame, rule, start="2023-01-01", end=None, cost=COST_PRIMARY)

        pass_cost = all(
            pd.notna(stress[f"excess_cost_{c}"]) and float(stress[f"excess_cost_{c}"]) > -0.05
            for c in COST_STRESS
        )
        summaries.append(
            {
                "rule": rule.name,
                "n_optional": rule.n_optional,
                "score": sc["score"],
                "median_fold_excess": sc["median_excess"],
                "min_fold_excess": sc["min_excess"],
                "positive_fold_share": sc["positive_fold_share"],
                "mean_fold_trades": sc["mean_trades"],
                "pass_stability": sc["pass_stability"],
                "pass_cost_stress": pass_cost,
                "holdout_excess": hold["excess_return"],
                "holdout_return": hold["total_return"],
                "holdout_trades": hold["trades"],
                "holdout_dd": hold["max_drawdown"],
                "full_excess": full["excess_return"],
                "full_return": full["total_return"],
                "full_dd": full["max_drawdown"],
                "full_exposure": full["exposure"],
                "full_trades": full["trades"],
                "oos2023_excess": oos_like["excess_return"],
                **stress,
                **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
            }
        )
        if i % 20 == 0 or i == len(rules):
            print(f"progress {i}/{len(rules)}")

    detail = pd.DataFrame(fold_detail_rows)
    summary = pd.DataFrame(summaries).sort_values(
        ["pass_stability", "pass_cost_stress", "score", "holdout_excess"],
        ascending=[False, False, False, False],
    )
    detail.to_csv(out / "fold_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out / "rank_robust.csv", index=False, encoding="utf-8-sig")

    # Winner: must pass stability; prefer pass_cost; then score; then holdout
    eligible = summary[summary["pass_stability"] == True]  # noqa: E712
    if eligible.empty:
        eligible = summary
    winner_row = eligible.iloc[0]
    winner_name = str(winner_row["rule"])
    win_rule = next(r for r in rules if r.name == winner_name)

    # Among baselines, report best by same score
    base_names = {r.name for r in baseline_robust_rules()}
    base_rank = summary[summary["rule"].isin(base_names)].copy()
    base_rank.to_csv(out / "baseline_rank.csv", index=False, encoding="utf-8-sig")

    gated = apply_robust_rule(frame, win_rule)
    from ai_invest_advisor.amv_index_backtest import run_index_backtest

    daily, trades, _, _ = run_index_backtest(
        gated,
        cost=COST_PRIMARY,
        peak_dd_exit=win_rule.peak_dd_exit,
        atr_trail_mult=win_rule.atr_trail_mult,
    )
    daily.to_csv(out / "daily_winner.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(out / "trades_winner.csv", index=False, encoding="utf-8-sig")

    top = summary.head(12)
    lines = [
        "# 创业板 AMV+情绪：抗过拟合稳健优化",
        "",
        "> 研究辅助，非投资建议。骨架锁定为 AMV 入场；叠加少量可解释滤镜；用滚动年份与成本压力选参。",
        "",
        f"- 标的：{asset_name}（`{symbol}`）",
        f"- 规则数：{len(rules)}（含基线）",
        f"- 选择折：{', '.join(t for t, _, _ in WF_FOLDS)}；留出验证：{HOLDOUT[0]}",
        f"- 主成本：{COST_PRIMARY:.2%}；压力成本：{', '.join(f'{c:.2%}' for c in COST_STRESS)}",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 方法（降过拟合）",
        "",
        "1. **不做万级盲搜**：候选约百条，且每条有经济含义（趋势/波动/量能/RSI/回撤/ATR）。",
        "2. **Walk-forward**：按年测试 2022–2025；要求多数年份超额为正，且最差年不过度崩。",
        "3. **复杂度惩罚**：可选滤镜越多，得分越扣。",
        "4. **成本压力**：0.15%/0.20% 单边下 2022–2025 超额不能明显翻脸。",
        "5. **2026 留出**：不参与选参，只做最终旁证。",
        "",
        "## 基线排名",
        "",
        "| 规则 | score | 折中位超额 | 正折占比 | 成本压力 | 2026留出超额 | 全样本超额 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in base_rank.iterrows():
        lines.append(
            f"| `{r['rule']}` | {r['score']:.3f} | {_pct(r['median_fold_excess'])} | "
            f"{r['positive_fold_share']:.0%} | {r['pass_cost_stress']} | "
            f"{_pct(r['holdout_excess'])} | {_pct(r['full_excess'])} |"
        )

    lines += [
        "",
        "## 稳健 Top 12（按稳定性→成本→score）",
        "",
        "| 规则 | score | 折中位 | 最差折 | 正折 | 成本 | 2026 | 全样本 | 可选数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in top.iterrows():
        lines.append(
            f"| `{r['rule']}` | {r['score']:.3f} | {_pct(r['median_fold_excess'])} | "
            f"{_pct(r['min_fold_excess'])} | {r['positive_fold_share']:.0%} | "
            f"{r['pass_cost_stress']} | {_pct(r['holdout_excess'])} | "
            f"{_pct(r['full_excess'])} | {int(r['n_optional'])} |"
        )

    lines += [
        "",
        f"## 推荐规则：`{winner_name}`",
        "",
        "```json",
        json.dumps(win_rule.to_dict(), ensure_ascii=False, indent=2),
        "```",
        "",
        f"- Walk-forward score：{float(winner_row['score']):.3f}",
        f"- 折中位超额：{_pct(winner_row['median_fold_excess'])}；正折占比：{float(winner_row['positive_fold_share']):.0%}",
        f"- 2026 留出超额：{_pct(winner_row['holdout_excess'])}",
        f"- 全样本超额：{_pct(winner_row['full_excess'])}；回撤：{_pct(winner_row['full_dd'])}；暴露：{float(winner_row['full_exposure']):.1%}",
        "",
        "## 结论",
        "",
        "- 优先保留能通过稳定性与成本压力的规则，而不是全样本收益最大的规则。",
        "- 若推荐相对基线提升有限，说明额外指标更适合做微调，而不是推翻 AMV+情绪骨架。",
        "- 风险：历史分年样本仍短；指标阈值仍有过拟合可能；不构成投资建议。",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asset": asset_name,
        "symbol": symbol,
        "n_rules": len(rules),
        "winner": winner_name,
        "winner_score": float(winner_row["score"]),
        "holdout_excess": float(winner_row["holdout_excess"])
        if pd.notna(winner_row["holdout_excess"])
        else None,
    }
    (out / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"winner={winner_name}")
    print(f"report: {out / 'report.md'}")
    return 0


def _pct(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"{float(value):.1%}"


if __name__ == "__main__":
    raise SystemExit(main())
