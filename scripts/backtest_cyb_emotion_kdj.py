from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "backtests" / "cyb_emotion_kdj"
OUTPUT_DIR = ROOT / "reports" / "backtests" / "cyb_emotion_kdj"

INDEX_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param=sz399006,day,2019-01-01,2026-07-17,2000,qfq"
)
BREADTH_URL = (
    "https://dq.10jqka.com.cn/fuyao/ext_quote_uplimit_down/"
    "extquote_updown/v1/distribution?date={date}"
)

INDEX_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "change",
    "turnover",
]


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2020-01-01"
    end_date: str = "2026-07-17"
    emotion_threshold: float = 15.0
    j_threshold: float = 30.0
    kdj_n: int = 9
    kdj_m1: int = 3
    kdj_m2: int = 3
    one_way_cost: float = 0.0


def fetch_index() -> pd.DataFrame:
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            completed = subprocess.run(
                [
                    "curl.exe",
                    "-sS",
                    "-L",
                    "--compressed",
                    "--max-time",
                    "30",
                    "-A",
                    "Mozilla/5.0",
                    INDEX_URL,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            payload = json.loads(completed.stdout)
            break
        except (subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(attempt)
    if payload is None:
        raise RuntimeError("创业板指接口连续3次连接失败") from last_error
    day_rows = payload.get("data", {}).get("sz399006", {}).get("day")
    if payload.get("code") != 0 or not day_rows:
        raise RuntimeError(f"创业板指接口返回异常: code={payload.get('code')}")
    frame = pd.DataFrame(
        day_rows,
        columns=["date", "open", "close", "high", "low", "volume"],
    )
    frame["amount"] = np.nan
    frame["amplitude"] = (
        (pd.to_numeric(frame["high"]) - pd.to_numeric(frame["low"]))
        / pd.to_numeric(frame["close"]).shift(1)
        * 100.0
    )
    frame["pct_chg"] = pd.to_numeric(frame["close"]).pct_change() * 100.0
    frame["change"] = pd.to_numeric(frame["close"]).diff()
    frame["turnover"] = np.nan
    frame = frame[INDEX_COLUMNS]
    frame["date"] = pd.to_datetime(frame["date"])
    for column in INDEX_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("创业板指 OHLC 存在缺失值")
    return frame


def parse_distribution(date: pd.Timestamp, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status_code") != 0:
        raise RuntimeError(f"{date:%Y-%m-%d} 涨跌分布接口异常: {payload}")
    result = payload.get("result") or {}
    distribution = result.get("distribution")
    if not isinstance(distribution, list) or len(distribution) != 63:
        raise ValueError(f"{date:%Y-%m-%d} 涨跌分布长度不是63")
    values = [int(value) for value in distribution]
    advancers = sum(values[:31])
    unchanged = values[31]
    decliners = sum(values[32:])
    quoted_total = advancers + unchanged + decliners
    if quoted_total <= 0:
        raise ValueError(f"{date:%Y-%m-%d} 有效股票总数为0")
    return {
        "date": date,
        "advancers": advancers,
        "unchanged": unchanged,
        "decliners": decliners,
        "quoted_total": quoted_total,
        "emotion": advancers / quoted_total * 100.0,
        "limit_up": int(result.get("limit_up", 0)),
        "limit_down": int(result.get("limit_down", 0)),
        "last_update_time": result.get("last_update_time"),
        "source": "同花顺涨跌分布",
    }


def fetch_one_breadth(date: pd.Timestamp) -> dict[str, Any]:
    url = BREADTH_URL.format(date=date.strftime("%Y%m%d"))
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            completed = subprocess.run(
                [
                    "curl.exe",
                    "-sS",
                    "-L",
                    "--compressed",
                    "--max-time",
                    "15",
                    "-A",
                    "Mozilla/5.0",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            return parse_distribution(date, json.loads(completed.stdout))
        except (
            subprocess.SubprocessError,
            UnicodeError,
            json.JSONDecodeError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            time.sleep(attempt * 0.5)
    raise RuntimeError(f"{date:%Y-%m-%d} 连续3次下载失败") from last_error


def fetch_breadth(dates: list[pd.Timestamp], workers: int = 12) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one_breadth, date): date for date in dates}
        for index, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if index % 250 == 0 or index == len(futures):
                print(f"涨跌分布下载进度: {index}/{len(futures)}", flush=True)
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def chinese_sma(values: pd.Series, n: int, initial: float = 50.0) -> pd.Series:
    output = np.empty(len(values), dtype=float)
    previous = float(initial)
    for index, value in enumerate(values.to_numpy(dtype=float)):
        if np.isnan(value):
            output[index] = previous
        else:
            previous = ((n - 1) * previous + value) / n
            output[index] = previous
    return pd.Series(output, index=values.index)


def add_kdj(frame: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    result = frame.copy()
    lowest = result["low"].rolling(n, min_periods=n).min()
    highest = result["high"].rolling(n, min_periods=n).max()
    spread = highest - lowest
    rsv = 100.0 * (result["close"] - lowest) / spread.replace(0, np.nan)
    result["rsv"] = rsv
    result["k"] = chinese_sma(rsv, m1, initial=50.0)
    result["d"] = chinese_sma(result["k"], m2, initial=50.0)
    result["j"] = 3.0 * result["k"] - 2.0 * result["d"]
    result["kdj_dead_cross"] = (
        (result["k"] < result["d"])
        & (result["k"].shift(1) >= result["d"].shift(1))
    )
    return result


def backtest(frame: pd.DataFrame, config: BacktestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = frame.copy().reset_index(drop=True)
    result["entry_condition"] = (
        (result["emotion"] < config.emotion_threshold)
        & (result["j"] < config.j_threshold)
    )
    position = 0
    entry_index: int | None = None
    entry_price: float | None = None
    equity = 1.0
    positions: list[int] = []
    equities: list[float] = []
    actions: list[str] = []
    trades: list[dict[str, Any]] = []

    for index, row in result.iterrows():
        if index > 0 and position == 1:
            equity *= float(row["close"] / result.loc[index - 1, "close"])

        action = ""
        if position == 1 and bool(row["kdj_dead_cross"]):
            exit_price = float(row["close"])
            equity *= 1.0 - config.one_way_cost
            assert entry_index is not None and entry_price is not None
            net_return = (
                exit_price / entry_price
                * (1.0 - config.one_way_cost) ** 2
                - 1.0
            )
            trades.append(
                {
                    "entry_date": result.loc[entry_index, "date"],
                    "entry_price": entry_price,
                    "entry_emotion": float(result.loc[entry_index, "emotion"]),
                    "entry_j": float(result.loc[entry_index, "j"]),
                    "exit_date": row["date"],
                    "exit_price": exit_price,
                    "exit_k": float(row["k"]),
                    "exit_d": float(row["d"]),
                    "holding_trading_days": index - entry_index,
                    "gross_return": exit_price / entry_price - 1.0,
                    "net_return": net_return,
                    "status": "closed",
                }
            )
            position = 0
            entry_index = None
            entry_price = None
            action = "sell_close"
        elif position == 0 and bool(row["entry_condition"]):
            position = 1
            entry_index = index
            entry_price = float(row["close"])
            equity *= 1.0 - config.one_way_cost
            action = "buy_close"

        positions.append(position)
        equities.append(equity)
        actions.append(action)

    if position == 1 and entry_index is not None and entry_price is not None:
        last = result.iloc[-1]
        trades.append(
            {
                "entry_date": result.loc[entry_index, "date"],
                "entry_price": entry_price,
                "entry_emotion": float(result.loc[entry_index, "emotion"]),
                "entry_j": float(result.loc[entry_index, "j"]),
                "exit_date": pd.NaT,
                "exit_price": np.nan,
                "exit_k": np.nan,
                "exit_d": np.nan,
                "holding_trading_days": len(result) - 1 - entry_index,
                "gross_return": float(last["close"] / entry_price - 1.0),
                "net_return": float(
                    last["close"] / entry_price * (1.0 - config.one_way_cost) - 1.0
                ),
                "status": "open_mark_to_market",
            }
        )

    result["position_after_close"] = positions
    result["strategy_equity"] = equities
    result["action"] = actions
    result["benchmark_equity"] = result["close"] / result["close"].iloc[0]
    return result, pd.DataFrame(trades)


def max_drawdown(equity: pd.Series) -> float:
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def summarize(daily: pd.DataFrame, trades: pd.DataFrame, config: BacktestConfig) -> dict[str, Any]:
    elapsed_days = max((daily["date"].iloc[-1] - daily["date"].iloc[0]).days, 1)
    years = elapsed_days / 365.2425
    strategy_total = float(daily["strategy_equity"].iloc[-1] - 1.0)
    benchmark_total = float(daily["benchmark_equity"].iloc[-1] - 1.0)
    daily_returns = daily["strategy_equity"].pct_change().fillna(0.0)
    std = float(daily_returns.std(ddof=1))
    closed = trades.loc[trades["status"] == "closed"].copy()
    return {
        "start_date": daily["date"].iloc[0].strftime("%Y-%m-%d"),
        "end_date": daily["date"].iloc[-1].strftime("%Y-%m-%d"),
        "trading_days": int(len(daily)),
        "strategy_total_return": strategy_total,
        "strategy_annualized_return": float((1.0 + strategy_total) ** (1.0 / years) - 1.0),
        "strategy_max_drawdown": max_drawdown(daily["strategy_equity"]),
        "strategy_sharpe_rf0": float(
            math.sqrt(252.0) * daily_returns.mean() / std if std > 0 else np.nan
        ),
        "benchmark_total_return": benchmark_total,
        "benchmark_annualized_return": float((1.0 + benchmark_total) ** (1.0 / years) - 1.0),
        "benchmark_max_drawdown": max_drawdown(daily["benchmark_equity"]),
        "closed_trades": int(len(closed)),
        "open_trades": int((trades["status"] != "closed").sum()) if len(trades) else 0,
        "win_rate": float((closed["net_return"] > 0).mean()) if len(closed) else np.nan,
        "average_trade_return": float(closed["net_return"].mean()) if len(closed) else np.nan,
        "median_trade_return": float(closed["net_return"].median()) if len(closed) else np.nan,
        "average_holding_trading_days": float(closed["holding_trading_days"].mean())
        if len(closed)
        else np.nan,
        "exposure": float(daily["position_after_close"].shift(1).fillna(0).mean()),
        "entry_signal_days": int(daily["entry_condition"].sum()),
        "emotion_min": float(daily["emotion"].min()),
        "emotion_below_15_days": int((daily["emotion"] < config.emotion_threshold).sum()),
    }


def validate(
    full_index: pd.DataFrame,
    breadth: pd.DataFrame,
    merged: pd.DataFrame,
    config: BacktestConfig,
    overlap: pd.DataFrame | None = None,
) -> dict[str, Any]:
    expected_dates = set(
        full_index.loc[
            full_index["date"].between(config.start_date, config.end_date), "date"
        ]
    )
    breadth_dates = set(breadth["date"])
    missing_dates = sorted(expected_dates - breadth_dates)
    duplicates = int(breadth["date"].duplicated().sum())
    latest = merged.iloc[-1]
    checks = {
        "missing_breadth_dates": [date.strftime("%Y-%m-%d") for date in missing_dates],
        "duplicate_breadth_dates": duplicates,
        "quoted_total_min": int(breadth["quoted_total"].min()),
        "quoted_total_max": int(breadth["quoted_total"].max()),
        "latest_date": latest["date"].strftime("%Y-%m-%d"),
        "latest_close": float(latest["close"]),
        "latest_k": float(latest["k"]),
        "latest_d": float(latest["d"]),
        "latest_j": float(latest["j"]),
        "latest_advancers": int(latest["advancers"]),
        "latest_unchanged": int(latest["unchanged"]),
        "latest_decliners": int(latest["decliners"]),
        "latest_emotion": float(latest["emotion"]),
    }
    if overlap is not None and len(overlap):
        emotion_gap = overlap["emotion_ths"] - overlap["emotion_legacy"]
        checks.update(
            {
                "overlap_days_2022": int(len(overlap)),
                "overlap_emotion_mae": float(emotion_gap.abs().mean()),
                "overlap_emotion_median_abs_error": float(emotion_gap.abs().median()),
                "overlap_threshold_15_agreement": float(
                    (
                        (overlap["emotion_ths"] < config.emotion_threshold)
                        == (overlap["emotion_legacy"] < config.emotion_threshold)
                    ).mean()
                ),
            }
        )
    if missing_dates or duplicates:
        raise ValueError(f"数据完整性校验失败: {checks}")
    return checks


def save_chart(daily: pd.DataFrame, output_path: Path) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(14, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [1.25, 1.0, 1.0]},
    )
    axes[0].plot(daily["date"], daily["strategy_equity"], label="策略净值", linewidth=1.8)
    axes[0].plot(daily["date"], daily["benchmark_equity"], label="创业板指买入持有", alpha=0.75)
    axes[0].set_ylabel("净值")
    axes[0].legend(loc="upper left")
    axes[0].grid(alpha=0.2)

    axes[1].plot(daily["date"], daily["emotion"], color="#22a884", linewidth=1.0)
    axes[1].axhline(15.0, color="#d62728", linestyle="--", linewidth=1.0, label="情绪阈值 15")
    axes[1].fill_between(
        daily["date"],
        daily["emotion"],
        15.0,
        where=daily["emotion"] < 15.0,
        color="#d62728",
        alpha=0.18,
    )
    axes[1].set_ylabel("市场情绪 (%)")
    axes[1].legend(loc="upper left")
    axes[1].grid(alpha=0.2)

    axes[2].plot(daily["date"], daily["k"], label="K", linewidth=1.0)
    axes[2].plot(daily["date"], daily["d"], label="D", linewidth=1.0)
    axes[2].plot(daily["date"], daily["j"], label="J", linewidth=0.9, alpha=0.8)
    buy = daily["action"] == "buy_close"
    sell = daily["action"] == "sell_close"
    axes[2].scatter(daily.loc[buy, "date"], daily.loc[buy, "j"], marker="^", color="red", s=45, label="买入")
    axes[2].scatter(daily.loc[sell, "date"], daily.loc[sell, "j"], marker="v", color="black", s=45, label="卖出")
    axes[2].axhline(30.0, color="gray", linestyle="--", linewidth=0.8)
    axes[2].set_ylabel("KDJ")
    axes[2].legend(loc="upper left", ncol=5)
    axes[2].grid(alpha=0.2)
    fig.suptitle("创业板指：市场情绪 < 15 且 J < 30 买入，KDJ 死叉卖出")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def write_report(
    summary: dict[str, Any],
    cost_summary: dict[str, Any],
    validation: dict[str, Any],
    config: BacktestConfig,
    output_path: Path,
) -> None:
    def pct(value: float) -> str:
        return f"{value:.2%}"

    text = f"""# 创业板指“市场情绪 + KDJ”严谨回测

生成时间：{datetime.now().astimezone().isoformat(timespec="seconds")}

## 结果摘要

- 区间：{summary["start_date"]} 至 {summary["end_date"]}，{summary["trading_days"]} 个交易日
- 策略累计收益：{pct(summary["strategy_total_return"])}
- 策略年化收益：{pct(summary["strategy_annualized_return"])}
- 策略最大回撤：{pct(summary["strategy_max_drawdown"])}
- Sharpe（无风险利率按 0）：{summary["strategy_sharpe_rf0"]:.2f}
- 创业板指买入持有累计收益：{pct(summary["benchmark_total_return"])}
- 创业板指买入持有最大回撤：{pct(summary["benchmark_max_drawdown"])}
- 完整交易：{summary["closed_trades"]} 笔；期末未平仓：{summary["open_trades"]} 笔
- 胜率：{pct(summary["win_rate"])}
- 平均单笔收益：{pct(summary["average_trade_return"])}
- 平均持有：{summary["average_holding_trading_days"]:.1f} 个交易日
- 仓位暴露：{pct(summary["exposure"])}
- 成本敏感性（单边 0.10%）累计收益：{pct(cost_summary["strategy_total_return"])}

## 回测规则

- 标的：创业板指 399006。
- 买入：空仓时，市场情绪严格小于 {config.emotion_threshold:g}，且 KDJ 的 J 严格小于 {config.j_threshold:g}。
- 卖出：持仓时，K 当日下穿 D（昨日 K >= D 且今日 K < D）。
- 成交：信号当日收盘价买入或卖出；单仓、全仓、不加仓。
- 成本：单边 {pct(config.one_way_cost)}。
- 期末持仓：按最后收盘价盯市，不强制平仓。

## 指标口径

- KDJ 与同花顺默认 `(9,3,3)` 一致：RSV 使用 9 日最高/最低，K、D 使用中国式 SMA，初值 50。
- 市场情绪数据：2020—2021 由包含历史退市股的全 A 逐股日线聚合；2022 年至今使用同花顺客户端内置涨跌分布。后者 63 个分箱中，前 31 格为上涨、第 32 格为平盘、后 31 格为下跌。
- 严谨分母为当日有有效涨跌报价的全 A 股票数：`上涨 / (上涨 + 平盘 + 下跌) * 100`。这消除了“拿今天股票总数除历史上涨家数”的未来函数，也不会把当日停牌、没有可观测涨跌幅的股票算进有效广度。

## 校验

- 数据缺失交易日：{len(validation["missing_breadth_dates"])}
- 重复交易日：{validation["duplicate_breadth_dates"]}
- 最新日 {validation["latest_date"]}：创业板指收盘 {validation["latest_close"]:.2f}
- 最新 K/D/J：{validation["latest_k"]:.2f} / {validation["latest_d"]:.2f} / {validation["latest_j"]:.2f}
- 最新上涨/平盘/下跌：{validation["latest_advancers"]} / {validation["latest_unchanged"]} / {validation["latest_decliners"]}
- 最新严谨情绪：{validation["latest_emotion"]:.4f}
- 2022 年双源重叠校验：{validation["overlap_days_2022"]} 日，情绪平均绝对误差 {validation["overlap_emotion_mae"]:.3f} 个百分点，`<15` 分类一致率 {validation["overlap_threshold_15_agreement"]:.2%}

## 重要限制

“用收盘后才能确认的情绪和 KDJ，再按同一个收盘价成交”在实盘中不可严格实现，存在同收盘成交的乐观偏差。本回测按用户指定执行；更可交易的版本应改为下一交易日开盘或收盘成交。历史表现不构成投资建议。
"""
    output_path.write_text(text, encoding="utf-8")


def run(config: BacktestConfig, refresh: bool = False, workers: int = 12) -> dict[str, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = DATA_DIR / "cyb_399006_daily.csv"
    breadth_path = DATA_DIR / "all_a_breadth_ths_2022_present.csv"
    legacy_path = DATA_DIR / "all_a_breadth_legacy_2020_2022.csv"
    combined_breadth_path = DATA_DIR / "all_a_breadth_combined.csv"

    if refresh or not index_path.exists():
        full_index = fetch_index()
        full_index.to_csv(index_path, index=False, encoding="utf-8-sig")
    else:
        full_index = pd.read_csv(index_path, parse_dates=["date"])

    analysis_dates = full_index.loc[
        full_index["date"].between(config.start_date, config.end_date), "date"
    ].tolist()
    if not analysis_dates:
        raise ValueError("指定区间内没有创业板指交易日")

    if breadth_path.exists() and not refresh:
        breadth = pd.read_csv(breadth_path, parse_dates=["date"])
        cached_dates = set(breadth["date"])
    else:
        breadth = pd.DataFrame()
        cached_dates = set()
    ths_dates = [date for date in analysis_dates if date >= pd.Timestamp("2022-01-01")]
    missing = [date for date in ths_dates if date not in cached_dates]
    if missing:
        downloaded = fetch_breadth(missing, workers=workers)
        breadth = pd.concat([breadth, downloaded], ignore_index=True)
        breadth = breadth.sort_values("date").drop_duplicates("date", keep="last")
        breadth.to_csv(breadth_path, index=False, encoding="utf-8-sig")

    if not legacy_path.exists():
        raise FileNotFoundError(
            f"缺少2020—2022历史广度数据，请先运行 scripts/build_legacy_breadth_duckdb.py: {legacy_path}"
        )
    legacy = pd.read_csv(legacy_path, parse_dates=["date"])
    overlap = legacy.loc[legacy["date"].dt.year == 2022, ["date", "emotion"]].merge(
        breadth.loc[breadth["date"].dt.year == 2022, ["date", "emotion"]],
        on="date",
        suffixes=("_legacy", "_ths"),
        validate="one_to_one",
    )
    legacy_pre_2022 = legacy.loc[legacy["date"] < pd.Timestamp("2022-01-01")].copy()
    combined_breadth = pd.concat([legacy_pre_2022, breadth], ignore_index=True, sort=False)
    combined_breadth = (
        combined_breadth.sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    combined_breadth.to_csv(combined_breadth_path, index=False, encoding="utf-8-sig")

    full_index = add_kdj(
        full_index,
        n=config.kdj_n,
        m1=config.kdj_m1,
        m2=config.kdj_m2,
    )
    index_slice = full_index.loc[
        full_index["date"].between(config.start_date, config.end_date)
    ].copy()
    breadth_slice = combined_breadth.loc[
        combined_breadth["date"].isin(analysis_dates)
    ].copy()
    merged = index_slice.merge(breadth_slice, on="date", how="left", validate="one_to_one")
    if merged["emotion"].isna().any():
        missing_dates = merged.loc[merged["emotion"].isna(), "date"].dt.strftime("%Y-%m-%d").tolist()
        raise ValueError(f"情绪数据未对齐: {missing_dates[:10]}")

    daily, trades = backtest(merged, config)
    summary = summarize(daily, trades, config)
    cost_config = replace(config, one_way_cost=0.001)
    cost_daily, cost_trades = backtest(merged, cost_config)
    cost_summary = summarize(cost_daily, cost_trades, cost_config)
    validation = validate(full_index, breadth_slice, daily, config, overlap=overlap)

    daily_path = OUTPUT_DIR / "daily_backtest.csv"
    trades_path = OUTPUT_DIR / "trades.csv"
    summary_path = OUTPUT_DIR / "summary.json"
    cost_summary_path = OUTPUT_DIR / "summary_cost_10bp.json"
    validation_path = OUTPUT_DIR / "validation.json"
    chart_path = OUTPUT_DIR / "equity_emotion_kdj.png"
    report_path = OUTPUT_DIR / "report.md"
    sources_path = OUTPUT_DIR / "sources.json"

    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    cost_summary_path.write_text(
        json.dumps(cost_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    sources_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "index": {
                    "provider": "腾讯证券公开行情接口",
                    "symbol": "399006",
                    "url": INDEX_URL,
                    "adjustment": "不复权（指数）",
                },
                "market_breadth": {
                    "provider": (
                        "2020—2021: Hugging Face 全A逐股日线聚合；"
                        "2022年至今: 同花顺客户端配置中的涨跌分布接口"
                    ),
                    "url_template": BREADTH_URL,
                    "formula": "sum(distribution[0:31]) / sum(distribution) * 100",
                    "legacy_dataset": (
                        "https://huggingface.co/datasets/cedwyh/"
                        "jinjing-shared-data"
                    ),
                },
                "config": asdict(config),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    save_chart(daily, chart_path)
    write_report(summary, cost_summary, validation, config, report_path)
    return {
        "index": index_path,
        "breadth_ths": breadth_path,
        "breadth_legacy": legacy_path,
        "breadth_combined": combined_breadth_path,
        "daily": daily_path,
        "trades": trades_path,
        "summary": summary_path,
        "summary_cost_10bp": cost_summary_path,
        "validation": validation_path,
        "chart": chart_path,
        "report": report_path,
        "sources": sources_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="创业板指市场情绪 + KDJ 回测")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--one-way-cost", type=float, default=0.0)
    args = parser.parse_args()
    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        one_way_cost=args.one_way_cost,
    )
    paths = run(config, refresh=args.refresh, workers=args.workers)
    print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
