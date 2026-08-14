from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_index_backtest import run_index_backtest  # noqa: E402
from ai_invest_advisor.amv_index_data import (  # noqa: E402
    IndexSpec,
    align_index_with_amv,
    build_amv_signals,
    download_index_daily,
    load_amv_daily,
)
from ai_invest_advisor.cyb_amv_strategy import (  # noqa: E402
    apply_cyb_gate_rule,
    cyb_candidate_rules,
    get_cyb_rule,
)


DEFAULT_OUT = ROOT / "reports" / "backtests" / "cyb_amv_beat_bh"
OOS_START = "2023-01-01"
ETF_SPEC = IndexSpec("159915", "创业板ETF", "sz159915")
INDEX_SPEC = IndexSpec("399006", "创业板指", "sz399006")


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if start:
        data = data[data["date"] >= pd.Timestamp(start)]
    if end:
        data = data[data["date"] <= pd.Timestamp(end)]
    return data.reset_index(drop=True)


def yearly_excess(daily: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    b = benchmark.copy()
    d["date"] = pd.to_datetime(d["date"])
    b["date"] = pd.to_datetime(b["date"])
    d["year"] = d["date"].dt.year
    b["year"] = b["date"].dt.year
    rows = []
    for year in sorted(d["year"].unique()):
        ds = d[d["year"] == year]
        bs = b[b["year"] == year]
        if len(ds) < 5 or len(bs) < 5:
            continue
        sr = float(ds["equity"].iloc[-1] / ds["equity"].iloc[0] - 1.0)
        br = float(bs["equity"].iloc[-1] / bs["equity"].iloc[0] - 1.0)
        rows.append(
            {
                "year": int(year),
                "strategy": sr,
                "buy_hold": br,
                "excess": sr - br,
                "exposure": float(ds["position"].mean()),
            }
        )
    return pd.DataFrame(rows)


def evaluate(frame: pd.DataFrame, rule, *, cost: float, start, end) -> dict:
    gated = apply_cyb_gate_rule(_slice(frame, start, end), rule)
    daily, trades, benchmark, summary = run_index_backtest(gated, cost=cost)
    yearly = yearly_excess(daily, benchmark)
    excess = float(summary["total_return"]) - float(summary["benchmark_total_return"])
    return {
        "rule": rule.name,
        "rule_dict": rule.to_dict(),
        "segment_start": start,
        "segment_end": end,
        "total_return": float(summary["total_return"]),
        "benchmark_total_return": float(summary["benchmark_total_return"]),
        "excess_return": excess,
        "max_drawdown": float(summary["max_drawdown"]),
        "exposure": float(summary["exposure"]),
        "trades": int(summary["trades"]),
        "sharpe": float(summary["sharpe"]),
        "annualized_return": float(summary["annualized_return"]),
        "years_beat_bh": int((yearly["excess"] > 0).sum()) if not yearly.empty else 0,
        "years_total": int(len(yearly)),
        "yearly": yearly,
        "daily": daily,
        "trade_frame": trades,
        "benchmark": benchmark,
    }


def load_cyb_frame(*, start, force_download: bool):
    amv = build_amv_signals(load_amv_daily())
    end = datetime.now().date()
    start_d = pd.Timestamp(start).date()
    cache = ROOT / "data" / "backtests" / "amv_index_gate"
    try:
        px = download_index_daily(
            ETF_SPEC, start=start_d, end=end, cache_dir=cache, force=force_download
        )
        frame = align_index_with_amv(px, amv, spec=ETF_SPEC)
        return frame, "创业板ETF(159915)", ETF_SPEC.tencent_symbol
    except Exception as exc:  # noqa: BLE001
        print(f"ETF下载失败，改用创业板指: {exc}")
        px = download_index_daily(
            INDEX_SPEC, start=start_d, end=end, cache_dir=cache, force=force_download
        )
        frame = align_index_with_amv(px, amv, spec=INDEX_SPEC)
        return frame, "创业板指(399006)", INDEX_SPEC.tencent_symbol


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创业板：寻找能跑赢持续持有的 0AMV 门控规则")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--cost", type=float, default=0.001)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--oos-start", default=OOS_START)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args(argv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    frame, asset_name, symbol = load_cyb_frame(
        start=args.start, force_download=args.force_download
    )

    segments = {
        "IS": (args.start, "2022-12-31"),
        "OOS": (args.oos_start, None),
        "FULL": (args.start, None),
    }
    rows = []
    full_artifacts = {}
    for rule in cyb_candidate_rules():
        for tag, (start, end) in segments.items():
            res = evaluate(frame, rule, cost=args.cost, start=start, end=end)
            rows.append(
                {
                    "rule": rule.name,
                    "segment_tag": tag,
                    "total_return": res["total_return"],
                    "benchmark_total_return": res["benchmark_total_return"],
                    "excess_return": res["excess_return"],
                    "max_drawdown": res["max_drawdown"],
                    "exposure": res["exposure"],
                    "trades": res["trades"],
                    "sharpe": res["sharpe"],
                    "years_beat_bh": res["years_beat_bh"],
                    "years_total": res["years_total"],
                    "annualized_return": res["annualized_return"],
                }
            )
            if tag == "FULL":
                full_artifacts[rule.name] = res

    detail = pd.DataFrame(rows)
    detail.to_csv(out / "cyb_rule_detail.csv", index=False, encoding="utf-8-sig")
    oos = detail[detail["segment_tag"] == "OOS"].sort_values(
        ["excess_return", "years_beat_bh"], ascending=[False, False]
    )
    full = detail[detail["segment_tag"] == "FULL"].sort_values(
        ["excess_return", "years_beat_bh"], ascending=[False, False]
    )
    oos.to_csv(out / "cyb_rule_rank_oos.csv", index=False, encoding="utf-8-sig")
    full.to_csv(out / "cyb_rule_rank_full.csv", index=False, encoding="utf-8-sig")

    baseline_oos = float(oos.loc[oos["rule"] == "baseline", "excess_return"].iloc[0])
    baseline_full = float(full.loc[full["rule"] == "baseline", "excess_return"].iloc[0])

    winner = "baseline"
    # Prefer OOS+FULL both positive excess.
    for _, row in oos.iterrows():
        full_ex = float(full.loc[full["rule"] == row["rule"], "excess_return"].iloc[0])
        if float(row["excess_return"]) > 0 and full_ex > 0:
            winner = str(row["rule"])
            break
    else:
        # Else best OOS that also improves FULL vs baseline.
        for _, row in oos.iterrows():
            full_ex = float(full.loc[full["rule"] == row["rule"], "excess_return"].iloc[0])
            if full_ex > baseline_full and float(row["excess_return"]) >= baseline_oos:
                winner = str(row["rule"])
                break

    win = full_artifacts[winner]
    win["daily"].to_csv(out / "daily_winner.csv", index=False, encoding="utf-8-sig")
    win["trade_frame"].to_csv(out / "trades_winner.csv", index=False, encoding="utf-8-sig")
    win["yearly"].to_csv(out / "yearly_winner.csv", index=False, encoding="utf-8-sig")

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(pd.to_datetime(win["daily"]["date"]), win["daily"]["equity"], label=f"策略:{winner}")
    ax.plot(
        pd.to_datetime(win["benchmark"]["date"]),
        win["benchmark"]["equity"],
        label="买入持有",
    )
    ax.set_title(f"{asset_name} 0AMV门控 vs 持续持有")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "equity_winner.png", dpi=120)
    plt.close(fig)

    w_full = full.loc[full["rule"] == winner].iloc[0]
    b_full = full.loc[full["rule"] == "baseline"].iloc[0]
    w_oos = oos.loc[oos["rule"] == winner].iloc[0]

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asset": asset_name,
        "symbol": symbol,
        "winner": winner,
        "cost": args.cost,
        "baseline_oos_excess": baseline_oos,
        "baseline_full_excess": baseline_full,
        "winner_oos_excess": float(w_oos["excess_return"]),
        "winner_full_excess": float(w_full["excess_return"]),
        "beats_bh_full": bool(w_full["excess_return"] > 0),
        "beats_bh_oos": bool(w_oos["excess_return"] > 0),
        "disclaimer": "研究辅助，非投资建议。",
    }
    (out / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 创业板：如何用 0AMV 门控跑赢持续持有",
        "",
        "> 研究辅助，非投资建议。",
        "",
        "## 问题拆解",
        "",
        "- 原规则有择时能力，但牛市年（2019/2020/2025）暴露太低，大幅跑输持有。",
        "- 熊市/震荡年（2022/2024/2026）往往能跑赢持有。",
        "- 改进方向：**提高牛市暴露**，同时尽量保留大跌保护。",
        "",
        f"- 标的：{asset_name}（`{symbol}`）",
        f"- 样本外：{args.oos_start} 起；成本单边 {args.cost:.2%}",
        "",
        "## 样本外排名（按超额）",
        "",
        "| 规则 | OOS超额 | OOS策略 | OOS持有 | 暴露 | 回撤 | 跑赢年数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in oos.head(12).iterrows():
        lines.append(
            f"| {row['rule']} | {row['excess_return']:.2%} | {row['total_return']:.2%} | "
            f"{row['benchmark_total_return']:.2%} | {row['exposure']:.1%} | {row['max_drawdown']:.1%} | "
            f"{int(row['years_beat_bh'])}/{int(row['years_total'])} |"
        )
    lines.extend(
        [
            "",
            f"## 推荐规则：`{winner}`",
            "",
            "```json",
            json.dumps(get_cyb_rule(winner).to_dict(), ensure_ascii=False, indent=2),
            "```",
            "",
            "### 全样本 vs baseline",
            "",
            f"- baseline：策略 {b_full['total_return']:.2%} / 持有 {b_full['benchmark_total_return']:.2%} / 超额 {b_full['excess_return']:.2%}",
            f"- 推荐：策略 {w_full['total_return']:.2%} / 持有 {w_full['benchmark_total_return']:.2%} / 超额 {w_full['excess_return']:.2%}",
            f"- 暴露：{b_full['exposure']:.1%} → {w_full['exposure']:.1%}",
            f"- 最大回撤：{b_full['max_drawdown']:.1%} → {w_full['max_drawdown']:.1%}",
            "",
            "### 分年表现（推荐规则）",
            "",
            "| 年 | 策略 | 持有 | 超额 | 暴露 |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for _, y in win["yearly"].iterrows():
        lines.append(
            f"| {int(y['year'])} | {y['strategy']:.2%} | {y['buy_hold']:.2%} | {y['excess']:.2%} | {y['exposure']:.1%} |"
        )
    if meta["beats_bh_full"] and meta["beats_bh_oos"]:
        conclusion = (
            f"找到规则 `{winner}`：样本外与全样本均跑赢持续持有。"
            "仍需警惕过拟合与 ETF 跟踪误差。"
        )
    elif meta["beats_bh_oos"] and not meta["beats_bh_full"]:
        conclusion = (
            f"`{winner}` 样本外跑赢，但全样本仍未超过持有；说明长牛阶段仍是主要拖累。"
        )
    else:
        conclusion = (
            "在当前 0AMV 阈值族内，**未能找到**样本外与全样本都稳定跑赢创业板持续持有的规则。"
            "更现实目标可能是：降低回撤、提高熊市相对收益，而不是绝对跑赢长牛持有。"
        )
    lines.extend(["", "## 结论", "", f"- {conclusion}", ""])
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"asset={asset_name} symbol={symbol}")
    print(oos.head(8)[["rule", "excess_return", "total_return", "exposure", "max_drawdown"]].to_string(index=False))
    print(f"\nwinner={winner}")
    print(
        f"FULL excess baseline={baseline_full:.2%} winner={meta['winner_full_excess']:.2%} | "
        f"OOS baseline={baseline_oos:.2%} winner={meta['winner_oos_excess']:.2%}"
    )
    print(f"report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
