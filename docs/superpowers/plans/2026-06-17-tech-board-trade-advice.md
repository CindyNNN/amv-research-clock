# Tech Board Trade Advice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only technology-board trading-advice command that converts existing dashboard cache data into explainable Markdown trade suggestions.

**Architecture:** Keep the feature inside the dashboard package because it consumes dashboard cache outputs. Add a pure `trade_advice` module for classification, report rendering, and file writing; wire it into the existing argparse CLI with no broker or order-placement capability.

**Tech Stack:** Python 3.10+, pandas, argparse, pytest.

## Global Constraints

- No automatic trading, broker login, account credential access, or order placement.
- Use existing dashboard cache files as the data source.
- All output must include `This is research support, not financial advice.`
- Tests must run offline.

---

### Task 1: Trade Advice Engine

**Files:**
- Create: `src/ai_invest_advisor/dashboard/trade_advice.py`
- Test: `tests/test_trade_advice.py`

**Interfaces:**
- Consumes: pandas DataFrame columns from `tech_board_scores.csv`: `board_name`, `theme`, `score`, `ret5`, `net_amount`, `risk_flags`, `leader`, `leader_pct_change`.
- Produces: `TradeAdviceReport`, `classify_market_stance(market_heat: float, tech_scores: pd.DataFrame) -> MarketTradeStance`, `build_trade_advice(scores: pd.DataFrame, market_heat: float, data_warnings: list[str] | None = None, top: int = 8) -> TradeAdviceReport`.

- [ ] **Step 1: Write failing tests**

Create tests that assert:

```python
def test_build_trade_advice_routes_focus_wait_and_avoid():
    scores = pd.DataFrame(
        [
            {"board_name": "AI", "theme": "AI", "score": 82, "ret5": 5, "net_amount": 20, "risk_flags": "无明显异常", "leader": "A", "leader_pct_change": 6},
            {"board_name": "Robot", "theme": "Robot", "score": 75, "ret5": 16, "net_amount": 30, "risk_flags": "5日涨幅偏热", "leader": "B", "leader_pct_change": 10},
            {"board_name": "Weak", "theme": "Weak", "score": 42, "ret5": -4, "net_amount": -15, "risk_flags": "资金流出", "leader": "C", "leader_pct_change": -2},
        ]
    )
    report = build_trade_advice(scores, market_heat=76, top=5)
    assert report.stance.label == "积极进攻"
    assert [item.board_name for item in report.focus] == ["AI"]
    assert [item.board_name for item in report.wait_for_pullback] == ["Robot"]
    assert [item.board_name for item in report.reduce_or_avoid] == ["Weak"]
```

Also test defensive stance when average score is weak or net amount is negative.

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
pytest tests/test_trade_advice.py -q
```

Expected: fails because `ai_invest_advisor.dashboard.trade_advice` does not exist.

- [ ] **Step 3: Implement minimal engine**

Implement dataclasses:

```python
@dataclass(frozen=True)
class MarketTradeStance:
    label: str
    allocation_hint: str
    reasons: list[str]

@dataclass(frozen=True)
class BoardTradeAdvice:
    board_name: str
    theme: str
    action: str
    score: float
    net_amount: float
    ret5: float
    leader: str
    leader_pct_change: float
    reasons: list[str]
    risk_flags: str

@dataclass(frozen=True)
class TradeAdviceReport:
    generated_at: str
    stance: MarketTradeStance
    focus: list[BoardTradeAdvice]
    wait_for_pullback: list[BoardTradeAdvice]
    reduce_or_avoid: list[BoardTradeAdvice]
    data_warnings: list[str]
```

Implement classification using the design thresholds.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_trade_advice.py -q
```

Expected: pass.

### Task 2: Markdown Rendering and Cache Loader

**Files:**
- Modify: `src/ai_invest_advisor/dashboard/trade_advice.py`
- Test: `tests/test_trade_advice.py`

**Interfaces:**
- Produces: `render_trade_advice_markdown(report: TradeAdviceReport, data_status: dict[str, object] | None = None) -> str`
- Produces: `generate_trade_advice_report(cache_dir: Path, output_dir: Path, top: int = 8) -> Path`

- [ ] **Step 1: Write failing tests**

Add tests that:

```python
def test_render_trade_advice_markdown_includes_required_sections():
    report = build_trade_advice(sample_scores, market_heat=76, top=5)
    markdown = render_trade_advice_markdown(report, {"status": "cached"})
    assert "# 科技板块交易建议" in markdown
    assert "## 可关注" in markdown
    assert "## 等待回调" in markdown
    assert "## 减仓/回避" in markdown
    assert "This is research support, not financial advice." in markdown
```

Also test `generate_trade_advice_report` with a temporary cache directory containing `tech_board_scores.csv` and `sentiment_history.csv`.

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
pytest tests/test_trade_advice.py -q
```

Expected: fails because rendering and file generation do not exist.

- [ ] **Step 3: Implement rendering and generation**

Read required cache files, fall back to market heat `0.0` with a warning when sentiment is missing, load optional `data_status.json`, write a dated Markdown report named `YYYY-MM-DD-tech-trade-advice.md`, and return the output path.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_trade_advice.py -q
```

Expected: pass.

### Task 3: CLI Wiring

**Files:**
- Modify: `src/ai_invest_advisor/cli.py`
- Modify: `tests/test_dashboard_cli.py`

**Interfaces:**
- Produces: argparse command `trade-advice`.
- Consumes: `generate_trade_advice_report(cache_dir: Path, output_dir: Path, top: int) -> Path`.

- [ ] **Step 1: Write failing CLI parser test**

Add:

```python
trade_advice = parser.parse_args(["trade-advice", "--top", "5", "--print"])
assert trade_advice.command == "trade-advice"
assert trade_advice.top == 5
assert trade_advice.print_report is True
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
pytest tests/test_dashboard_cli.py -q
```

Expected: fails because `trade-advice` is not registered.

- [ ] **Step 3: Implement CLI command**

Add a `trade_advice(args: argparse.Namespace) -> None` handler that writes the report, prints the output path, and prints report contents when `args.print_report` is true.

- [ ] **Step 4: Run test and verify GREEN**

Run:

```powershell
pytest tests/test_dashboard_cli.py -q
```

Expected: pass.

### Task 4: Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
pytest tests/test_trade_advice.py tests/test_dashboard_cli.py -q
```

Expected: pass.

- [ ] **Step 2: Run full offline test suite**

Run:

```powershell
pytest -q
```

Expected: pass.

- [ ] **Step 3: Run CLI smoke command if cache exists**

Run:

```powershell
python -m ai_invest_advisor.cli trade-advice --cache-dir data/dashboard/latest --output-dir reports/trade_advice --top 5
```

Expected: writes a Markdown report or prints a clear missing-cache message telling the user to run `refresh-dashboard`.

## Self-Review

Spec coverage: the plan implements the tech-board-only scope, cache inputs, output report, CLI command, warning behavior, and no-trading boundary.

Placeholder scan: no TBD/TODO placeholders are present.

Type consistency: dataclass and function names match across all tasks.
