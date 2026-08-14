# 通达信创业板情绪副图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有创业板情绪历史和策略信号推送到本机 TdxQuant，并提供通达信日线副图公式。

**Architecture:** 新增一个不依赖通达信进程的数据载荷模块，负责严格校验 CSV 并生成六列 TQ 序列；独立 CLI 在运行时加载本机 `tqcenter` 并调用 `send_bt_data`。公式通过固定 `SIGNALS_TQ` ID 显示情绪、冰点、持仓和买卖标记；批处理仅在邮件监控成功后尝试推送。

**Tech Stack:** Python 3.11、pandas、pytest、TdxQuant `tqcenter`、通达信公式语言、Windows CMD。

## Global Constraints

- 证券代码固定为 `399006.SZ`，周期语义固定为完整日线。
- 数据协议固定为情绪、买入、卖出、持仓、J值、收盘价六列，ID为1—6。
- `count` 必须等于全部有效历史行数。
- 不导入账号、不调用任何委托或交易函数。
- 通达信推送失败不得改变邮件状态、行情缓存或历史CSV。
- 当前 `.git` 目录无有效仓库元数据，不能创建工作树或提交；使用现有工作区完成并验证。

---

### Task 1: 构建并校验 TQ 历史载荷

**Files:**
- Create: `src/ai_invest_advisor/tdx_indicator_push.py`
- Create: `tests/test_tdx_indicator_push.py`

**Interfaces:**
- Produces: `TdxPayload(time_list: list[str], data_list: list[list[str]])`。
- Produces: `build_tdx_payload(frame: pd.DataFrame) -> TdxPayload`。
- Produces: `load_tdx_payload(path: Path) -> TdxPayload`。

- [ ] **Step 1: Write failing payload tests**

```python
def test_build_payload_uses_six_fixed_fields():
    payload = build_tdx_payload(sample_frame())
    assert payload.time_list == ["20260716", "20260717"]
    assert payload.data_list[-1] == [
        "8.728721", "1", "0", "1", "6.310737", "3428.630000"
    ]

@pytest.mark.parametrize("mutation", ["duplicate_date", "bad_signal", "nan"])
def test_build_payload_rejects_invalid_history(mutation):
    with pytest.raises(TdxPayloadError):
        build_tdx_payload(invalid_frame(mutation))
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_tdx_indicator_push.py -q`
Expected: collection error because the module does not exist.

- [ ] **Step 3: Implement minimal payload conversion**

