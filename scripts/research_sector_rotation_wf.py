"""Walk-forward robustness for monthly industry-ETF rotation."""
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
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from ai_invest_advisor.amv_index_backtest import run_index_backtest  # noqa: E402
from ai_invest_advisor.cyb_emotion_amv_combo import apply_combined_rule  # noqa: E402
from ai_invest_advisor.cyb_robust_optimize import (  # noqa: E402
    COST_PRIMARY,
    COST_STRESS,
    HOLDOUT,
    WF_FOLDS,
    score_walk_forward,
)
from ai_invest_advisor.sector_etf_rotation import (  # noqa: E402
    RotRule,
    baseline_rot_rules,
    iter_rot_hypotheses,
    rotate,
    window_from_equity,
)
from ai_invest_advisor.sector_etf_universe import SECTOR_ETF_UNIVERSE  # noqa: E402
from research_amv_sector_etfs import (  # noqa: E402
    RULE,
    build_frame,
    monthly_last,
    research_end,
)

OUT = ROOT / "reports" / "backtests" / "amv_sector_rotation_wf"


def yearly_excess(rot_daily: pd.DataFrame, bench_daily: pd.DataFrame, start: str, end: str | None) -> dict:
    rot = window_from_equity(rot_daily, start=start, end=end)
    bench = window_from_equity(
        bench_daily.rename(columns={"traded": "traded_unused"})
        if "traded" in bench_daily.columns
        else bench_daily,
        start=start,
        end=end,
    )
    if rot["skipped"] or bench["skipped"]:
        return {
            "total_return": 0.0,
            "benchmark_total_return": float("nan"),
            "excess_return": float("nan"),
            "max_drawdown": 0.0,
            "trades": 0,
            "skipped": True,
        }
    return {
        "total_return": rot["total_return"],
        "benchmark_total_return": bench["total_return"],
        "excess_return": rot["total_return"] - bench["total_return"],
        "max_drawdown": rot["max_drawdown"],
        "trades": rot["trades"],
        "skipped": False,
        "bars": rot["bars"],
    }


