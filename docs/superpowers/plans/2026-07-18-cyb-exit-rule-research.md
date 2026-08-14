# 创业板情绪策略卖出规则研究 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可复现的卖出规则研究器，在固定的“情绪冰点 + MA250 + 次日开盘买入”条件下，寻找计费后收益高于101.57%且最大回撤不超过20%的简单稳健退出规则。

**Architecture:** 将研究拆成纯数据准备、单规则逐日状态机、候选规则与指标、稳健性筛选、CLI报告五个边界。所有条件型退出在收盘确认并于下一交易日开盘执行；固定期限退出按预定收盘执行。核心模块只返回DataFrame和字典，文件写入集中在CLI层。

**Tech Stack:** Python 3.11、pandas、numpy、matplotlib、pytest。

## Global Constraints

- 样本区间固定为2020-01-02至2026-07-17，MA250使用2019年起的指数数据预热。
- 情绪定义固定为`advancers / (advancers + decliners) * 100`。
- 入场固定为信号日`emotion < 15 and close >= ma250`，下一交易日开盘买入。
- 一次只持有一笔；待买入或持仓期间忽略新信号；卖出日不安排新买入。
- 条件型退出在收盘确认，下一交易日开盘成交；固定期限退出在到期日收盘成交。
- 主成本为单边0.1%，敏感性成本为单边0.15%。
- 合格规则必须满足主成本最大回撤不超过20%，且累计收益高于101.57%。
- 禁止未来数据、盘中OHLC触发顺序假设、自动交易调用和券商接口。
- 当前目录不是有效Git仓库；每个任务以全量测试通过作为检查点，不执行`git commit`。

---

### Task 1: 研究数据集与指标

**Files:**
- Create: `src/ai_invest_advisor/cyb_exit_research.py`
- Create: `tests/test_cyb_exit_research.py`

**Interfaces:**
- Produces: `ResearchConfig`、`load_research_frame(config: ResearchConfig) -> pd.DataFrame`
- DataFrame至少包含：`date, open, high, low, close, emotion, ma250, atr14, ma5, ma10, ma20, k, d, j, kdj_dead_cross, entry_signal`

- [ ] **Step 1: 写数据准备失败测试**

```python
from pathlib import Path
import pandas as pd

from ai_invest_advisor.cyb_exit_research import ResearchConfig, load_research_frame


def test_load_research_frame_uses_no_flat_emotion_and_preheated_ma250(tmp_path):
    dates = pd.bdate_range("2019-01-02", periods=270)
    index = pd.DataFrame(
        {
            "date": dates,
            "open": range(1000, 1270),
            "high": range(1001, 1271),
            "low": range(999, 1269),
            "close": range(1000, 1270),
        }
    )
    breadth = pd.DataFrame(
        {
            "date": dates,
            "advancers": 10,
            "unchanged": 90,
            "decliners": 90,
        }
    )
    index_path = tmp_path / "index.csv"
    breadth_path = tmp_path / "breadth.csv"
    index.to_csv(index_path, index=False)
    breadth.to_csv(breadth_path, index=False)

    frame = load_research_frame(
        ResearchConfig(
            index_path=index_path,
            breadth_path=breadth_path,
            start_date="2019-12-20",
            end_date=str(dates[-1].date()),
        )
    )

    assert frame["emotion"].iloc[-1] == 10.0
    assert pd.notna(frame["ma250"].iloc[-1])
    assert frame["date"].is_unique
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python -m pytest tests/test_cyb_exit_research.py::test_load_research_frame_uses_no_flat_emotion_and_preheated_ma250 -q`

Expected: FAIL，错误包含`ModuleNotFoundError`或`cannot import name 'ResearchConfig'`。

- [ ] **Step 3: 实现配置、指标和验证**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ResearchConfig:
    index_path: Path = ROOT / "data/backtests/cyb_emotion_kdj/cyb_399006_daily.csv"
    breadth_path: Path = ROOT / "data/backtests/cyb_emotion_kdj/all_a_breadth_combined.csv"
    start_date: str = "2020-01-02"
    end_date: str = "2026-07-17"
    emotion_threshold: float = 15.0
    ma_filter: int = 250
    primary_cost: float = 0.001
    sensitivity_cost: float = 0.0015


