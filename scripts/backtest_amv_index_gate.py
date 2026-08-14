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
    INDEX_UNIVERSE,
    AmvIndexDataError,
    prepare_research_frames,
)
from ai_invest_advisor.amv_strategy_variants import (  # noqa: E402
    apply_gate_rule,
    get_rule,
    recommended_rule,
)


DEFAULT_OUT = ROOT / "reports" / "backtests" / "amv_index_gate"
DEFAULT_CACHE = ROOT / "data" / "backtests" / "amv_index_gate"
COSTS = (0.0, 0.001, 0.0015)


def _resolve_rule(name: str):
    if name in {"recommended", "improved", "v2"}:
        return recommended_rule()
    return get_rule(name)


def _fmt_pct(value: float | object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if pd.isna(number):
        return "n/a"
    return f"{number * 100:.2f}%"


def _write_equity_chart(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    title: str,
    path: Path,
) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(pd.to_datetime(daily["date"]), daily["equity"], label="策略", linewidth=1.5)
    ax.plot(
        pd.to_datetime(benchmark["date"]),
        benchmark["equity"],
        label="买入持有",
        linewidth=1.2,
        alpha=0.85,
    )
    ax.set_title(title)
    ax.set_ylabel("净值")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _build_report_md(summary: pd.DataFrame, metadata: dict) -> str:
    primary = summary[summary["cost"] == 0.001].copy()
    primary["excess"] = primary["total_return"] - primary["benchmark_total_return"]
    by_total = primary.sort_values("total_return", ascending=False)
    by_excess = primary.sort_values("excess", ascending=False)
    rule = metadata.get("rule", {})
    lines = [
        "# 0AMV 门控指数策略回测",
        "",
        "> 研究辅助，非投资建议。指数代理 ETF，存在跟踪误差。",
        "",
        "## 规则",
        "",
        f"- 规则名：`{metadata.get('rule_name', 'baseline')}`",
        f"- 清仓：0AMV 当日涨跌幅 ≤ {float(rule.get('exit_threshold', -0.023)):.1%}",
        f"- 入场：0AMV 连续两日涨幅之和 > {float(rule.get('entry_two_day_sum', 0.04)):.1%}，且空仓",
        "- 成交：收盘确认信号，下一交易日开盘成交",
        "- 主成本：单边 0.10%；敏感性 0 / 0.15%",
    ]
    if rule.get("exit_ignore_if_index_above_ma"):
        lines.append(
            f"- 改进：若指数收盘价仍高于 MA{rule['exit_ignore_if_index_above_ma']}，则忽略清仓信号（趋势保护）"
        )
    if rule.get("exit_require_amv_below_ma"):
        lines.append(
            f"- 附加：清仓还需 0AMV 收盘价低于 MA{rule['exit_require_amv_below_ma']}"
        )
    lines.extend(
        [
            "",
            "## 数据",
            "",
            f"- 生成时间：{metadata['generated_at']}",
            f"- 0AMV 路径：`{metadata['amv_path']}`",
            f"- 研究起点：{metadata['start']}",
            f"- 指数：{', '.join(metadata['symbols'])}",
            "",
            "## 主结果（成本 0.10%）",
            "",
            "| 指数 | 区间 | 总收益 | 年化 | 最大回撤 | 夏普 | 交易次数 | 胜率 | 暴露 | 买入持有 | 超额 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in by_total.iterrows():
        lines.append(
            "| {name} | {start}~{end} | {total} | {ann} | {mdd} | {sharpe:.2f} | {trades} | {win} | {exp} | {bh} | {ex} |".format(
                name=row["name"],
                start=row["start"],
                end=row["end"],
                total=_fmt_pct(row["total_return"]),
                ann=_fmt_pct(row["annualized_return"]),
                mdd=_fmt_pct(row["max_drawdown"]),
                sharpe=float(row["sharpe"]),
                trades=int(row["trades"]),
                win=_fmt_pct(row["win_rate"]),
                exp=_fmt_pct(row["exposure"]),
                bh=_fmt_pct(row["benchmark_total_return"]),
                ex=_fmt_pct(row["excess"]),
            )
        )

    best = by_total.iloc[0]
    best_excess = by_excess.iloc[0]
    beat_n = int((primary["excess"] > 0).sum())
    lines.extend(
        [
            "",
            "## 简要结论",
            "",
            f"- 主成本下策略总收益最高：**{best['name']}**（{_fmt_pct(best['total_return'])}），"
            f"相对买入持有 {_fmt_pct(float(best['excess']))}。",
            f"- 相对买入持有超额最高：**{best_excess['name']}**"
            f"（超额 {_fmt_pct(best_excess['excess'])}，策略 {_fmt_pct(best_excess['total_return'])} "
            f"vs 基准 {_fmt_pct(best_excess['benchmark_total_return'])}）。",
            f"- 五个指数中有 **{beat_n}/5** 跑赢同期买入持有。",
            "- 请同时看最大回撤、交易次数与暴露比例；高收益若伴随极高回撤或样本外不稳定，不宜直接实盘。",
            "",
            "## 风险提示",
            "",
            "- 0AMV 为指南针专有指数，依赖本地缓存质量。",
            "- 次日开盘成交仍理想化，未计滑点、冲击与 ETF 折溢价。",
            "- 阈值/过滤器经样本外筛选，仍有过拟合风险。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0AMV 门控五指数独立回测")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--amv-path", type=Path, default=ROOT / "data" / "compass" / "0amv_daily.csv")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--rule",
        default="recommended",
        help="baseline | recommended | 或其他 GateRule 名称",
    )
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args(argv)

    rule = _resolve_rule(args.rule)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        frames = prepare_research_frames(
            start=args.start,
            end=args.end,
            amv_path=args.amv_path,
            cache_dir=args.cache_dir,
            force_download=args.force_download,
        )
    except AmvIndexDataError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    summaries: list[dict] = []
    for spec in INDEX_UNIVERSE:
        frame = apply_gate_rule(frames[spec.tencent_symbol], rule)
        for cost in COSTS:
            daily, trades, benchmark, summary = run_index_backtest(frame, cost=cost)
            summary["rule"] = rule.name
            summaries.append(summary)
            tag = spec.tencent_symbol
            if cost == 0.001:
                daily.to_csv(out_dir / f"daily_{tag}.csv", index=False, encoding="utf-8-sig")
                trades.to_csv(out_dir / f"trades_{tag}.csv", index=False, encoding="utf-8-sig")
                benchmark.to_csv(
                    out_dir / f"benchmark_{tag}.csv", index=False, encoding="utf-8-sig"
                )
                _write_equity_chart(
                    daily,
                    benchmark,
                    title=f"{spec.name} ({tag}) {rule.name} vs 买入持有",
                    path=out_dir / f"equity_{tag}.png",
                )

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(out_dir / "summary.csv", index=False, encoding="utf-8-sig")
    summary_df.to_json(out_dir / "summary.json", orient="records", force_ascii=False, indent=2)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "amv_path": str(args.amv_path),
        "start": args.start,
        "end": args.end,
        "symbols": [s.tencent_symbol for s in INDEX_UNIVERSE],
        "costs": list(COSTS),
        "rule_name": rule.name,
        "rule": rule.to_dict(),
        "execution": "close_signal_next_open_fill",
        "disclaimer": "研究辅助，非投资建议。",
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(
        _build_report_md(summary_df, metadata),
        encoding="utf-8",
    )

    primary = summary_df[summary_df["cost"] == 0.001].copy()
    primary["excess"] = primary["total_return"] - primary["benchmark_total_return"]
    print(f"rule={rule.name}")
    print(
        primary[
            ["name", "total_return", "benchmark_total_return", "excess", "max_drawdown", "exposure"]
        ].to_string(index=False)
    )
    print(f"\n报告已写入: {out_dir}")
    print("风险提示: 研究辅助，非投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
