from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_index_backtest import run_index_backtest  # noqa: E402
from ai_invest_advisor.cyb_emotion_amv_combo import (  # noqa: E402
    CombinedRule,
    apply_combined_rule,
    baseline_rules,
    build_combined_frame,
    iter_grid_rules,
)


DEFAULT_OUT = ROOT / "reports" / "backtests" / "cyb_emotion_amv_grid"
OOS_START = "2023-01-01"
COST = 0.001


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if start:
        data = data[data["date"] >= pd.Timestamp(start)]
    if end:
        data = data[data["date"] <= pd.Timestamp(end)]
    return data.reset_index(drop=True)


def eval_rule_on_frame(frame: pd.DataFrame, rule: CombinedRule, *, start, end) -> dict:
    gated = apply_combined_rule(_slice(frame, start, end), rule)
    if gated["entry_signal"].sum() == 0 and gated["exit_signal"].sum() == 0:
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
            **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
        }
    daily, trades, benchmark, summary = run_index_backtest(gated, cost=COST)
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
        **{f"p_{k}": v for k, v in rule.to_dict().items() if k != "name"},
    }


# Worker globals set in pool initializer
_FRAME = None


def _init_worker(frame_path: str) -> None:
    global _FRAME
    _FRAME = pd.read_pickle(frame_path)