def _chinese_sma(values: pd.Series, n: int) -> pd.Series:
    previous = 50.0
    output: list[float] = []
    for value in values.to_numpy(dtype=float):
        if not np.isnan(value):
            previous = ((n - 1) * previous + value) / n
        output.append(previous)
    return pd.Series(output, index=values.index, dtype=float)


def load_research_frame(config: ResearchConfig) -> pd.DataFrame:
    index = pd.read_csv(config.index_path, parse_dates=["date"])
    breadth = pd.read_csv(config.breadth_path, parse_dates=["date"])
    index = index.sort_values("date").drop_duplicates("date", keep="last")
    index["ma250"] = index["close"].rolling(250, min_periods=250).mean()
    for period in (5, 10, 20):
        index[f"ma{period}"] = index["close"].rolling(period, min_periods=period).mean()
    previous_close = index["close"].shift(1)
    true_range = pd.concat(
        [
            index["high"] - index["low"],
            (index["high"] - previous_close).abs(),
            (index["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    index["atr14"] = true_range.rolling(14, min_periods=14).mean()
    lowest = index["low"].rolling(9, min_periods=9).min()
    highest = index["high"].rolling(9, min_periods=9).max()
    index["rsv"] = 100 * (index["close"] - lowest) / (highest - lowest).replace(0, np.nan)
    index["k"] = _chinese_sma(index["rsv"], 3)
    index["d"] = _chinese_sma(index["k"], 3)
    index["j"] = 3 * index["k"] - 2 * index["d"]
    index["kdj_dead_cross"] = (index["k"] < index["d"]) & (
        index["k"].shift(1) >= index["d"].shift(1)
    )
    breadth = breadth[["date", "advancers", "decliners"]].copy()
    frame = index.merge(breadth, on="date", how="left", validate="one_to_one")
    frame["emotion"] = 100 * frame["advancers"] / (
        frame["advancers"] + frame["decliners"]
    )
    frame["entry_signal"] = (
        (frame["emotion"] < config.emotion_threshold)
        & (frame["close"] >= frame["ma250"])
    )
    frame = frame.loc[frame["date"].between(config.start_date, config.end_date)].reset_index(drop=True)
    required = ["open", "high", "low", "close", "emotion", "atr14", "k", "d", "j"]
    first_valid_ma250 = frame["ma250"].first_valid_index()
    ma250_has_internal_gap = (
        first_valid_ma250 is None
        or frame.loc[first_valid_ma250:, "ma250"].isna().any()
    )
    if (
        frame.empty
        or frame[required].isna().any().any()
        or not frame["date"].is_unique
        or ma250_has_internal_gap
    ):
        raise ValueError("研究数据缺失、重复或MA250预热后出现断点")
    frame["entry_signal"] = frame["entry_signal"].fillna(False)
    return frame
```

- [ ] **Step 4: 运行Task 1测试**

Run: `python -m pytest tests/test_cyb_exit_research.py -q`

Expected: PASS。

### Task 2: 无未来数据的退出状态机

**Files:**
- Modify: `src/ai_invest_advisor/cyb_exit_research.py`
- Modify: `tests/test_cyb_exit_research.py`

**Interfaces:**
- Consumes: Task 1的研究DataFrame
- Produces: `ExitSpec`、`simulate_exit(frame: pd.DataFrame, spec: ExitSpec, cost: float) -> tuple[pd.DataFrame, pd.DataFrame]`

- [ ] **Step 1: 写成交时点和不重叠测试**

```python
from ai_invest_advisor.cyb_exit_research import ExitSpec, simulate_exit


def test_conditional_exit_executes_next_open_and_ignores_overlapping_entries():
    frame = make_frame(
        opens=[100, 100, 102, 104, 103, 105],
        closes=[100, 101, 103, 102, 104, 106],
        entry_signals=[True, True, False, False, False, False],
        ma5=[99, 99, 100, 103, 103, 104],
    )
    _, trades = simulate_exit(
        frame,
        ExitSpec(name="ma5", family="ma", max_hold=5, ma_period=5, min_hold=1),
        cost=0.0,
    )

    assert len(trades) == 1
    assert trades.iloc[0]["entry_index"] == 1
    assert trades.iloc[0]["exit_signal_index"] == 3
    assert trades.iloc[0]["exit_index"] == 4
    assert trades.iloc[0]["exit_price"] == 103
```

测试辅助函数必须在同一测试文件中完整定义：

```python
def make_frame(opens, closes, entry_signals, ma5=None):
    size = len(opens)
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=size),
            "open": opens,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "entry_signal": entry_signals,
            "ma5": ma5 if ma5 is not None else close - 1,
            "ma10": close - 1,
            "ma20": close - 1,
            "atr14": 2.0,
            "k": 40.0,
            "d": 30.0,
            "kdj_dead_cross": False,
        }
    )
```

- [ ] **Step 2: 运行测试并确认因接口不存在而失败**

Run: `python -m pytest tests/test_cyb_exit_research.py::test_conditional_exit_executes_next_open_and_ignores_overlapping_entries -q`

Expected: FAIL，错误包含`cannot import name 'ExitSpec'`。

- [ ] **Step 3: 实现规则结构和状态机**

```python
@dataclass(frozen=True)
class ExitSpec:
    name: str
    family: str
    max_hold: int
    stop_loss: float | None = None
    take_profit: float | None = None
    trail_pct: float | None = None
    trail_activation: float = 0.0
    atr_multiple: float | None = None
    ma_period: int | None = None
    kdj_dead_cross: bool = False
    min_hold: int = 0


def _conditional_reason(row, *, entry_price, peak_close, spec, holding_days):
    if holding_days < spec.min_hold:
        return None
    trade_return = row["close"] / entry_price - 1
    if spec.stop_loss is not None and trade_return <= -spec.stop_loss:
        return "stop_loss"
    if spec.take_profit is not None and trade_return >= spec.take_profit:
        return "take_profit"
    if (
        spec.trail_pct is not None
        and peak_close / entry_price - 1 >= spec.trail_activation
        and row["close"] <= peak_close * (1 - spec.trail_pct)
    ):
        return "trailing_close"
    if (
        spec.atr_multiple is not None
        and row["close"] <= peak_close - spec.atr_multiple * row["atr14"]
    ):
        return "atr_trailing"
    if spec.ma_period is not None and row["close"] < row[f"ma{spec.ma_period}"]:
        return f"below_ma{spec.ma_period}"
    if spec.kdj_dead_cross and bool(row["kdj_dead_cross"]):
        return "kdj_dead_cross"
    return None
```

`simulate_exit`必须逐日维护`flat/pending_entry/holding/pending_exit`四种状态。入场信号日只安排下一日开盘；条件退出日只安排下一日开盘；到达`max_hold`时直接按当日收盘退出。交易表必须记录`entry_index, entry_date, entry_price, exit_signal_index, exit_index, exit_date, exit_price, exit_reason, holding_days, gross_return, net_return`。每日表必须记录`equity, position, action`。

- [ ] **Step 4: 增加固定期限、成本和净值乘积测试**

```python
def test_time_exit_uses_scheduled_close_and_equity_matches_trade_product():
    frame = make_frame(
        opens=[100, 101, 102, 103, 104],
        closes=[100, 102, 104, 106, 108],
        entry_signals=[True, False, False, False, False],
    )
    daily, trades = simulate_exit(
        frame,
        ExitSpec(name="hold3", family="time", max_hold=3),
        cost=0.001,
    )
    expected = 106 / 101 * 0.999**2 - 1
    assert trades.iloc[0]["net_return"] == pytest.approx(expected)
    assert daily["equity"].iloc[-1] - 1 == pytest.approx(expected)
```

- [ ] **Step 5: 运行状态机测试**

Run: `python -m pytest tests/test_cyb_exit_research.py -q`

Expected: PASS。

### Task 3: 候选规则、统计指标和成本敏感性

**Files:**
- Modify: `src/ai_invest_advisor/cyb_exit_research.py`
- Modify: `tests/test_cyb_exit_research.py`

**Interfaces:**
- Produces: `build_exit_specs() -> list[ExitSpec]`
- Produces: `summarize_run(daily, trades, spec, cost) -> dict[str, object]`
- Produces: `run_grid(frame, config) -> tuple[pd.DataFrame, pd.DataFrame]`

- [ ] **Step 1: 写候选空间边界测试**

```python
def test_candidate_grid_contains_only_approved_rule_families():
    specs = build_exit_specs()
    families = {spec.family for spec in specs}
    assert families == {"time", "threshold", "trailing", "atr", "ma", "kdj", "combo"}
    assert all(5 <= spec.max_hold <= 15 for spec in specs)
    assert len({spec.name for spec in specs}) == len(specs)
    assert not any(
        spec.take_profit is not None
        and spec.trail_pct is not None
        and spec.atr_multiple is not None
        for spec in specs
    )
```

- [ ] **Step 2: 运行候选空间测试并确认失败**

Run: `python -m pytest tests/test_cyb_exit_research.py::test_candidate_grid_contains_only_approved_rule_families -q`

Expected: FAIL，错误包含`build_exit_specs`未定义。

- [ ] **Step 3: 实现候选生成**

`build_exit_specs`必须生成：

- 时间退出：`max_hold=5..15`
- 固定止损：`stop_loss in (0.04, 0.06, 0.08)` × `max_hold in (9, 12, 15)`
- 固定止盈：`take_profit in (0.05, 0.08, 0.10, 0.12)` × `max_hold in (9, 12, 15)`
- 最高收盘回撤：`trail_pct in (0.04, 0.06, 0.08, 0.10)` × `trail_activation in (0.0, 0.03, 0.05)` × `max_hold in (9, 12, 15)`
- ATR：`atr_multiple in (2.0, 2.5, 3.0)` × `max_hold in (9, 12, 15)`
- 均线：`ma_period in (5, 10, 20)` × `min_hold in (2, 3)` × `max_hold in (9, 12, 15)`
- KDJ：`min_hold in (2, 3)` × `max_hold in (9, 12, 15)`
- 组合仅限“KDJ或MA + 8%最高收盘回撤 + 最长持有期”。

- [ ] **Step 4: 实现汇总和网格运行**

`summarize_run`计算完整交易数、累计收益、年化收益、最大回撤、Calmar、胜率、平均/中位数、最好/最差单笔、平均持有日。`run_grid`对每条规则分别运行`cost=0, 0.001, 0.0015`，汇总表每行一个`spec_name + cost`，逐笔表增加`spec_name + cost`。

- [ ] **Step 5: 写并运行成本单调性测试**

```python
def test_cost_sensitivity_cannot_increase_total_return():
    size = 40
    close = pd.Series(100 + np.arange(size) * 0.5)
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=size),
            "open": close - 0.1,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "entry_signal": [True] + [False] * (size - 1),
            "ma5": close - 1,
            "ma10": close - 1,
            "ma20": close - 1,
            "atr14": 2.0,
            "k": 40.0,
            "d": 30.0,
            "kdj_dead_cross": False,
        }
    )
    summary, _ = run_grid(frame, ResearchConfig())
    pivot = summary.pivot(index="spec_name", columns="cost", values="total_return")
    assert (pivot[0.0] >= pivot[0.001]).all()
    assert (pivot[0.001] >= pivot[0.0015]).all()