Validate exact required fields, parse strictly increasing unique dates, require finite close/emotion/J, restrict emotion to 0—100, signals to BUY/SELL/HOLD/FLAT, and holding to 0/1. Format the date as `YYYYMMDD`, continuous values with six decimals, and binary fields as `0` or `1`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_tdx_indicator_push.py -q`
Expected: PASS.

### Task 2: Add isolated TdxQuant sender and CLI

**Files:**
- Modify: `src/ai_invest_advisor/tdx_indicator_push.py`
- Create: `scripts/push_tdx_cyb_indicator.py`
- Modify: `tests/test_tdx_indicator_push.py`
- Create: `tests/test_tdx_indicator_cli.py`

**Interfaces:**
- Produces: `send_tdx_payload(tq, payload, stock_code="399006.SZ") -> dict`。
- CLI options: `--data`, `--tdx-home`。

- [ ] **Step 1: Write failing sender test**

```python
def test_sender_uses_full_count_and_fixed_symbol():
    fake = FakeTq({"ErrorId": "0"})
    result = send_tdx_payload(fake, sample_payload)
    assert result["ErrorId"] == "0"
    assert fake.call["stock_code"] == "399006.SZ"
    assert fake.call["count"] == len(sample_payload.time_list)
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_tdx_indicator_push.py::test_sender_uses_full_count_and_fixed_symbol -q`
Expected: FAIL because `send_tdx_payload` is missing.

- [ ] **Step 3: Implement sender**

Call only `tq.send_bt_data`. Reject empty payloads, mismatched time/data lengths, non-dict responses, and responses whose `ErrorId` is not string `"0"`.

- [ ] **Step 4: Write failing CLI tests**

Test success with an injected fake tqcenter module and test missing `tqcenter.py` returns a clear nonzero result without importing any trading module.

- [ ] **Step 5: Implement CLI**

Insert `<tdx-home>\PYPlugins\user` into `sys.path`, import `tqcenter` only inside `main`, call `tq.initialize(__file__)`, send the full CSV, print rows/date range, and always call `tq.close()` after a successful initialization.

- [ ] **Step 6: Verify sender and CLI**

Run: `python -m pytest tests/test_tdx_indicator_push.py tests/test_tdx_indicator_cli.py -q`
Expected: PASS.

### Task 3: Create the通达信副图公式

**Files:**
- Create: `tdx_indicators/CYBQX_创业板情绪副图.txt`
- Create: `tests/test_tdx_indicator_formula.py`

**Interfaces:**
- Consumes: `SIGNALS_TQ(1..6, 2)`。
- Produces: 情绪、冰点15、持仓状态、买卖图标和文字。

- [ ] **Step 1: Write failing static formula test**

```python
def test_formula_uses_fixed_signal_ids_and_no_trading_calls():
    source = FORMULA.read_text(encoding="utf-8")
    for signal_id in range(1, 7):
        assert f"SIGNALS_TQ({signal_id},2)" in source
    assert "DRAWICON" in source
    assert "DRAWTEXT" in source
    assert "ORDER_STOCK" not in source.upper()
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_tdx_indicator_formula.py -q`
Expected: FAIL because the formula file does not exist.

- [ ] **Step 3: Implement the formula**

Use ID1 as a yellow thick emotion line, constant 15 as a white dotted line, ID4 multiplied by100 as a blue position line, ID2/ID3 as buy/sell conditions, and ID5/ID6 as `NODRAW` diagnostics. Draw buy icon/text above emotion and sell icon/text below emotion.

- [ ] **Step 4: Verify formula contract**

Run: `python -m pytest tests/test_tdx_indicator_formula.py -q`
Expected: PASS.

### Task 4: Batch integration, documentation, and end-to-end verification

**Files:**
- Create: `push_tdx_cyb_indicator.bat`
- Modify: `run_cyb_signal_monitor.bat`
- Modify: `docs/cyb_email_monitor_usage.md`
- Modify: `tests/test_cyb_monitor_files.py`

**Interfaces:**
- Standalone batch pushes the existing CSV.
- Main batch runs TQ push only when the monitor command succeeds.

- [ ] **Step 1: Write failing batch contract tests**

Check that both batch files are ASCII, use `%~dp0`, set `PYTHONPATH`, and that the main batch connects commands with `&&` so a failed monitor skips TQ push.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_cyb_monitor_files.py -q`
Expected: FAIL because the standalone batch is missing and the main batch does not call the push CLI.

- [ ] **Step 3: Implement ASCII-safe batch files**

Keep each batch as a one-line ASCII command to avoid the prior UTF-8/LF CMD parsing issue. The standalone batch runs only the push CLI; the main batch runs monitor `&&` push, then pauses.

- [ ] **Step 4: Add exact installation instructions**

Document opening `C:\Softwares\new_tdx\tdxw.exe`, creating a technical indicator named `CYBQX`, choosing副图, pasting the formula, running the push batch, and refreshing `399006` 日线.

- [ ] **Step 5: Run all automated verification**

Run: `python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Validate real payload without TDX mutation**

Run: `python -c "from pathlib import Path; from ai_invest_advisor.tdx_indicator_push import load_tdx_payload; p=load_tdx_payload(Path('data/monitor/ths_cyb_emotion_subchart.csv')); print(len(p.time_list), p.time_list[0], p.time_list[-1], p.data_list[-1])"`
Expected: `1584 20200102 20260717` followed by the six latest values.

- [ ] **Step 7: Attempt real TQ push**

First check whether `tdxw.exe` is running. If it is running, execute `python scripts/push_tdx_cyb_indicator.py`; success requires `ErrorId == "0"`. If it is not running, do not launch or log in automatically; report that the implementation is ready and provide the standalone batch for the user to run after opening TDX.