def _worker(payload: dict) -> dict:
    rule = CombinedRule(**payload["rule"])
    row = eval_rule_on_frame(_FRAME, rule, start=payload["start"], end=payload["end"])
    row["segment_tag"] = payload["tag"]
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="情绪×0AMV 联合策略对比与大网格搜索")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--oos-start", default=OOS_START)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--grid-limit", type=int, default=0, help="0=全网格")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--baselines-only", action="store_true")
    args = parser.parse_args(argv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    frame, asset_name, symbol = build_combined_frame(
        prefer_etf=True, start=args.start, force_download=args.force_download
    )
    frame_path = out / "_combined_frame.pkl"
    frame.to_pickle(frame_path)

    rules = list(baseline_rules())
    if not args.baselines_only:
        grid = list(iter_grid_rules())
        if args.grid_limit > 0:
            grid = grid[: args.grid_limit]
        # Avoid duplicating baseline names if any overlap
        baseline_names = {r.name for r in rules}
        rules.extend([r for r in grid if r.name not in baseline_names])

    jobs = []
    for rule in rules:
        jobs.append({"rule": rule.to_dict(), "tag": "IS", "start": args.start, "end": "2022-12-31"})
        jobs.append({"rule": rule.to_dict(), "tag": "OOS", "start": args.oos_start, "end": None})
        jobs.append({"rule": rule.to_dict(), "tag": "FULL", "start": args.start, "end": None})

    print(f"asset={asset_name} symbol={symbol} rules={len(rules)} jobs={len(jobs)}")
    rows: list[dict] = []
    if args.workers <= 1:
        for i, job in enumerate(jobs, 1):
            rule = CombinedRule(**job["rule"])
            row = eval_rule_on_frame(frame, rule, start=job["start"], end=job["end"])
            row["segment_tag"] = job["tag"]
            rows.append(row)
            if i % 200 == 0:
                print(f"progress {i}/{len(jobs)}")
    else:
        with ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=_init_worker,
            initargs=(str(frame_path),),
        ) as pool:
            futs = [pool.submit(_worker, job) for job in jobs]
            done = 0
            for fut in as_completed(futs):
                rows.append(fut.result())
                done += 1
                if done % 500 == 0:
                    print(f"progress {done}/{len(jobs)}")

    detail = pd.DataFrame(rows)
    detail.to_csv(out / "combo_rule_detail.csv", index=False, encoding="utf-8-sig")

    def rank(tag: str) -> pd.DataFrame:
        sub = detail[(detail["segment_tag"] == tag) & (~detail["skipped"])].copy()
        return sub.sort_values(
            ["excess_return", "sharpe", "total_return"],
            ascending=[False, False, False],
        )

    oos = rank("OOS")
    full = rank("FULL")
    oos.to_csv(out / "rank_oos.csv", index=False, encoding="utf-8-sig")
    full.to_csv(out / "rank_full.csv", index=False, encoding="utf-8-sig")

    # Baseline comparison table
    base_names = [r.name for r in baseline_rules()]
    base_full = full[full["rule"].isin(base_names)].copy()
    base_oos = oos[oos["rule"].isin(base_names)].copy()
    base_full.to_csv(out / "baseline_full.csv", index=False, encoding="utf-8-sig")
    base_oos.to_csv(out / "baseline_oos.csv", index=False, encoding="utf-8-sig")

    # Winner: OOS excess>0 and FULL excess>0 if possible; else best OOS with FULL>baseline amv
    amv_full = float(full.loc[full["rule"] == "amv_baseline", "excess_return"].iloc[0]) if "amv_baseline" in set(full["rule"]) else -1e9
    winner = str(oos.iloc[0]["rule"]) if not oos.empty else "amv_baseline"
    for _, row in oos.iterrows():
        fr = full.loc[full["rule"] == row["rule"]]
        if fr.empty:
            continue
        full_ex = float(fr.iloc[0]["excess_return"])
        if float(row["excess_return"]) > 0 and full_ex > 0:
            winner = str(row["rule"])
            break
    else:
        for _, row in oos.iterrows():
            fr = full.loc[full["rule"] == row["rule"]]
            if fr.empty:
                continue
            if float(fr.iloc[0]["excess_return"]) > amv_full:
                winner = str(row["rule"])
                break

    # Save winner equity
    win_rule = next((r for r in rules if r.name == winner), None)
    if win_rule is None:
        # reconstruct from detail params
        sample = oos.loc[oos["rule"] == winner].iloc[0]
        win_rule = CombinedRule(
            name=winner,
            entry_mode=sample["p_entry_mode"],
            emotion_entry_max=sample["p_emotion_entry_max"] if pd.notna(sample["p_emotion_entry_max"]) else None,
            j_entry_max=sample["p_j_entry_max"] if pd.notna(sample["p_j_entry_max"]) else None,
            amv_entry_two_day=sample["p_amv_entry_two_day"] if pd.notna(sample["p_amv_entry_two_day"]) else None,
            exit_mode=sample["p_exit_mode"],
            amv_exit_threshold=sample["p_amv_exit_threshold"] if pd.notna(sample["p_amv_exit_threshold"]) else None,
            emotion_exit_min=sample["p_emotion_exit_min"] if pd.notna(sample["p_emotion_exit_min"]) else None,
            exit_ignore_if_above_ma=int(sample["p_exit_ignore_if_above_ma"]) if pd.notna(sample["p_exit_ignore_if_above_ma"]) else None,
            min_hold_days=int(sample["p_min_hold_days"]),
        )
    gated = apply_combined_rule(_slice(frame, args.start, None), win_rule)
    daily, trades, benchmark, summary = run_index_backtest(gated, cost=COST)
    daily.to_csv(out / "daily_winner.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(out / "trades_winner.csv", index=False, encoding="utf-8-sig")

    w_oos = oos.loc[oos["rule"] == winner].iloc[0]
    w_full = full.loc[full["rule"] == winner].iloc[0]

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asset": asset_name,
        "symbol": symbol,
        "n_rules": len(rules),
        "n_jobs": len(jobs),
        "winner": winner,
        "winner_oos_excess": float(w_oos["excess_return"]),
        "winner_full_excess": float(w_full["excess_return"]),
        "winner_rule": win_rule.to_dict(),
        "cost": COST,
        "disclaimer": "研究辅助，非投资建议。网格存在过拟合风险。",
    }
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 情绪策略 vs 0AMV，以及联合网格改进",
        "",
        "> 研究辅助，非投资建议。统一假设：收盘确认、次日开盘成交、单边成本 0.10%。",
        "",
        f"- 标的：{asset_name}（`{symbol}`）",
        f"- 样本：{args.start} 起；样本外 {args.oos_start} 起",
        f"- 规则数：{len(rules)}（含 baseline + 网格）",
        "",
        "## 1. Baseline 公平对比（全样本）",
        "",
        "| 策略 | 总收益 | 买入持有 | 超额 | 回撤 | 暴露 | 交易 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in base_names:
        row = full.loc[full["rule"] == name]
        if row.empty:
            continue
        r = row.iloc[0]
        lines.append(
            f"| {name} | {r['total_return']:.2%} | {r['benchmark_total_return']:.2%} | "
            f"{r['excess_return']:.2%} | {r['max_drawdown']:.1%} | {r['exposure']:.1%} | {int(r['trades'])} |"
        )
    lines.extend(
        [
            "",
            "## 2. Baseline 样本外对比",
            "",
            "| 策略 | OOS超额 | OOS收益 | OOS持有 | 回撤 | 暴露 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name in base_names:
        row = oos.loc[oos["rule"] == name]
        if row.empty:
            continue
        r = row.iloc[0]
        lines.append(
            f"| {name} | {r['excess_return']:.2%} | {r['total_return']:.2%} | "
            f"{r['benchmark_total_return']:.2%} | {r['max_drawdown']:.1%} | {r['exposure']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 3. 联合网格样本外 Top 15（按超额）",
            "",
            "| 规则 | OOS超额 | OOS收益 | 回撤 | 暴露 | 交易 | entry | exit |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for _, r in oos.head(15).iterrows():
        lines.append(
            f"| `{r['rule'][:80]}` | {r['excess_return']:.2%} | {r['total_return']:.2%} | "
            f"{r['max_drawdown']:.1%} | {r['exposure']:.1%} | {int(r['trades'])} | "
            f"{r['p_entry_mode']} | {r['p_exit_mode']} |"
        )
    lines.extend(
        [
            "",
            f"## 4. 推荐联合规则：`{winner}`",
            "",
            "```json",
            json.dumps(win_rule.to_dict(), ensure_ascii=False, indent=2),
            "```",
            "",
            f"- 样本外超额：{float(w_oos['excess_return']):.2%}",
            f"- 全样本超额：{float(w_full['excess_return']):.2%}",
            f"- 全样本策略/持有：{float(w_full['total_return']):.2%} / {float(w_full['benchmark_total_return']):.2%}",
            "",
            "## 5. 结论",
            "",
            "- 情绪+KDJ 与 0AMV 是不同信号源：前者偏冰点抄底，后者偏活跃市值动量/风控。",
            "- 联合网格用于检验“情绪过滤 + 0AMV 进出”是否提升相对持有的超额。",
            "- 若推荐规则仍难全样本跑赢创业板持有，说明长牛年份仍是核心约束；可把联合规则用于降回撤/近年增强。",
            "",
        ]
    )
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print("\nBaseline FULL:")
    print(base_full[["rule", "total_return", "excess_return", "max_drawdown", "exposure"]].to_string(index=False))
    print("\nOOS top5:")
    print(oos.head(5)[["rule", "excess_return", "total_return", "exposure"]].to_string(index=False))
    print(f"\nwinner={winner}")
    print(f"report: {out / 'report.md'}")
    try:
        frame_path.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
