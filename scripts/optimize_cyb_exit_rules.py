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
OUTPUT_DIR = ROOT / "reports" / "backtests" / "cyb_exit_optimization"
ONE_WAY_COST = 0.001


@dataclass(frozen=True)
class ExitRule:
    name: str
    label: str
    should_exit: Callable[[pd.Series, float], bool]


def add_exit_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for window in (10, 15, 20, 25, 30, 40):
        column = f"ma{window}"
        cross_column = f"cross_below_ma{window}"
        result[column] = result["close"].rolling(window, min_periods=window).mean()
        result[cross_column] = (
            (result["close"] < result[column])
            & (result["close"].shift(1) >= result[column].shift(1))
        )
    return result


RULES = [
    ExitRule(
        "kdj_dead_cross",
        "KDJ死叉（基准）",
        lambda row, peak: bool(row["kdj_dead_cross"]),
    ),
    ExitRule(
        "cross_below_ma10",
        "收盘跌破MA10",
        lambda row, peak: bool(row["cross_below_ma10"]),
    ),
    ExitRule(
        "cross_below_ma20",
        "收盘跌破MA20",
        lambda row, peak: bool(row["cross_below_ma20"]),
    ),
    ExitRule(
        "cross_below_ma30",
        "收盘跌破MA30",
        lambda row, peak: bool(row["cross_below_ma30"]),
    ),
    ExitRule(
        "kdj_or_ma20",
        "KDJ死叉 或 跌破MA20",
        lambda row, peak: bool(row["kdj_dead_cross"] or row["cross_below_ma20"]),
    ),
    ExitRule(
        "kdj_and_below_ma20",
        "KDJ死叉 且 收盘低于MA20",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma20"]),
    ),
    ExitRule(
        "kdj_and_below_ma10",
        "KDJ死叉 且 收盘低于MA10",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma10"]),
    ),
    ExitRule(
        "kdj_and_below_ma15",
        "KDJ死叉 且 收盘低于MA15",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma15"]),
    ),
    ExitRule(
        "kdj_and_below_ma25",
        "KDJ死叉 且 收盘低于MA25",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma25"]),
    ),
    ExitRule(
        "kdj_and_below_ma30",
        "KDJ死叉 且 收盘低于MA30",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma30"]),
    ),
    ExitRule(
        "kdj_and_below_ma40",
        "KDJ死叉 且 收盘低于MA40",
        lambda row, peak: bool(row["kdj_dead_cross"] and row["close"] < row["ma40"]),
    ),
    ExitRule(
        "trailing_stop_10",
        "最高收盘回撤10%",
        lambda row, peak: bool(row["close"] <= peak * 0.90),
    ),
    ExitRule(
        "kdj_or_trailing_10",
        "KDJ死叉 或 回撤10%",
        lambda row, peak: bool(
            row["kdj_dead_cross"] or row["close"] <= peak * 0.90
        ),
    ),
    ExitRule(
        "hybrid20_or_trailing_8",
        "死叉且低于MA20 或 回撤8%",
        lambda row, peak: bool(
            (row["kdj_dead_cross"] and row["close"] < row["ma20"])
            or row["close"] <= peak * 0.92
        ),
    ),
    ExitRule(
        "hybrid20_or_trailing_10",
        "死叉且低于MA20 或 回撤10%",
        lambda row, peak: bool(
            (row["kdj_dead_cross"] and row["close"] < row["ma20"])
            or row["close"] <= peak * 0.90
        ),
    ),
    ExitRule(
        "hybrid20_or_trailing_12",
        "死叉且低于MA20 或 回撤12%",
        lambda row, peak: bool(
            (row["kdj_dead_cross"] and row["close"] < row["ma20"])
            or row["close"] <= peak * 0.88
        ),
    ),
    ExitRule(
        "hybrid20_or_trailing_15",
        "死叉且低于MA20 或 回撤15%",
        lambda row, peak: bool(
            (row["kdj_dead_cross"] and row["close"] < row["ma20"])
            or row["close"] <= peak * 0.85
        ),
    ),
]


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def run_rule(
    frame: pd.DataFrame,
    rule: ExitRule,
    start: str,
    end: str,
    one_way_cost: float = ONE_WAY_COST,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int | str]]:
    data = frame.loc[frame["date"].between(start, end)].copy().reset_index(drop=True)
    position = 0
    entry_index: int | None = None
    entry_price: float | None = None
    peak_close = float("nan")
    equity = 1.0
    positions: list[int] = []
    equities: list[float] = []
    actions: list[str] = []
    trades: list[dict[str, float | int | str | pd.Timestamp]] = []

    for index, row in data.iterrows():
        if index > 0 and position:
            equity *= float(row["close"] / data.loc[index - 1, "close"])
            peak_close = max(peak_close, float(row["close"]))

        action = ""
        if position and rule.should_exit(row, peak_close):
            assert entry_index is not None and entry_price is not None
            exit_price = float(row["close"])
            equity *= 1.0 - one_way_cost
            trades.append(
                {
                    "rule": rule.name,
                    "entry_date": data.loc[entry_index, "date"],
                    "entry_price": entry_price,
                    "exit_date": row["date"],
                    "exit_price": exit_price,
                    "holding_days": index - entry_index,
                    "gross_return": exit_price / entry_price - 1.0,
                    "net_return": (
                        exit_price
                        / entry_price
                        * (1.0 - one_way_cost) ** 2
                        - 1.0
                    ),
                    "status": "closed",
                }
            )
            position = 0
            entry_index = None
            entry_price = None
            peak_close = float("nan")
            action = "sell_close"
        elif not position and bool(row["entry_condition"]):
            position = 1
            entry_index = index
            entry_price = float(row["close"])
            peak_close = float(row["close"])
            equity *= 1.0 - one_way_cost
            action = "buy_close"

        positions.append(position)
        equities.append(equity)
        actions.append(action)

    if position and entry_index is not None and entry_price is not None:
        last = data.iloc[-1]
        trades.append(
            {
                "rule": rule.name,
                "entry_date": data.loc[entry_index, "date"],
                "entry_price": entry_price,
                "exit_date": pd.NaT,
                "exit_price": np.nan,
                "holding_days": len(data) - 1 - entry_index,
                "gross_return": float(last["close"] / entry_price - 1.0),
                "net_return": float(
                    last["close"] / entry_price * (1.0 - one_way_cost) - 1.0
                ),
                "status": "open_mark_to_market",
            }
        )

    data["position"] = positions
    data["equity"] = equities
    data["action"] = actions
    trade_frame = pd.DataFrame(trades)
    closed = trade_frame.loc[trade_frame["status"] == "closed"]
    elapsed_days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1)
    years = elapsed_days / 365.2425
    total_return = float(data["equity"].iloc[-1] - 1.0)
    returns = data["equity"].pct_change().fillna(0.0)
    standard_deviation = float(returns.std(ddof=1))
    summary: dict[str, float | int | str] = {
        "rule": rule.name,
        "label": rule.label,
        "start": data["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": data["date"].iloc[-1].strftime("%Y-%m-%d"),
        "total_return": total_return,
        "annualized_return": float((1.0 + total_return) ** (1.0 / years) - 1.0),
        "max_drawdown": max_drawdown(data["equity"]),
        "sharpe_rf0": float(
            math.sqrt(252.0) * returns.mean() / standard_deviation
            if standard_deviation > 0
            else np.nan
        ),
        "closed_trades": int(len(closed)),
        "win_rate": float((closed["net_return"] > 0).mean()) if len(closed) else np.nan,
        "average_trade": float(closed["net_return"].mean()) if len(closed) else np.nan,
        "average_holding_days": float(closed["holding_days"].mean())
        if len(closed)
        else np.nan,
        "exposure": float(data["position"].shift(1).fillna(0).mean()),
        "open_position": int(position),
    }
    return data, trade_frame, summary


def period_results(frame: pd.DataFrame, start: str, end: str, period: str):
    summaries: list[dict[str, float | int | str]] = []
    daily: dict[str, pd.DataFrame] = {}
    trades: list[pd.DataFrame] = []
    for rule in RULES:
        rule_daily, rule_trades, summary = run_rule(frame, rule, start, end)
        summary["period"] = period
        summaries.append(summary)
        daily[rule.name] = rule_daily
        trades.append(rule_trades.assign(period=period))
    return pd.DataFrame(summaries), daily, pd.concat(trades, ignore_index=True)


def save_comparison_chart(full_daily: dict[str, pd.DataFrame], output: Path) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    selected = [
        "kdj_dead_cross",
        "cross_below_ma20",
        "kdj_and_below_ma10",
        "kdj_and_below_ma20",
        "kdj_and_below_ma30",
    ]
    fig, ax = plt.subplots(figsize=(14, 7))
    for rule_name in selected:
        data = full_daily[rule_name]
        label = next(rule.label for rule in RULES if rule.name == rule_name)
        ax.plot(data["date"], data["equity"], label=label, linewidth=1.4)
    ax.axvline(pd.Timestamp("2024-01-01"), color="black", linestyle="--", alpha=0.55)
    ax.text(
        pd.Timestamp("2024-01-15"),
        ax.get_ylim()[1] * 0.97,
        "样本外开始",
        va="top",
    )
    ax.set_title("不同卖出规则的成本后净值（单边0.10%）")
    ax.set_ylabel("策略净值")
    ax.grid(alpha=0.2)
    ax.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(INPUT, parse_dates=["date"])
    source = add_exit_features(source)

    full_summary, full_daily, full_trades = period_results(
        source, "2020-01-01", "2026-07-17", "full"
    )
    train_summary, _, train_trades = period_results(
        source, "2020-01-01", "2023-12-31", "train_2020_2023"
    )
    test_summary, _, test_trades = period_results(
        source, "2024-01-01", "2026-07-17", "test_2024_present"
    )
    summary = pd.concat(
        [full_summary, train_summary, test_summary],
        ignore_index=True,
    )
    trades = pd.concat([full_trades, train_trades, test_trades], ignore_index=True)
    yearly_rows: list[dict[str, float | int | str]] = []
    for rule in RULES:
        data = full_daily[rule.name].copy()
        data["year"] = data["date"].dt.year
        for year, group in data.groupby("year"):
            start_equity = float(group["equity"].iloc[0])
            end_equity = float(group["equity"].iloc[-1])
            yearly_rows.append(
                {
                    "rule": rule.name,
                    "label": rule.label,
                    "year": int(year),
                    "return": end_equity / start_equity - 1.0,
                }
            )
    yearly = pd.DataFrame(yearly_rows)

    summary_path = OUTPUT_DIR / "exit_rule_summary.csv"
    trades_path = OUTPUT_DIR / "exit_rule_trades.csv"
    yearly_path = OUTPUT_DIR / "exit_rule_yearly_returns.csv"
    chart_path = OUTPUT_DIR / "exit_rule_equity_comparison.png"
    metadata_path = OUTPUT_DIR / "experiment_metadata.json"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    yearly.to_csv(yearly_path, index=False, encoding="utf-8-sig")
    save_comparison_chart(full_daily, chart_path)
    metadata_path.write_text(
        json.dumps(
            {
                "input": str(INPUT),
                "one_way_cost": ONE_WAY_COST,
                "entry": "emotion < 15 and J < 30, same-day close",
                "execution": "same-day close",
                "train": "2020-01-01 to 2023-12-31",
                "test": "2024-01-01 to 2026-07-17",
                "rules": [{"name": rule.name, "label": rule.label} for rule in RULES],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(f"summary={summary_path}")
    print(f"trades={trades_path}")
    print(f"yearly={yearly_path}")
    print(f"chart={chart_path}")


if __name__ == "__main__":
    main()