def mark_cyb_trades(daily: pd.DataFrame) -> pd.DataFrame:
    out = daily.copy()
    out["traded"] = out["action"].isin({"schedule_entry", "entry", "exit", "schedule_exit"}).astype(int)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    end = research_end()
    print("window ->", end, flush=True)
    frames: dict[str, pd.DataFrame] = {}
    for item in SECTOR_ETF_UNIVERSE:
        frames[item.spec.code] = build_frame(item.spec, force=args.force_download, end=end)
        print("loaded", item.spec.code, len(frames[item.spec.code]), flush=True)

    cyb_frame = apply_combined_rule(frames["159915"], RULE)
    cyb_by_cost: dict[float, pd.DataFrame] = {}
    for cost in (COST_PRIMARY, *COST_STRESS):
        daily, _trades, _bench, _summary = run_index_backtest(cyb_frame, cost=cost)
        cyb_by_cost[cost] = mark_cyb_trades(daily)
    cyb_primary = cyb_by_cost[COST_PRIMARY]
    cyb_primary.to_csv(OUT / "cyb_gated_daily.csv", index=False, encoding="utf-8-sig")

    hold_cyb = frames["159915"][["date", "close"]].copy()
    hold_cyb["equity"] = hold_cyb["close"] / float(hold_cyb["close"].iloc[0])
    hold_cyb["traded"] = 0

    rules = list(baseline_rot_rules())
    known = {r.name for r in rules}
    rules.extend([r for r in iter_rot_hypotheses() if r.name not in known])
    print("rules", len(rules), flush=True)

    cache: dict[tuple[str, float], dict] = {}
    fold_rows: list[dict] = []
    summaries: list[dict] = []

    for i, rule in enumerate(rules, 1):
        for cost in (COST_PRIMARY, *COST_STRESS):
            cache[(rule.name, cost)] = rotate(
                frames, cyb_by_cost[cost], rule=rule, cost=cost
            )
        sim = cache[(rule.name, COST_PRIMARY)]
        year_rows = []
        for tag, start, stop in WF_FOLDS:
            row = yearly_excess(sim["daily"], cyb_primary, start, stop)
            row.update({"rule": rule.name, "fold": tag, "n_optional": rule.n_optional})
            year_rows.append(row)
            fold_rows.append(row)
        sc = score_walk_forward(year_rows, n_optional=rule.n_optional)
        stress = {}
        for cost in COST_STRESS:
            r = yearly_excess(
                cache[(rule.name, cost)]["daily"],
                cyb_by_cost[cost],
                "2022-01-01",
                "2025-12-31",
            )
            stress[f"excess_cost_{cost}"] = r["excess_return"]
        hold = yearly_excess(sim["daily"], cyb_primary, HOLDOUT[1], str(end))
        full = yearly_excess(sim["daily"], cyb_primary, "2020-01-02", str(end))
        vs_bh = yearly_excess(sim["daily"], hold_cyb, "2020-01-02", str(end))
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
                "holdout_dd": hold["max_drawdown"],
                "full_excess_vs_cyb_gate": full["excess_return"],
                "full_excess_vs_cyb_hold": vs_bh["excess_return"],
                "full_return": sim["total_return"],
                "full_dd": sim["max_drawdown"],
                "full_exposure": sim["exposure"],
                "full_trades": sim["trades"],
                "full_sharpe": sim["sharpe"],
                "last_held": sim["last_held"],
                **stress,
                **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
            }
        )
        print(
            f"{i}/{len(rules)} {rule.name} med_ex={sc['median_excess'] if pd.notna(sc['median_excess']) else float('nan'):.3f} "
            f"pos={sc['positive_fold_share']:.0%} stab={sc['pass_stability']} "
            f"2026ex={hold['excess_return'] if pd.notna(hold['excess_return']) else float('nan'):.3f}",
            flush=True,
        )

    summary = pd.DataFrame(summaries).sort_values(
        ["pass_stability", "pass_cost_stress", "score", "holdout_excess"],
        ascending=[False, False, False, False],
    )
    pd.DataFrame(fold_rows).to_csv(OUT / "fold_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUT / "rank_robust.csv", index=False, encoding="utf-8-sig")

    gated = summary[summary["p_use_gate"] == True]  # noqa: E712
    eligible = gated[gated["pass_stability"] == True]  # noqa: E712
    pick_from = eligible if not eligible.empty else gated
    winner_row = pick_from.iloc[0]
    winner = next(r for r in rules if r.name == winner_row["rule"])
    stable = eligible[eligible["positive_fold_share"] >= 0.999] if not eligible.empty else gated.iloc[0:0]
    if not stable.empty:
        stable_row = stable.sort_values(["pass_cost_stress", "score"], ascending=[False, False]).iloc[0]
        stable_rule = next(r for r in rules if r.name == stable_row["rule"])
    else:
        stable_row = winner_row
        stable_rule = winner

    research = next(r for r in rules if r.name == "m20_k1_raw_gate")
    research_row = summary[summary["rule"] == "m20_k1_raw_gate"].iloc[0]

    def dump(rule: RotRule, tag: str) -> dict:
        sim = cache[(rule.name, COST_PRIMARY)]
        sim["daily"].to_csv(OUT / f"{tag}_daily.csv", index=False, encoding="utf-8-sig")
        cats, eq = monthly_last(sim["daily"])
        _, cyb_eq = monthly_last(cyb_primary)
        _, hold_eq = monthly_last(hold_cyb)
        return {
            "rule": rule.name,
            "params": rule.to_dict(),
            "total_return": sim["total_return"],
            "max_drawdown": sim["max_drawdown"],
            "trades": sim["trades"],
            "sharpe": sim["sharpe"],
            "last_held": sim["last_held"],
            "monthly": {"categories": cats, "equity": eq, "cyb_gated": cyb_eq, "cyb_hold": hold_eq},
        }

    paths = {
        "winner": dump(winner, "winner"),
        "stable": dump(stable_rule, "stable"),
        "research_m20_k1": dump(research, "research_m20_k1"),
    }
    cyb_full = yearly_excess(cyb_primary, hold_cyb, "2020-01-02", str(end))
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "end": str(end),
        "cost_primary": COST_PRIMARY,
        "cost_stress": COST_STRESS,
        "folds": [f[0] for f in WF_FOLDS],
        "holdout": HOLDOUT[0],
        "n_rules": int(len(rules)),
        "benchmark": "ChiNext 159915 amv_emo70_ma60 gated",
        "winner": winner_row.to_dict(),
        "stable": stable_row.to_dict(),
        "research_m20_k1": research_row.to_dict(),
        "cyb_gated_full": cyb_full,
        "paths": paths,
        "top12": gated.head(12).to_dict(orient="records"),
        "fold_table": [
            {
                "rule": r,
                **{
                    row["fold"]: round(float(row["excess_return"]) * 100, 1)
                    if pd.notna(row["excess_return"])
                    else None
                    for row in fold_rows
                    if row["rule"] == r
                },
            }
            for r in [winner.name, stable_rule.name, "m20_k1_raw_gate"]
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
    )

    lines = [
        "# 月频行业 ETF 轮动 Walk-forward",
        "",
        "> 研究辅助，非投资建议。骨架锁定为创业板 `amv_emo70_ma60` 开门再做月频动量；2022–2025 选参，2026 留出。",
        "",
        f"- 窗口：2020-01-02 至 {end}",
        f"- 规则数：{len(rules)}（回看 20/60/120 × Top1/2/3 × 是否跳过负动量 + 无门控对照）",
        "- 超额对照：同一成本下的创业板 ETF 0AMV 门控",
        f"- 主成本 10bp；压力 15bp / 20bp",
        f"- 生成：{payload['generated_at']}",
        "",
        "## 选参结果",
        "",
        f"- 得分冠军：`{winner.name}`，折中位超额 {float(winner_row['median_fold_excess'])*100:.1f}ppt，正折 {float(winner_row['positive_fold_share']):.0%}，稳定性 {bool(winner_row['pass_stability'])}，2026 留出超额 {float(winner_row['holdout_excess'])*100:.1f}ppt",
        f"- 更稳档：`{stable_rule.name}`，正折 {float(stable_row['positive_fold_share']):.0%}，2026 留出超额 {float(stable_row['holdout_excess'])*100:.1f}ppt",
        f"- 研究版 `m20_k1_raw_gate`：折中位超额 {float(research_row['median_fold_excess'])*100:.1f}ppt，正折 {float(research_row['positive_fold_share']):.0%}，稳定性 {bool(research_row['pass_stability'])}，2026 留出超额 {float(research_row['holdout_excess'])*100:.1f}ppt",
        "",
        "## 稳健排名（仅带 0AMV 门控）",
        "",
        "| 规则 | score | 折中位超额 | 最差折 | 正折 | 成本压 | 2026超额 | 全样本 vs 门控 | 全样本累计 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in gated.head(12).to_dict(orient="records"):
        lines.append(
            f"| `{row['rule']}` | {row['score']:.3f} | {row['median_fold_excess']*100:.1f}% | "
            f"{row['min_fold_excess']*100:.1f}% | {row['positive_fold_share']:.0%} | "
            f"{row['pass_cost_stress']} | {row['holdout_excess']*100:.1f}% | "
            f"{row['full_excess_vs_cyb_gate']*100:.1f}ppt | {row['full_return']*100:.1f}% |"
        )
    lines += [
        "",
        "正折占比要求 ≥75% 且最差年超额 > -15% 才算过稳定性。复杂度（非 20 日、非 Top1、跳过负动量）会扣分。",
        "",
        "研究支持，不是投资建议。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(summary[["rule", "score", "median_fold_excess", "positive_fold_share", "pass_stability", "holdout_excess", "full_return"]].head(12).to_string(index=False))
    print("winner", winner.name, "stable", stable_rule.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
