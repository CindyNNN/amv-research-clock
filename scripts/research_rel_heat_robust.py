"""Walk-forward robust optimization for relative crowding-heat ChiNext timing."""
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
from ai_invest_advisor.cyb_robust_optimize import (  # noqa: E402
    COST_PRIMARY,
    COST_STRESS,
    HOLDOUT,
    WF_FOLDS,
    score_walk_forward,
)
from ai_invest_advisor.rel_heat_strategy import (  # noqa: E402
    RelHeatRule,
    apply_rel_heat_rule,
    baseline_rel_heat_rules,
    enrich_rel_heat,
    iter_rel_heat_hypotheses,
)

DEFAULT_OUT = ROOT / "reports" / "backtests" / "rel_heat_robust"
CROWDING = ROOT / "reports" / "backtests" / "bagholder_crowding_cyb" / "daily_crowding.csv"
ETF = ROOT / "data" / "backtests" / "amv_index_gate" / "index_sz159915_daily.csv"
AMV_EQ = ROOT / "reports" / "backtests" / "cyb_robust_optimize" / "equity_top3_daily.csv"


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if start:
        data = data[data["date"] >= pd.Timestamp(start)]
    if end:
        data = data[data["date"] <= pd.Timestamp(end)]
    return data.reset_index(drop=True)


def eval_rule(
    frame: pd.DataFrame,
    rule: RelHeatRule,
    *,
    start: str | None,
    end: str | None,
    cost: float = COST_PRIMARY,
) -> dict:
    gated = apply_rel_heat_rule(_slice(frame, start, end), rule)
    heat_ok = gated["rel_heat"].notna()
    if gated.empty or not heat_ok.any():
        return {
            "rule": rule.name,
            "total_return": 0.0,
            "benchmark_total_return": float("nan"),
            "excess_return": float("nan"),
            "max_drawdown": 0.0,
            "exposure": 0.0,
            "trades": 0,
            "sharpe": 0.0,
            "skipped": True,
            "n_optional": rule.n_optional,
        }
    live = gated.loc[heat_ok].reset_index(drop=True)
    if not live["entry_signal"].any() and not live["exit_signal"].any():
        daily, trades, _bench, summary = run_index_backtest(
            live, cost=cost, peak_dd_exit=rule.peak_dd_exit
        )
    else:
        daily, trades, _bench, summary = run_index_backtest(
            live, cost=cost, peak_dd_exit=rule.peak_dd_exit
        )
    excess = float(summary["total_return"]) - float(summary["benchmark_total_return"])
    return {
        "rule": rule.name,
        "total_return": float(summary["total_return"]),
        "benchmark_total_return": float(summary["benchmark_total_return"]),
        "excess_return": excess,
        "max_drawdown": float(summary["max_drawdown"]),
        "exposure": float(summary["exposure"]),
        "trades": int(summary["trades"]),
        "sharpe": float(summary["sharpe"]),
        "annualized_return": float(summary["annualized_return"]),
        "skipped": False,
        "n_optional": rule.n_optional,
    }


def load_frame() -> pd.DataFrame:
    etf = pd.read_csv(ETF, parse_dates=["date"])
    crowd = pd.read_csv(CROWDING, parse_dates=["date"])
    merged = etf.merge(crowd[["date", "bh_close", "bh_ret20"]], on="date", how="inner")
    return enrich_rel_heat(merged)


