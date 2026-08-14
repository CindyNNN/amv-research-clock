from pathlib import Path

import nbformat as nbf


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "cyb_emotion_strategy_exploration.ipynb"


def code(source: str):
    return nbf.v4.new_code_cell(source.strip())


def markdown(source: str):
    return nbf.v4.new_markdown_cell(source.strip())


def main() -> None:
    notebook = nbf.v4.new_notebook()
    notebook["metadata"]["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    notebook["metadata"]["language_info"] = {"name": "python", "version": "3"}
    notebook["cells"] = [
        markdown(
            """
# 创业板指：情绪约束的快反与趋势策略探索

## tl;dr

- 更高频与更低回撤之间存在明显取舍，单纯放宽情绪阈值会增加交易，但无法稳定降低回撤。
- 当前最稳健的低回撤候选是 `fast_j_trend_e25`：仅在中期多头环境内进行情绪低吸。
- 更趋势化的候选是 `trend_dip_e25`，收益更高但持仓更久，且收益集中于 2024–2025，可信度低于快反候选。
- 本研究使用收盘信号、下一交易日开盘成交、单边 0.10% 成本，避免用当天收盘价生成信号并成交的前视偏差。
"""
        ),
        markdown(
            """
## Context & Methods

研究区间为 2020-01-02 至 2026-07-17，标的是创业板指 399006。

情绪指标采用严格历史口径：

`上涨家数 / (上涨家数 + 平盘家数 + 下跌家数) × 100`

所有策略均为单仓位、全额持有。信号在日线收盘后生成，下一交易日开盘执行。每次买入和卖出各扣 0.10% 成本。指数本身不可直接交易，实盘需要用创业板 ETF 等可交易代理，并重新计入跟踪误差、滑点和基金费用。

候选分为两类：

1. 快反趋势过滤：中期多头环境（MA20>MA60 且收盘>MA60）下，情绪低迷且 J 值超卖；回到 MA5、持有满 5 日或亏损 5% 时退出。
2. 趋势回调：更严格的上升趋势（另要求 MA20 五日上升）下情绪低迷；跌破 MA20 或从持仓峰值回撤 10% 时退出。
"""
        ),
        code(
            """
from pathlib import Path
import json
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import display

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
OUT = ROOT / "reports" / "backtests" / "cyb_emotion_strategy_exploration"
SOURCE = ROOT / "reports" / "backtests" / "cyb_emotion_kdj" / "daily_backtest.csv"

daily = pd.read_csv(SOURCE, parse_dates=["date"])
summary = pd.read_csv(OUT / "strategy_summary.csv")
trades = pd.read_csv(OUT / "strategy_trades.csv", parse_dates=["entry_date", "exit_date"])
yearly = pd.read_csv(OUT / "strategy_yearly_returns.csv")
metadata = json.loads((OUT / "experiment_metadata.json").read_text(encoding="utf-8"))
"""
        ),
        markdown(
            """
## Data

下面的检查用于确认日期唯一、行情字段有效、情绪处于 0–100 范围，并确认实验元数据采用“收盘信号、次日开盘成交”。
"""
        ),
        code(
            """
checks = {
    "rows": len(daily),
    "start": daily["date"].min().date().isoformat(),
    "end": daily["date"].max().date().isoformat(),
    "duplicate_dates": int(daily["date"].duplicated().sum()),
    "missing_ohlc": int(daily[["open", "high", "low", "close"]].isna().sum().sum()),
    "missing_emotion": int(daily["emotion"].isna().sum()),
    "emotion_min": float(daily["emotion"].min()),
    "emotion_max": float(daily["emotion"].max()),
    "invalid_high_low_rows": int(
        ((daily["high"] < daily[["open", "close"]].max(axis=1))
         | (daily["low"] > daily[["open", "close"]].min(axis=1))).sum()
    ),
    "execution": metadata["execution"],
    "one_way_cost": metadata["one_way_cost"],
}
display(pd.Series(checks, name="value").to_frame())

assert checks["duplicate_dates"] == 0
assert checks["missing_ohlc"] == 0
assert checks["missing_emotion"] == 0
assert 0 <= checks["emotion_min"] <= checks["emotion_max"] <= 100
assert checks["invalid_high_low_rows"] == 0
assert metadata["signal_timestamp"] == "daily close"
assert metadata["execution"] == "next trading day open"
"""
        ),
        markdown(
            """
## Results

主表只保留基准、快反趋势过滤的参数邻域、以及趋势回调的参数邻域。收益和回撤均已包含双边交易成本。
"""
        ),
        code(
            """
selected = [
    "baseline_next_open",
    "fast_j_trend_e20",
    "fast_j_trend_e25",
    "fast_j_trend_e30",
    "fast_j30_trend_e25",
    "trend_dip_e15",
    "trend_dip_e20",
    "trend_dip_e25",
]
columns = [
    "strategy", "total_return", "annualized_return", "max_drawdown",
    "calmar", "sharpe_rf0", "closed_trades", "trades_per_year",
    "win_rate", "average_holding_days", "exposure",
]
full = summary[(summary["period"] == "full") & summary["strategy"].isin(selected)][columns].copy()
for column in ["total_return", "annualized_return", "max_drawdown", "win_rate", "exposure"]:
    full[column] = full[column].map(lambda value: f"{value:.2%}")
for column in ["calmar", "sharpe_rf0", "trades_per_year", "average_holding_days"]:
    full[column] = full[column].map(lambda value: f"{value:.2f}")
display(full.set_index("strategy"))
"""
        ),
        code(
            """
period_names = ["2020_2021", "2022_2023", "2024_present"]
focus = ["fast_j_trend_e20", "fast_j_trend_e25", "fast_j_trend_e30", "trend_dip_e25"]
stability = summary[
    summary["strategy"].isin(focus) & summary["period"].isin(period_names)
].pivot(index="strategy", columns="period", values="total_return")
display(stability.style.format("{:.2%}"))

year_table = yearly[yearly["strategy"].isin(focus)].pivot(
    index="year", columns="strategy", values="return"
)
display(year_table.style.format("{:.2%}"))
"""
        ),
        code(
            """
plot_data = summary[
    (summary["period"] == "full")
    & summary["strategy"].isin([
        "baseline_next_open", "fast_j_trend_e20", "fast_j_trend_e25",
        "fast_j_trend_e30", "trend_dip_e25"
    ])
].copy()

fig, ax = plt.subplots(figsize=(9, 5.5))
for _, row in plot_data.iterrows():
    ax.scatter(
        abs(row["max_drawdown"]) * 100,
        row["annualized_return"] * 100,
        s=40 + row["trades_per_year"] * 18,
        label=row["strategy"],
    )
    ax.annotate(
        row["strategy"],
        (abs(row["max_drawdown"]) * 100, row["annualized_return"] * 100),
        xytext=(5, 5),
        textcoords="offset points",
        fontsize=8,
    )
ax.set_xlabel("Maximum drawdown (%)")
ax.set_ylabel("Annualized return (%)")
ax.set_title("Risk-return trade-off; marker size = trades per year")
ax.grid(alpha=0.25)
fig.tight_layout()
plt.show()
"""
        ),
        markdown(
            """
## Takeaways

1. **低回撤首选：`fast_j_trend_e25`。**  
   买入：MA20>MA60、收盘>MA60、情绪<25、J<20；次日开盘执行。  
   卖出：收盘回到 MA5，或持有满 5 个交易日，或收盘相对入场价亏损 5%；次日开盘执行。

2. **参数邻域较平滑。**  
   情绪阈值 20/25/30 对应更多交易与更大回撤，并没有只在某一个阈值出现异常高收益。25 是风险与频率的折中，不应被理解为精确最优值。

3. **更趋势化候选：`trend_dip_e25`。**  
   它的全期收益和年化收益更高，但只有 17 笔交易，且大部分利润集中在 2025 年。它更适合作为待验证的趋势子策略，而非单独作为主系统。

4. **不能靠放宽阈值直接获得“高频且低回撤”。**  
   不加趋势过滤的快反策略可达到每年约 8–11 笔，但全期最大回撤约 23%–35%，且近年表现衰减。趋势过滤是降低回撤的主要来源，也必然减少交易次数。

### 限制与下一步

- 样本只有约 6.5 年，低回撤候选仅 20 笔，统计置信度有限。
- 历史情绪数据 2020–2021 为全 A 股日线重建，2022 起来自同花顺市场分布接口；虽做过重叠校验，仍存在口径变化风险。
- 本轮是小规模规则探索，不是严格的样本外研究。下一步应固定规则，做滚动前推（walk-forward）和创业板 ETF 代理成交回测。
- 所有结果仅为研究支持，不构成投资建议。
"""
        ),
    ]
    NOTEBOOK.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(notebook, NOTEBOOK)
    print(NOTEBOOK)


if __name__ == "__main__":
    main()
