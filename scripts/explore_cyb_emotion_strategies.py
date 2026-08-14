from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "reports" / "backtests" / "cyb_emotion_kdj" / "daily_backtest.csv"
OUTPUT_DIR = ROOT / "reports" / "backtests" / "cyb_emotion_strategy_exploration"
ONE_WAY_COST = 0.001

EntryFunction = Callable[[pd.Series], bool]
ExitFunction = Callable[[pd.Series, dict[str, float | int]], bool]


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    family: str
    entry: EntryFunction
    exit: ExitFunction


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values("date").reset_index(drop=True)
    for window in (5, 10, 20, 60):
        result[f"ma{window}"] = result["close"].rolling(window, min_periods=window).mean()
    result["ma20_rising"] = result["ma20"] > result["ma20"].shift(5)
    result["trend_up"] = (
        (result["ma20"] > result["ma60"])
        & result["ma20_rising"]
        & (result["close"] > result["ma60"])
    )
    result["trend_regime"] = (
        (result["ma20"] > result["ma60"])
        & (result["close"] > result["ma60"])
    )
    result["cross_below_ma10"] = (
        (result["close"] < result["ma10"])
        & (result["close"].shift(1) >= result["ma10"].shift(1))
    )
    result["cross_below_ma20"] = (
        (result["close"] < result["ma20"])
        & (result["close"].shift(1) >= result["ma20"].shift(1))
    )
    delta = result["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    rs = gain / loss.replace(0, np.nan)
    result["rsi2"] = (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)
    result["emotion_min_3"] = result["emotion"].rolling(3, min_periods=1).min()
    result["emotion_min_5"] = result["emotion"].rolling(5, min_periods=1).min()
    result["emotion_turn_up"] = result["emotion"] > result["emotion"].shift(1)
    for threshold in (15, 20, 25, 30):
        result[f"emotion_cross_above_{threshold}"] = (
            (result["emotion"] >= threshold)
            & (result["emotion"].shift(1) < threshold)
        )
    result["k_cross_above_d"] = (
        (result["k"] > result["d"])
        & (result["k"].shift(1) <= result["d"].shift(1))
    )
    return result


def current_return(row: pd.Series, state: dict[str, float | int]) -> float:
    return float(row["close"]) / float(state["entry_price"]) - 1.0


STRATEGIES = [
    Strategy(
        "baseline_next_open",
        "基准：情绪<15且J<30 / KDJ死叉",
        "baseline",
        lambda row: bool(row["emotion"] < 15 and row["j"] < 30),
        lambda row, state: bool(row["kdj_dead_cross"]),
    ),
    Strategy(
        "fast_j_e20",
        "快反：情绪<20且J<20 / MA5或5日",
        "fast_reversion",
        lambda row: bool(row["emotion"] < 20 and row["j"] < 20),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_j_trend_e20",
        "快反趋势过滤：多头环境、情绪<20、J<20 / MA5或5日",
        "fast_trend",
        lambda row: bool(
            row["trend_regime"]
            and row["emotion"] < 20
            and row["j"] < 20
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_j_trend_e25",
        "快反趋势过滤：多头环境、情绪<25、J<20 / MA5或5日",
        "fast_trend",
        lambda row: bool(
            row["trend_regime"]
            and row["emotion"] < 25
            and row["j"] < 20
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_j_trend_e30",
        "快反趋势过滤邻域：多头环境、情绪<30、J<20 / MA5或5日",
        "fast_trend",
        lambda row: bool(
            row["trend_regime"]
            and row["emotion"] < 30
            and row["j"] < 20
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_j30_trend_e25",
        "快反趋势过滤邻域：多头环境、情绪<25、J<30 / MA5或5日",
        "fast_trend",
        lambda row: bool(
            row["trend_regime"]
            and row["emotion"] < 25
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_rsi_trend_e25",
        "RSI快反趋势过滤：多头环境、情绪<25、RSI2<10 / MA5或5日",
        "fast_trend",
        lambda row: bool(
            row["trend_regime"]
            and row["emotion"] < 25
            and row["rsi2"] < 10
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_rsi_e25",
        "快反：情绪<25且RSI2<10 / MA5或5日",
        "fast_reversion",
        lambda row: bool(row["emotion"] < 25 and row["rsi2"] < 10),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 5
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_rsi_e35",
        "高频：情绪<35且RSI2<8 / MA5或4日",
        "fast_reversion",
        lambda row: bool(
            row["emotion"] < 35
            and row["rsi2"] < 8
            and row["close"] < row["ma10"]
        ),
        lambda row, state: bool(
            row["close"] >= row["ma5"]
            or state["bars_held"] >= 4
            or current_return(row, state) <= -0.04
        ),
    ),
    Strategy(
        "fast_emotion_turn",
        "高频：情绪<30且回升、J<30 / 7日",
        "fast_reversion",
        lambda row: bool(
            row["emotion"] < 30
            and row["emotion_turn_up"]
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["j"] > 80
            or state["bars_held"] >= 7
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "fast_ice_rebound",
        "确认反弹：近3日冰点且情绪上穿20 / 5日",
        "fast_reversion",
        lambda row: bool(
            row["emotion_min_3"] < 15
            and row["emotion_cross_above_20"]
            and row["close"] > row["close_prev"]
        ),
        lambda row, state: bool(
            state["bars_held"] >= 5
            or row["j"] > 80
            or current_return(row, state) <= -0.05
        ),
    ),
    Strategy(
        "trend_dip_e20",
        "趋势回调：上升趋势且情绪<20、J<30",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 20
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_dip_e15",
        "趋势回调邻域：上升趋势、情绪<15、J<30",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 15
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_dip_e25",
        "趋势回调邻域：上升趋势、情绪<25、J<30",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 25
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_dip_e20_ma10",
        "趋势回调快退出：上升趋势、情绪<20、J<30 / 跌破MA10",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 20
            and row["j"] < 30
        ),
        lambda row, state: bool(
            row["cross_below_ma10"]
            or row["close"] <= float(state["peak_close"]) * 0.92
        ),
    ),
    Strategy(
        "trend_dip_e30",
        "趋势回调：上升趋势且情绪<30、RSI2<15",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 30
            and row["rsi2"] < 15
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_emotion_recovery",
        "趋势确认：近5日情绪<20后上穿30",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion_min_5"] < 20
            and row["emotion_cross_above_30"]
            and row["close"] > row["ma20"]
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_broad_dip",
        "趋势宽口径：情绪<35、RSI2<20",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion"] < 35
            and row["rsi2"] < 20
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
    Strategy(
        "trend_hybrid_e20",
        "趋势混合：情绪<20且J<30 / 死叉且低于MA20",
        "trend",
        lambda row: bool(
            row["close"] > row["ma60"]
            and row["emotion"] < 20
            and row["j"] < 30
        ),
        lambda row, state: bool(
            (row["kdj_dead_cross"] and row["close"] < row["ma20"])
            or row["close"] <= float(state["peak_close"]) * 0.92
        ),
    ),
    Strategy(
        "trend_kdj_recovery",
        "趋势确认：近5日冰点后K上穿D",
        "trend",
        lambda row: bool(
            row["trend_up"]
            and row["emotion_min_5"] < 20
            and row["k_cross_above_d"]
        ),
        lambda row, state: bool(
            row["cross_below_ma20"]
            or row["close"] <= float(state["peak_close"]) * 0.90
        ),
    ),
]


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def backtest(
    frame: pd.DataFrame,
    strategy: Strategy,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int | str]]:
    data = frame.loc[frame["date"].between(start, end)].copy().reset_index(drop=True)
    data["close_prev"] = data["close"].shift(1)
    position = 0
    pending_order = ""
    equity_close = 1.0
    entry_price = float("nan")
    entry_date = pd.NaT
    entry_equity_before_cost = float("nan")
    bars_held = 0
    peak_close = float("nan")
    positions: list[int] = []
    equities: list[float] = []
    actions: list[str] = []
    trades: list[dict[str, float | int | str | pd.Timestamp]] = []

    for index, row in data.iterrows():
        if index == 0:
            equity_open = equity_close
        elif position:
            equity_open = equity_close * float(row["open"] / data.loc[index - 1, "close"])
        else:
            equity_open = equity_close

        action = ""
        if pending_order == "sell" and position:
            exit_price = float(row["open"])
            equity_open *= 1.0 - ONE_WAY_COST
            net_return = (
                exit_price / entry_price * (1.0 - ONE_WAY_COST) ** 2 - 1.0
            )
            trades.append(
                {
                    "strategy": strategy.name,
                    "family": strategy.family,
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": row["date"],
                    "exit_price": exit_price,
                    "holding_days": bars_held,
                    "net_return": net_return,
                    "status": "closed",
                }
            )
            position = 0
            bars_held = 0
            peak_close = float("nan")
            action = "sell_open"
        elif pending_order == "buy" and not position:
            position = 1
            entry_price = float(row["open"])
            entry_date = row["date"]
            entry_equity_before_cost = equity_open
            equity_open *= 1.0 - ONE_WAY_COST
            bars_held = 0
            peak_close = entry_price
            action = "buy_open"
        pending_order = ""

        if position:
            equity_close = equity_open * float(row["close"] / row["open"])
            bars_held += 1
            peak_close = max(peak_close, float(row["close"]))
            state = {
                "entry_price": entry_price,
                "bars_held": bars_held,
                "peak_close": peak_close,
                "entry_equity_before_cost": entry_equity_before_cost,
            }
            if strategy.exit(row, state):
                pending_order = "sell"
        else:
            equity_close = equity_open
            if index > 0 and strategy.entry(row):
                pending_order = "buy"

        positions.append(position)
        equities.append(equity_close)
        actions.append(action)

    if position:
        last = data.iloc[-1]
        trades.append(
            {
                "strategy": strategy.name,
                "family": strategy.family,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": pd.NaT,
                "exit_price": np.nan,
                "holding_days": bars_held,
                "net_return": float(
                    last["close"] / entry_price * (1.0 - ONE_WAY_COST) - 1.0
                ),
                "status": "open_mark_to_market",
            }
        )

    data["position"] = positions
    data["equity"] = equities
    data["action"] = actions
    trades_frame = pd.DataFrame(
        trades,
        columns=[
            "strategy",
            "family",
            "entry_date",
            "entry_price",
            "exit_date",
            "exit_price",
            "holding_days",
            "net_return",
            "status",
        ],
    )
    closed = trades_frame.loc[trades_frame["status"] == "closed"]
    elapsed_days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1)
    years = elapsed_days / 365.2425
    total_return = float(data["equity"].iloc[-1] - 1.0)
    daily_return = data["equity"].pct_change().fillna(0.0)
    volatility = float(daily_return.std(ddof=1))
    annualized_return = float((1.0 + total_return) ** (1.0 / years) - 1.0)
    drawdown = max_drawdown(data["equity"])
    summary: dict[str, float | int | str] = {
        "strategy": strategy.name,
        "label": strategy.label,
        "family": strategy.family,
        "start": data["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": data["date"].iloc[-1].strftime("%Y-%m-%d"),
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": drawdown,
        "calmar": annualized_return / abs(drawdown) if drawdown < 0 else np.nan,
        "sharpe_rf0": (
            float(math.sqrt(252.0) * daily_return.mean() / volatility)
            if volatility > 0
            else np.nan
        ),
        "closed_trades": int(len(closed)),
        "trades_per_year": float(len(closed) / years),
        "win_rate": float((closed["net_return"] > 0).mean()) if len(closed) else np.nan,
        "average_trade": float(closed["net_return"].mean()) if len(closed) else np.nan,
        "median_trade": float(closed["net_return"].median()) if len(closed) else np.nan,
        "average_holding_days": float(closed["holding_days"].mean())
        if len(closed)
        else np.nan,
        "exposure": float(data["position"].mean()),
        "open_position": int(position),
        "pending_order_at_end": pending_order,
    }
    return data, trades_frame, summary


PERIODS = {
    "full": ("2020-01-01", "2026-07-17"),
    "2020_2021": ("2020-01-01", "2021-12-31"),
    "2022_2023": ("2022-01-01", "2023-12-31"),
    "2024_present": ("2024-01-01", "2026-07-17"),
}


def plot_selected(
    daily_results: dict[str, pd.DataFrame],
    selected: list[str],
    output: Path,
) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(14, 7))
    for name in selected:
        data = daily_results[name]
        label = next(strategy.label for strategy in STRATEGIES if strategy.name == name)
        ax.plot(data["date"], data["equity"], label=label, linewidth=1.4)
    ax.set_title("情绪高频与趋势候选策略：次日开盘成交、单边成本0.10%")
    ax.set_ylabel("策略净值")
    ax.grid(alpha=0.2)
    ax.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(INPUT, parse_dates=["date"])
    source = add_features(source)
    all_summaries: list[dict[str, float | int | str]] = []
    all_trades: list[pd.DataFrame] = []
    full_daily: dict[str, pd.DataFrame] = {}
    yearly_rows: list[dict[str, float | int | str]] = []

    for period, (start, end) in PERIODS.items():
        for strategy in STRATEGIES:
            daily, trades, summary = backtest(source, strategy, start, end)
            summary["period"] = period
            all_summaries.append(summary)
            all_trades.append(trades.assign(period=period))
            if period == "full":
                full_daily[strategy.name] = daily
                daily = daily.copy()
                daily["year"] = daily["date"].dt.year
                for year, group in daily.groupby("year"):
                    yearly_rows.append(
                        {
                            "strategy": strategy.name,
                            "label": strategy.label,
                            "family": strategy.family,
                            "year": int(year),
                            "return": float(
                                group["equity"].iloc[-1] / group["equity"].iloc[0] - 1.0
                            ),
                        }
                    )

    summary_frame = pd.DataFrame(all_summaries)
    trades_frame = pd.concat(all_trades, ignore_index=True)
    yearly_frame = pd.DataFrame(yearly_rows)
    full = summary_frame.loc[summary_frame["period"] == "full"].copy()
    full["rank_score"] = (
        full["calmar"].rank(pct=True)
        + full["sharpe_rf0"].rank(pct=True)
        + full["trades_per_year"].clip(upper=15).rank(pct=True) * 0.25
    )
    selected = (
        full.sort_values("rank_score", ascending=False)
        .head(5)["strategy"]
        .tolist()
    )

    summary_path = OUTPUT_DIR / "strategy_summary.csv"
    trades_path = OUTPUT_DIR / "strategy_trades.csv"
    yearly_path = OUTPUT_DIR / "strategy_yearly_returns.csv"
    chart_path = OUTPUT_DIR / "selected_strategy_equity.png"
    metadata_path = OUTPUT_DIR / "experiment_metadata.json"
    summary_frame.to_csv(summary_path, index=False, encoding="utf-8-sig")
    trades_frame.to_csv(trades_path, index=False, encoding="utf-8-sig")
    yearly_frame.to_csv(yearly_path, index=False, encoding="utf-8-sig")
    plot_selected(full_daily, selected, chart_path)
    metadata_path.write_text(
        json.dumps(
            {
                "input": str(INPUT),
                "signal_timestamp": "daily close",
                "execution": "next trading day open",
                "one_way_cost": ONE_WAY_COST,
                "periods": PERIODS,
                "selected_for_chart": selected,
                "strategies": [
                    {
                        "name": strategy.name,
                        "label": strategy.label,
                        "family": strategy.family,
                    }
                    for strategy in STRATEGIES
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        full.sort_values(["max_drawdown", "annualized_return"], ascending=[False, False])[
            [
                "strategy",
                "family",
                "total_return",
                "annualized_return",
                "max_drawdown",
                "calmar",
                "sharpe_rf0",
                "closed_trades",
                "trades_per_year",
                "win_rate",
                "average_holding_days",
                "exposure",
            ]
        ].to_string(index=False)
    )
    print(f"summary={summary_path}")
    print(f"trades={trades_path}")
    print(f"yearly={yearly_path}")
    print(f"chart={chart_path}")


if __name__ == "__main__":
    main()
