"""Explore 5-unit ChiNext position sizing vs binary 0AMV full-in/full-out."""
from __future__ import annotations

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
from ai_invest_advisor.amv_tranche import (  # noqa: E402
    N_UNITS,
    binary_targets,
    mean_sleeve_equity,
    ramp_targets,
    run_target_backtest,
    step_targets,
    summarize_daily,
)
from ai_invest_advisor.cyb_emotion_amv_combo import CombinedRule, apply_combined_rule  # noqa: E402
from ai_invest_advisor.cyb_robust_optimize import HOLDOUT, WF_FOLDS, score_walk_forward  # noqa: E402
from ai_invest_advisor.sector_etf_rotation import window_from_equity  # noqa: E402
from research_amv_sector_etfs import RULE, START, build_frame, monthly_last, research_end  # noqa: E402
from ai_invest_advisor.sector_etf_universe import SECTOR_ETF_UNIVERSE  # noqa: E402

OUT = ROOT / "reports" / "backtests" / "amv_five_tranche"
COST = 0.001
EMOTION_SLEEVES = (50.0, 55.0, 60.0, 65.0, 70.0)


def delay_signals(frame: pd.DataFrame, days: int) -> pd.DataFrame:
    out = frame.copy()
    if days <= 0:
        return out
    out["entry_signal"] = out["entry_signal"].shift(days).fillna(False).astype(bool)
    out["exit_signal"] = out["exit_signal"].shift(days).fillna(False).astype(bool)
    return out


