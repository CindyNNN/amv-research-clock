"""Plot top-3 robust ChiNext strategies vs ChiNext index buy-and-hold."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_index_backtest import buy_and_hold_equity, run_index_backtest
from ai_invest_advisor.amv_index_data import IndexSpec, download_index_daily
from ai_invest_advisor.cyb_robust_optimize import (
    COST_PRIMARY,
    RobustRule,
    apply_robust_rule,
    baseline_robust_rules,
    build_research_frame,
)


OUT = ROOT / "reports" / "backtests" / "cyb_robust_optimize"


def _setup_font() -> None:
    fonts = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC"):
        if cand in fonts:
            plt.rcParams["font.sans-serif"] = [cand]
            break
    plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    _setup_font()

    frame, asset_name, symbol = build_research_frame()
    cache = ROOT / "data" / "backtests" / "amv_index_gate"
    idx = download_index_daily(
        IndexSpec("399006", "创业板指", "sz399006"),
        start=frame["date"].min().date(),
        end=frame["date"].max().date(),
        cache_dir=cache,
        force=False,
    )
    idx["date"] = pd.to_datetime(idx["date"]).dt.normalize()
    dates = frame[["date"]].copy()
    idx_aligned = idx.merge(dates, on="date", how="inner").sort_values("date")

    bh_idx = buy_and_hold_equity(idx_aligned, cost=COST_PRIMARY)
    bh_etf = buy_and_hold_equity(frame, cost=COST_PRIMARY)

    rules = [
        RobustRule(
            name="得分冠军_AMV+情绪60+MA60",
            amv_entry_two_day=0.03,
            amv_exit_threshold=-0.035,
            emotion_exit_min=60.0,
            exit_ignore_if_above_ma=60,
            min_hold_days=0,
        ),
        next(r for r in baseline_robust_rules() if r.name == "amv_emo70_ma60"),
        next(r for r in baseline_robust_rules() if r.name == "amv_min10_protect_ma20"),
    ]
    display = {
        "得分冠军_AMV+情绪60+MA60": "①得分冠军 e60|a-3.5%|MA60",
        "amv_emo70_ma60": "②稳健备选 e70|MA60",
        "amv_min10_protect_ma20": "③AMV基线 min10|MA20",
    }

    series: dict[str, pd.Series] = {}
    stats: list[dict] = []
    for rule in rules:
        gated = apply_robust_rule(frame, rule)
        daily, _trades, _bench, summary = run_index_backtest(
            gated,
            cost=COST_PRIMARY,
            peak_dd_exit=rule.peak_dd_exit,
            atr_trail_mult=rule.atr_trail_mult,
        )
        label = display[rule.name]
        eq = daily[["date", "equity"]].copy()
        eq["date"] = pd.to_datetime(eq["date"]).dt.normalize()
        series[label] = eq.set_index("date")["equity"]
        stats.append(
            {
                "策略": label,
                "总收益": f"{summary['total_return']:.1%}",
                "相对ETF持有超额": (
                    f"{summary['total_return'] - summary['benchmark_total_return']:.1%}"
                ),
                "最大回撤": f"{summary['max_drawdown']:.1%}",
                "暴露": f"{summary['exposure']:.1%}",
                "交易次数": int(summary["trades"]),
            }
        )

    bh_idx = bh_idx.copy()
    bh_idx["date"] = pd.to_datetime(bh_idx["date"]).dt.normalize()
    bh_etf = bh_etf.copy()
    bh_etf["date"] = pd.to_datetime(bh_etf["date"]).dt.normalize()
    series["创业板指持有(399006)"] = bh_idx.set_index("date")["equity"]
    series["创业板ETF持有(159915)"] = bh_etf.set_index("date")["equity"]

    panel = pd.DataFrame(series).sort_index().ffill().dropna(how="any")
    panel = panel / panel.iloc[0]

    colors = {
        "①得分冠军 e60|a-3.5%|MA60": "#1f77b4",
        "②稳健备选 e70|MA60": "#2ca02c",
        "③AMV基线 min10|MA20": "#ff7f0e",
        "创业板指持有(399006)": "#444444",
        "创业板ETF持有(159915)": "#999999",
    }
    styles = {
        "创业板指持有(399006)": "--",
        "创业板ETF持有(159915)": ":",
    }

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=140)
    for col in panel.columns:
        ax.plot(
            panel.index,
            panel[col],
            label=col,
            color=colors.get(col),
            linestyle=styles.get(col, "-"),
            linewidth=2.0 if "持有" not in col else 1.6,
            alpha=0.95,
        )
    ax.axhline(1.0, color="#bbbbbb", linewidth=0.8)
    ax.set_title(
        "创业板ETF策略累计净值 vs 创业板指/ETF持有\n"
        f"标的策略：{asset_name}({symbol}) | 收盘确认次日开盘 | 成本0.10% | "
        f"{panel.index.min().date()} ~ {panel.index.max().date()}"
    )
    ax.set_xlabel("日期")
    ax.set_ylabel("累计净值 (起点=1)")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    png = OUT / "equity_top3_vs_cyb.png"
    fig.savefig(png, bbox_inches="tight")
    plt.close()

    weekly = panel.resample("W-FRI").last().dropna(how="all")
    final = panel.iloc[-1]
    rows = []
    for col in panel.columns:
        dd = float((panel[col] / panel[col].cummax() - 1).min())
        rows.append(
            {
                "name": col,
                "final_nav": round(float(final[col]), 4),
                "total_return_pct": round((float(final[col]) - 1) * 100, 2),
                "max_dd_pct": round(dd * 100, 2),
            }
        )

    payload = {
        "start": str(panel.index.min().date()),
        "end": str(panel.index.max().date()),
        "asset": asset_name,
        "symbol": symbol,
        "categories": [d.strftime("%Y-%m-%d") for d in weekly.index],
        "series": {
            c: [None if pd.isna(v) else round(float(v), 4) for v in weekly[c].tolist()]
            for c in weekly.columns
        },
        "summary": rows,
        "stats": stats,
    }
    (OUT / "equity_top3_chart_data.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    panel.to_csv(OUT / "equity_top3_daily.csv", encoding="utf-8-sig")
    print(f"png={png}")
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