def monthly_last(daily: pd.DataFrame, col: str = "equity") -> tuple[list[str], list[float]]:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["ym"] = frame["date"].dt.to_period("M").astype(str)
    last = frame.groupby("ym", sort=True).tail(1)
    return last["ym"].tolist(), [round(float(x), 4) for x in last[col].tolist()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="相对拥挤热度：走样本外稳健优化")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    frame = load_frame()
    rules = list(baseline_rel_heat_rules())
    known = {r.name for r in rules}
    rules.extend([r for r in iter_rel_heat_hypotheses() if r.name not in known])
    print(f"rows={len(frame)} rules={len(rules)} folds={len(WF_FOLDS)}")

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
        stress = {}
        for cost in COST_STRESS:
            r = eval_rule(frame, rule, start="2022-01-01", end="2025-12-31", cost=cost)
            stress[f"excess_cost_{cost}"] = r["excess_return"]
        hold = eval_rule(frame, rule, start=HOLDOUT[1], end=HOLDOUT[2], cost=COST_PRIMARY)
        full = eval_rule(frame, rule, start="2020-02-01", end=None, cost=COST_PRIMARY)
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
                "full_excess": full["excess_return"],
                "full_return": full["total_return"],
                "full_dd": full["max_drawdown"],
                "full_exposure": full["exposure"],
                "full_trades": full["trades"],
                "full_sharpe": full["sharpe"],
                **stress,
                **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
            }
        )
        if i % 20 == 0 or i == len(rules):
            print(f"progress {i}/{len(rules)}")

    summary = pd.DataFrame(summaries).sort_values(
        ["pass_stability", "pass_cost_stress", "score", "holdout_excess"],
        ascending=[False, False, False, False],
    )
    pd.DataFrame(fold_detail_rows).to_csv(out / "fold_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out / "rank_robust.csv", index=False, encoding="utf-8-sig")

    eligible = summary[summary["pass_stability"] == True]  # noqa: E712
    if eligible.empty:
        pick_from = summary
        pick_note = "no rule passed stability; reporting top score anyway"
    else:
        pick_from = eligible
        pick_note = "winner among pass_stability"
    winner_row = pick_from.iloc[0]
    winner_name = str(winner_row["rule"])
    win_rule = next(r for r in rules if r.name == winner_name)

    # Stable pick: 100% positive folds if any, else winner.
    stable = eligible[eligible["positive_fold_share"] >= 0.999] if not eligible.empty else summary.iloc[0:0]
    if not stable.empty:
        stable_row = stable.sort_values(["pass_cost_stress", "score"], ascending=[False, False]).iloc[0]
    else:
        stable_row = winner_row

    baseline = summary[summary["rule"].isin({r.name for r in baseline_rel_heat_rules()})]
    baseline.to_csv(out / "baseline_rank.csv", index=False, encoding="utf-8-sig")

    def dump_path(rule: RelHeatRule, tag: str) -> dict:
        gated = apply_rel_heat_rule(frame, rule)
        live = gated.dropna(subset=["rel_heat"]).reset_index(drop=True)
        daily, trades, bench, s = run_index_backtest(
            live, cost=COST_PRIMARY, peak_dd_exit=rule.peak_dd_exit
        )
        daily.to_csv(out / f"{tag}_daily.csv", index=False, encoding="utf-8-sig")
        trades.to_csv(out / f"{tag}_trades.csv", index=False, encoding="utf-8-sig")
        cats, eq = monthly_last(daily)
        _, bh = monthly_last(bench)
        last = daily.iloc[-2] if str(daily.iloc[-1].get("action")) == "exit_eod_force" else daily.iloc[-1]
        return {
            "rule": rule.name,
            "params": rule.to_dict(),
            "summary": s,
            "monthly": {"categories": cats, "equity": eq, "buy_hold": bh},
            "position_now": int(last["position"]),
            "action_now": str(last["action"]),
            "last_date": str(pd.Timestamp(last["date"]).date()),
            "last_heat": float(live.iloc[-1]["rel_heat"]),
            "n_trades": int(len(trades)),
        }

    winner_path = dump_path(win_rule, "winner")
    stable_rule = next(r for r in rules if r.name == str(stable_row["rule"]))
    stable_path = dump_path(stable_rule, "stable")
    base_rule = baseline_rel_heat_rules()[0]
    base_path = dump_path(base_rule, "baseline_30_70")

    amv_compare = None
    if AMV_EQ.exists():
        amv = pd.read_csv(AMV_EQ, parse_dates=["date"])
        w = pd.read_csv(out / "winner_daily.csv", parse_dates=["date"])
        merged = w.merge(amv, on="date", how="inner")
        if len(merged) > 20:
            def span(col: str) -> float:
                return float(merged[col].iloc[-1] / merged[col].iloc[0] - 1.0)
            amv_compare = {
                "start": str(merged["date"].iloc[0].date()),
                "end": str(merged["date"].iloc[-1].date()),
                "winner": span("equity"),
                "amv_score": span("①得分冠军 e60|a-3.5%|MA60"),
                "amv_stable": span("②稳健备选 e70|MA60"),
                "etf_hold": span("创业板ETF持有(159915)"),
            }

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_rules": len(rules),
        "pick_note": pick_note,
        "winner": winner_path,
        "stable": stable_path,
        "baseline_30_70": base_path,
        "winner_rank": winner_row.to_dict(),
        "stable_rank": stable_row.to_dict(),
        "amv_overlap": amv_compare,
        "top12": summary.head(12).to_dict(orient="records"),
        "n_pass_stability": int((summary["pass_stability"] == True).sum()),  # noqa: E712
        "n_pass_both": int(
            ((summary["pass_stability"] == True) & (summary["pass_cost_stress"] == True)).sum()  # noqa: E712
        ),
    }
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    lines = [
        "# 相对拥挤热度：走样本外稳健优化",
        "",
        "> 研究辅助，非投资建议。骨架锁定为相对热度（韭菜篮20日收益 − 创业板20日收益的历史分位）；高分视为低质量追逐，用于减仓而不是选股。",
        "",
        f"- 标的：创业板ETF 159915",
        f"- 规则数：{len(rules)}（含 3 条基线）",
        "- 选择折：2022–2025；留出：2026",
        "- 主成本：0.10%；压力：0.15%、0.20%",
        f"- 生成时间：{payload['generated_at']}",
        "",
        "## 结论摘要",
        "",
        f"- 通过稳定性门槛的规则：{payload['n_pass_stability']} / {len(rules)}",
        f"- 稳定性 + 成本压力都过：{payload['n_pass_both']}",
        f"- 得分冠军：`{winner_name}`",
        f"- 更稳备选：`{stable_row['rule']}`",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("winner", winner_name, "score", round(float(winner_row["score"]), 3))
    print("stable", stable_row["rule"], "pos", stable_row["positive_fold_share"])
    print("pass_stab", payload["n_pass_stability"], "pass_both", payload["n_pass_both"])
    if amv_compare:
        print("amv overlap", amv_compare)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