def yearly(daily: pd.DataFrame, bench: pd.DataFrame, start: str, end: str | None) -> dict:
    rot = window_from_equity(daily, start=start, end=end)
    base = window_from_equity(bench, start=start, end=end)
    if rot["skipped"] or base["skipped"]:
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
        "benchmark_total_return": base["total_return"],
        "excess_return": rot["total_return"] - base["total_return"],
        "max_drawdown": rot["max_drawdown"],
        "trades": rot["trades"],
        "skipped": False,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    end = research_end()
    cyb_item = next(x for x in SECTOR_ETF_UNIVERSE if x.spec.code == "159915")
    raw = build_frame(cyb_item.spec, force=False, end=end)
    frame = apply_combined_rule(raw, RULE)
    print("bars", len(frame), "end", end, flush=True)

    binary_bt, _tr, _bh, binary_sum = run_index_backtest(frame, cost=COST)
    binary_bt = binary_bt.copy()
    binary_bt["traded"] = binary_bt["action"].isin(
        {"schedule_entry", "entry", "exit", "schedule_exit"}
    ).astype(int)
    binary_bt["weight"] = binary_bt["position"].astype(float)
    binary_ref = run_target_backtest(frame, binary_targets(frame), cost=COST)

    specs = []

    def add(name, result, n_optional, note):
        specs.append((name, result, n_optional, note))

    add("binary_full", binary_ref, 0, "当前：0AMV 满进满出")
    add("ramp_1_of_5", run_target_backtest(frame, ramp_targets(frame), cost=COST), 0, "同一开关，每天只加减 1/5")
    add("step_signal", run_target_backtest(frame, step_targets(frame), cost=COST), 0, "每个入场信号加 1/5，每个离场信号减 1/5")

    emo_dailies = []
    for emo in EMOTION_SLEEVES:
        rule = CombinedRule(
            name=f"emo{int(emo)}",
            entry_mode="amv",
            emotion_entry_max=None,
            j_entry_max=None,
            amv_entry_two_day=0.03,
            exit_mode="emotion",
            amv_exit_threshold=None,
            emotion_exit_min=emo,
            exit_ignore_if_above_ma=60,
            min_hold_days=0,
        )
        gated = apply_combined_rule(raw, rule)
        one = run_target_backtest(gated, binary_targets(gated), cost=COST)
        emo_dailies.append(one["daily"])
    emo_mean = mean_sleeve_equity(emo_dailies)
    add(
        "sleeves_emo_50_70",
        summarize_daily(emo_mean, fills=int(emo_mean["traded_units"].gt(0).sum())),
        1,
        "五份独立账户：离场线 50/55/60/65/70",
    )

    delay_dailies = []
    for lag in range(5):
        delayed = delay_signals(frame, lag)
        one = run_target_backtest(delayed, binary_targets(delayed), cost=COST)
        delay_dailies.append(one["daily"])
    delay_mean = mean_sleeve_equity(delay_dailies)
    add(
        "sleeves_delay_0_4",
        summarize_daily(delay_mean, fills=int(delay_mean["traded_units"].gt(0).sum())),
        1,
        "五份独立账户：同一规则，入场/离场分别延迟 0–4 日",
    )

    hold = raw.copy()
    hold["equity"] = hold["close"] / float(hold["close"].iloc[0])
    hold["traded"] = 0

    rows = []
    fold_rows = []
    paths = {}
    for name, result, n_optional, note in specs:
        daily = result["daily"].copy()
        if "traded" not in daily.columns:
            daily["traded"] = (daily["traded_units"] > 0).astype(int)
        daily.to_csv(OUT / f"{name}_daily.csv", index=False, encoding="utf-8-sig")
        year_rows = []
        for tag, start, stop in WF_FOLDS:
            yr = yearly(daily, binary_ref["daily"].assign(traded=(binary_ref["daily"]["traded_units"] > 0).astype(int)), start, stop)
            yr.update({"rule": name, "fold": tag, "n_optional": n_optional})
            year_rows.append(yr)
            fold_rows.append(yr)
        sc = score_walk_forward(year_rows, n_optional=n_optional)
        holdout = yearly(daily, binary_ref["daily"].assign(traded=(binary_ref["daily"]["traded_units"] > 0).astype(int)), HOLDOUT[1], str(end))
        vs_bh = yearly(daily, hold, "2020-01-02", str(end))
        cats, eq = monthly_last(daily)
        rows.append(
            {
                "rule": name,
                "note": note,
                "n_optional": n_optional,
                "full_return": result["total_return"],
                "full_dd": result["max_drawdown"],
                "sharpe": result["sharpe"],
                "exposure": result["exposure"],
                "fills": result["fills"],
                "last_units": result["last_units"],
                "last_weight": result["last_weight"],
                "excess_vs_binary": result["total_return"] - binary_ref["total_return"],
                "excess_vs_hold": vs_bh["excess_return"],
                "median_fold_excess": sc["median_excess"],
                "min_fold_excess": sc["min_excess"],
                "positive_fold_share": sc["positive_fold_share"],
                "pass_stability": sc["pass_stability"],
                "score": sc["score"],
                "holdout_excess": holdout["excess_return"],
                "holdout_return": holdout["total_return"],
            }
        )
        paths[name] = {"categories": cats, "equity": eq, **{k: v for k, v in result.items() if k != "daily"}}
        print(
            f"{name}: ret {result['total_return']*100:.1f}% dd {result['max_drawdown']*100:.1f}% "
            f"exp {result['exposure']*100:.0f}% units {result['last_units']}/5 "
            f"vs_bin {(result['total_return']-binary_ref['total_return'])*100:.1f}ppt "
            f"WF {sc['median_excess'] if pd.notna(sc['median_excess']) else float('nan'):.3f} stab={sc['pass_stability']}",
            flush=True,
        )

    table = pd.DataFrame(rows)
    table.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(fold_rows).to_csv(OUT / "fold_detail.csv", index=False, encoding="utf-8-sig")
    _, cyb_eq = monthly_last(binary_ref["daily"])
    _, hold_eq = monthly_last(hold)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "end": str(end),
        "n_units": N_UNITS,
        "binary_official": {
            "total_return": float(binary_sum["total_return"]),
            "max_drawdown": float(binary_sum["max_drawdown"]),
            "trades": int(binary_sum["trades"]),
            "exposure": float(binary_sum["exposure"]),
        },
        "rows": table.to_dict(orient="records"),
        "monthly": {
            name: paths[name]["categories"] if name == "binary_full" else paths[name]["equity"]
            for name in paths
        },
        "monthly_categories": paths["binary_full"]["categories"],
        "monthly_equity": {name: paths[name]["equity"] for name in paths},
        "monthly_binary": cyb_eq,
        "monthly_hold": hold_eq,
        "fold_detail": fold_rows,
    }
    # fix monthly dump
    payload["monthly"] = {
        "categories": paths["binary_full"]["categories"],
        "equity": {name: paths[name]["equity"] for name in paths},
        "hold": hold_eq,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    lines = [
        "# 创业板 0AMV 五份仓位",
        "",
        f"- 窗口：{START} 至 {end}",
        "- 标的：159915；骨架仍是 amv_emo70_ma60",
        "- 每份 20%；成交：收盘确认、次日开盘；成本按成交仓位计（满仓来回仍约 20bp）",
        "",
        "| 做法 | 累计 | 回撤 | 暴露 | 相对满仓 | 折中位超额 | 稳定 | 当前份数 |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in table.to_dict(orient="records"):
        lines.append(
            f"| {row['rule']} | {row['full_return']*100:.1f}% | {row['full_dd']*100:.1f}% | "
            f"{row['exposure']*100:.0f}% | {row['excess_vs_binary']*100:.1f}ppt | "
            f"{(row['median_fold_excess']*100 if pd.notna(row['median_fold_excess']) else float('nan')):.1f}ppt | "
            f"{row['pass_stability']} | {row['last_units']:.1f} |"
        )
    lines += ["", "研究支持，不是投资建议。", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(table[["rule", "full_return", "full_dd", "exposure", "excess_vs_binary", "pass_stability", "last_units"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
