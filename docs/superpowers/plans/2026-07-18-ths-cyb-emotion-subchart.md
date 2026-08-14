# 同花顺远航版创业板情绪副图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每日创业板情绪监控生成远航版可读取的历史副图数据，并提供可直接粘贴的 Python 副图脚本。

**Architecture:** 新增纯函数导出模块，从完整历史逐日回放现有状态机并原子写入精简 CSV。每日监控在行情和策略计算成功后更新该文件。远航版脚本读取 CSV，通过连续收盘价匹配对齐当前日线，再绘制情绪、阈值、持仓线和买卖文字。

**Tech Stack:** Python 3.11、pandas、pytest、同花顺远航版 Python 指标 API。

## Global Constraints

- 策略规则必须复用 `evaluate_snapshot`，不得在导出器中另写一套判断。
- 副图只消费本地文件，不联网、不读取邮箱配置。
- 外部 CSV 必须原子替换，字段固定为 `date,close,emotion,j,signal,holding`。
- 无法可靠对齐时必须拒绝绘图并显示状态，不允许猜测。
- 仅用于投资研究和提醒，不执行交易。

---

### Task 1: 历史副图数据导出

**Files:**
- Create: `src/ai_invest_advisor/ths_indicator_export.py`
- Create: `tests/test_ths_indicator_export.py`

**Interfaces:**
- Consumes: `evaluate_snapshot(ModelState, MarketSnapshot)` 和 `snapshot_from_row(pd.Series)`。
- Produces: `build_subchart_frame(history: pd.DataFrame) -> pd.DataFrame`、`write_subchart_csv_atomic(frame: pd.DataFrame, path: Path) -> None`。

- [ ] **Step 1: Write the failing tests**

```python
def test_build_subchart_frame_reuses_state_machine(sample_history):
    result = build_subchart_frame(sample_history)
    assert result.columns.tolist() == [
        "date", "close", "emotion", "j", "signal", "holding"
    ]
    assert result["signal"].tolist() == ["BUY", "HOLD", "SELL"]
    assert result["holding"].tolist() == [1, 1, 0]

def test_write_subchart_csv_atomic(tmp_path, sample_history):
    path = tmp_path / "subchart.csv"
    write_subchart_csv_atomic(build_subchart_frame(sample_history), path)
    assert path.read_text(encoding="utf-8").splitlines()[0] == (
        "date,close,emotion,j,signal,holding"
    )
    assert not path.with_suffix(".csv.tmp").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ths_indicator_export.py -q`
Expected: FAIL because `ai_invest_advisor.ths_indicator_export` does not exist.

- [ ] **Step 3: Implement the exporter**

Implement `build_subchart_frame` by sorting unique dates, starting from `ModelState.flat()`, evaluating every row through `evaluate_snapshot`, and recording the resulting signal and holding flag. Implement atomic UTF-8 CSV replacement with a sibling `.tmp` file.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/test_ths_indicator_export.py -q`
Expected: PASS.

### Task 2: Integrate export into every monitor run

**Files:**
- Modify: `scripts/run_cyb_signal_monitor.py`
- Modify: `tests/test_cyb_signal_monitor_cli.py`

**Interfaces:**
- Consumes: `build_subchart_frame` and `write_subchart_csv_atomic`.
- Produces: default file `data/monitor/ths_cyb_emotion_subchart.csv` on successful market-data processing.

- [ ] **Step 1: Write the failing integration test**

```python
def test_run_monitor_exports_ths_subchart_on_dry_run(tmp_path, sample_history):
    export_path = tmp_path / "ths.csv"
    result = run_monitor(
        dry_run=True,
        state_path=tmp_path / "state.json",
        export_path=export_path,
        now=NOW,
        load_history=lambda **_: sample_history,
    )
    assert result == 0
    assert export_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cyb_signal_monitor_cli.py -q`
Expected: FAIL because `run_monitor` has no `export_path` argument.

- [ ] **Step 3: Add the integration**

Add `DEFAULT_THS_EXPORT_PATH`, an injectable `export_path`, and write the subchart file after history validation and before email construction. Add CLI option `--ths-export`.

- [ ] **Step 4: Run monitor tests**

Run: `python -m pytest tests/test_cyb_signal_monitor_cli.py tests/test_ths_indicator_export.py -q`
Expected: PASS.

### Task 3: Create the remote-voyage Python subchart

**Files:**
- Create: `ths_indicators/cyb_emotion_subchart.py`
- Create: `tests/test_ths_indicator_script.py`

**Interfaces:**
- Consumes: absolute CSV path and remote-voyage globals `total`, `get`, `save`, `draw.curve`, `text`.
- Produces: curves `情绪`, `冰点15`, `持仓状态` and BUY/SELL labels.

- [ ] **Step 1: Write static contract tests**

```python
def test_indicator_uses_expected_remote_voyage_api():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'get("收盘价", i)' in source
    assert 'draw.curve("情绪"' in source
    assert 'draw.curve("冰点15"' in source
    assert 'text(' in source
    assert "requests" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ths_indicator_script.py -q`
Expected: FAIL because the indicator script does not exist.

- [ ] **Step 3: Implement parsing, alignment, and drawing**

Read the fixed CSV path with built-in `open`, parse finite numeric rows, find the best alignment using 3—5 consecutive close matches within 0.05 points, and only then call `save`. Draw the emotion curve, constant 15 line, scaled holding state at 100/0, BUY text at emotion + 5, SELL text at emotion - 5, and the latest status text. If file read or alignment fails, draw only the 15 line and a red status message.

- [ ] **Step 4: Run the static contract tests**

Run: `python -m pytest tests/test_ths_indicator_script.py -q`
Expected: PASS.

### Task 4: Documentation and full verification

**Files:**
- Modify: `docs/cyb_email_monitor_usage.md`

**Interfaces:**
- Consumes: completed exporter and indicator script.
- Produces: paste/install/update instructions and risk disclosure.

- [ ] **Step 1: Add exact installation instructions**

Document the generated CSV path, the indicator source path, how to create a Python副图 in远航版, and that the chart must be 创业板指399006日线.

- [ ] **Step 2: Run the complete suite**

Run: `python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 3: Generate a real dry-run export**

Run: `python scripts/run_cyb_signal_monitor.py --dry-run`
Expected: exit 0, status preview printed, and `data/monitor/ths_cyb_emotion_subchart.csv` updated through the latest completed trading day.

- [ ] **Step 4: Inspect the output contract**

Run: `Get-Content data/monitor/ths_cyb_emotion_subchart.csv -TotalCount 3; Get-Content data/monitor/ths_cyb_emotion_subchart.csv -Tail 3`
Expected: exact six-column header, sorted dates, finite emotion/J values where available, and legal signal values.