```

Run: `python -m pytest tests/test_cyb_exit_research.py -q`

Expected: PASS。

### Task 4: 稳健性筛选与留一年检验

**Files:**
- Modify: `src/ai_invest_advisor/cyb_exit_research.py`
- Modify: `tests/test_cyb_exit_research.py`

**Interfaces:**
- Produces: `rank_candidates(summary, trades, config) -> pd.DataFrame`
- Produces: `leave_one_year_out(frame, specs, config) -> pd.DataFrame`

- [ ] **Step 1: 写约束筛选测试**

```python
def test_rank_candidates_enforces_return_and_drawdown_constraints():
    summary = pd.DataFrame(
        [
            {"spec_name": "high_return_high_dd", "cost": 0.001, "total_return": 1.50, "max_drawdown": -0.25, "calmar": 1.0, "trades": 30},
            {"spec_name": "qualified", "cost": 0.001, "total_return": 1.10, "max_drawdown": -0.18, "calmar": 1.2, "trades": 30},
            {"spec_name": "low_return", "cost": 0.001, "total_return": 0.90, "max_drawdown": -0.10, "calmar": 1.4, "trades": 30},
        ]
    )
    ranked = rank_candidates(summary, pd.DataFrame(), ResearchConfig())
    assert ranked["spec_name"].tolist() == ["qualified"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_cyb_exit_research.py::test_rank_candidates_enforces_return_and_drawdown_constraints -q`

Expected: FAIL，错误包含`rank_candidates`未定义。

- [ ] **Step 3: 实现筛选、年度贡献和邻域稳定性**

`rank_candidates`必须：

- 只取`cost == 0.001`
- 要求`total_return > 1.0157`、`max_drawdown >= -0.20`
- 要求敏感性成本0.0015的同名规则`total_return > 0`
- 计算有交易年份数、盈利年份数、最大单年利润贡献
- 标记`neighbor_stable`：同规则族相邻阈值或相邻最长持有期中至少一个也满足回撤约束且收益方向一致
- 排序键为`total_return desc, calmar desc, abs(max_drawdown) asc, complexity asc`

- [ ] **Step 4: 实现留一年检验**

对2020至2026逐年剔除后，使用同一规则参数重新运行。输出`excluded_year, spec_name, total_return, max_drawdown, rank`，不允许在每个子样本重新调参后只保存赢家。

- [ ] **Step 5: 运行稳健性测试**

Run: `python -m pytest tests/test_cyb_exit_research.py -q`

Expected: PASS。

### Task 5: CLI、报告和最终验证

**Files:**
- Create: `scripts/research_cyb_exit_rules.py`
- Create: `tests/test_cyb_exit_research_cli.py`
- Generate: `reports/backtests/cyb_exit_rule_research/rule_summary.csv`
- Generate: `reports/backtests/cyb_exit_rule_research/rule_trades.csv`
- Generate: `reports/backtests/cyb_exit_rule_research/qualified_rules.csv`
- Generate: `reports/backtests/cyb_exit_rule_research/leave_one_year_out.csv`
- Generate: `reports/backtests/cyb_exit_rule_research/report.md`
- Generate: `reports/backtests/cyb_exit_rule_research/equity_comparison.png`
- Generate: `reports/backtests/cyb_exit_rule_research/metadata.json`

**Interfaces:**
- Consumes: Tasks 1至4的公开函数
- Produces: 可复现研究文件和读者报告

- [ ] **Step 1: 写CLI烟雾测试**

```python
def test_cli_writes_required_outputs(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/research_cyb_exit_rules.py",
            "--output-dir",
            str(tmp_path),
            "--limit-specs",
            "3",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for name in (
        "rule_summary.csv",
        "rule_trades.csv",
        "qualified_rules.csv",
        "leave_one_year_out.csv",
        "report.md",
        "equity_comparison.png",
        "metadata.json",
    ):
        assert (tmp_path / name).exists()
    assert "qualified_rules" in completed.stdout
```

- [ ] **Step 2: 运行CLI测试并确认脚本不存在**

Run: `python -m pytest tests/test_cyb_exit_research_cli.py -q`

Expected: FAIL，返回码非0且提示脚本不存在。

- [ ] **Step 3: 实现CLI和报告**

CLI参数必须包括`--start, --end, --primary-cost, --sensitivity-cost, --output-dir, --limit-specs`。报告必须明确列出：

- 数据源和生成时间；
- 当前固定9日基准；
- 合格规则排行榜；
- 推荐规则与备选规则；
- 年度表现、留一年排名和参数邻域；
- 成本敏感性；
- 同收盘成交、样本拼接、小样本和多重测试风险。

图表只比较基准、推荐规则和最多两个备选规则，包含净值与回撤两栏。

- [ ] **Step 4: 运行完整研究**

Run: `python scripts/research_cyb_exit_rules.py`

Expected: 输出JSON路径清单，所有七个报告文件存在。

- [ ] **Step 5: 执行最终验证**

Run: `python -m pytest -q`

Expected: 现有69项测试加新增测试全部通过，零失败。

Run: `python scripts/research_cyb_exit_rules.py --primary-cost 0.001 --sensitivity-cost 0.0015`

Expected: `qualified_rules.csv`中每条规则满足`total_return > 1.0157`和`max_drawdown >= -0.20`；若无规则满足，报告必须明确写“未找到同时提高收益且满足回撤约束的卖出规则”，不得降低门槛后宣称成功。
