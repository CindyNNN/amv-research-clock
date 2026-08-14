# 创业板盘中预估与收盘严谨双模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有创业板信号监控扩展为14:40盘中只读预估和15:20收盘正式确认两种模式，并安装两个互不冲突的Windows计划任务。

**Architecture:** 行情模块通过显式运行模式决定是否保留当日动态日K及是否写正式缓存；监控模块在盘中模式只基于正式状态副本计算邮件，不保存状态或副图，收盘模式维持现有正式写入链路。两个BAT分别调用模式参数，PowerShell安装器负责可重复创建Windows任务计划。

**Tech Stack:** Python 3.11、pandas、pytest、Windows Batch、PowerShell ScheduledTasks。

## Global Constraints

- `intraday` 只用于预估，禁止写策略状态、正式行情缓存、正式副图CSV或通达信。
- `close` 是唯一正式状态来源，邮件成功后才保存状态。
- 14:40邮件必须明确标记“盘中预估”；15:20邮件必须明确标记“收盘确认”。
- 盘中缺少当天数据时发送不可用邮件，不得用上一交易日生成当天预估。
- 现有 `run_cyb_signal_monitor.bat` 保持为收盘严谨兼容入口。
- 两个Windows任务均为周一至周五、交互式用户Cindy、错过后补跑、允许唤醒、最长30分钟。
- 不增加券商接口、自动下单或交易执行。
- 当前目录不是有效Git仓库；每项任务以测试和审查为检查点，不执行提交。

---

### Task 1: 行情数据双模式与缓存隔离

**Files:**
- Modify: `src/ai_invest_advisor/cyb_market_data.py`
- Modify: `tests/test_cyb_market_data.py`

**Interfaces:**
- Produces: `RunMode = Literal["intraday", "close"]`
- Produces: `select_index_rows(frame: pd.DataFrame, *, as_of: date, now: datetime, mode: RunMode) -> pd.DataFrame`
- Updates: `load_complete_history(..., mode: RunMode = "close") -> pd.DataFrame`
- Preserves: `select_completed_index_rows(...)` as a compatibility wrapper for `mode="close"`

- [ ] **Step 1: Write the failing row-selection tests**

```python
from ai_invest_advisor.cyb_market_data import select_index_rows


def test_intraday_mode_keeps_current_dynamic_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )
    result = select_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        mode="intraday",
    )
    assert result["date"].max().date() == date(2026, 7, 17)


def test_close_mode_before_1515_excludes_current_dynamic_day():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "close": [100.0, 101.0],
        }
    )
    result = select_index_rows(
        frame,
        as_of=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        mode="close",
    )
    assert result["date"].max().date() == date(2026, 7, 16)
```

- [ ] **Step 2: Run the row-selection tests and verify RED**

Run:

```text
python -m pytest tests/test_cyb_market_data.py::test_intraday_mode_keeps_current_dynamic_day tests/test_cyb_market_data.py::test_close_mode_before_1515_excludes_current_dynamic_day -q
```

Expected: FAIL because `select_index_rows` does not exist.

- [ ] **Step 3: Implement explicit row selection**

Add validation and preserve the existing wrapper:

```python
RunMode = Literal["intraday", "close"]


def select_index_rows(frame, *, as_of, now, mode):
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported run mode: {mode}")
    result = frame.loc[pd.to_datetime(frame["date"]).dt.date <= as_of].copy()
    if (
        mode == "close"
        and as_of == now.date()
        and now.timetz().replace(tzinfo=None) < SHANGHAI_CLOSE_BUFFER
    ):
        result = result.loc[pd.to_datetime(result["date"]).dt.date < as_of]
    if result.empty:
        raise MarketDataError("指定日期之前没有可用创业板指数日线")
    return result.reset_index(drop=True)


def select_completed_index_rows(frame, *, as_of, now):
    return select_index_rows(
        frame,
        as_of=as_of,
        now=now,
        mode="close",
    )
```

- [ ] **Step 4: Write failing cache-isolation tests**

Use `MarketDataPaths` pointing at `tmp_path`, a fake index payload containing the current date, and a fake breadth payload. Assert:

```python
intraday = load_complete_history(
    paths=paths,
    as_of=date(2026, 7, 17),
    now=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
    fetch_json=fake_fetch_json,
    mode="intraday",
)
assert intraday["date"].max().date() == date(2026, 7, 17)
assert not paths.index_cache.exists()
assert original_breadth_bytes == paths.breadth_cache.read_bytes()

close = load_complete_history(
    paths=paths,
    as_of=date(2026, 7, 17),
    now=datetime(2026, 7, 17, 15, 20, tzinfo=SHANGHAI),
    fetch_json=fake_fetch_json,
    mode="close",
)
assert paths.index_cache.exists()
assert date(2026, 7, 17) in set(pd.read_csv(paths.breadth_cache)["date"])
```

The fixture must include at least 25 historical rows so MA20 and KDJ validation are meaningful.

- [ ] **Step 5: Run cache-isolation tests and verify RED**

Run:

```text
python -m pytest tests/test_cyb_market_data.py -q
```

Expected: FAIL because `load_complete_history` has no `mode` and writes caches unconditionally.

- [ ] **Step 6: Implement mode-aware in-memory fetch and persistence**

In `load_complete_history`:

```python
index = parse_index_payload(fetch_json(build_index_url(as_of)))
index = select_index_rows(index, as_of=as_of, now=now, mode=mode)
if mode == "close":
    _write_csv_atomic(index, paths.index_cache)

# Fetch missing breadth for rows selected into this run.
downloaded = _fetch_missing_breadth(...)
# Merge `downloaded` into the in-memory breadth frame for both modes.
if mode == "close" and not downloaded.empty:
    _write_csv_atomic(breadth, paths.breadth_cache)
```

Validate `mode` before any network or file write. Do not add a separate intraday cache.

- [ ] **Step 7: Verify Task 1**

Run:

```text
python -m pytest tests/test_cyb_market_data.py -q
python -m pytest -q
```

Expected: all tests PASS with zero failures.

### Task 2: 盘中状态隔离、不可用分支与邮件语义

**Files:**
- Modify: `scripts/run_cyb_signal_monitor.py`
- Modify: `src/ai_invest_advisor/qq_email.py`
- Modify: `tests/test_cyb_signal_monitor_cli.py`
- Modify: `tests/test_qq_email.py`

**Interfaces:**
- Updates: `run_monitor(*, mode: RunMode = "close", ...) -> int`
- Updates: `build_status_message(..., mode: RunMode) -> EmailMessage`
- Produces: `build_intraday_unavailable_message(*, config, run_at, latest_date) -> EmailMessage`
- Updates: `build_error_message(..., mode: RunMode = "close") -> EmailMessage`

- [ ] **Step 1: Write failing intraday state-isolation test**

```python
def test_intraday_sends_preview_without_saving_state_or_export(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "subchart.csv"
    original = ModelState.flat()
    save_state_atomic(state_path, original)
    before = state_path.read_bytes()
    sent = []

    result = run_monitor(
        mode="intraday",
        dry_run=False,
        state_path=state_path,
        export_path=export_path,
        now=INTRADAY_NOW,
        load_history=buy_history,
        send=lambda config, message: sent.append(message),
        environ=EMAIL_ENV,
    )

    assert result == 0
    assert state_path.read_bytes() == before
    assert not export_path.exists()
    assert "盘中预估" in sent[0]["Subject"]
    assert "不是正式收盘信号" in sent[0].get_content()
```

- [ ] **Step 2: Run the isolation test and verify RED**

Run:

```text
python -m pytest tests/test_cyb_signal_monitor_cli.py::test_intraday_sends_preview_without_saving_state_or_export -q
```

Expected: FAIL because `run_monitor` has no `mode` and saves state/exports.

- [ ] **Step 3: Implement mode-aware monitor control flow**

Pass mode to `load_history`:

```python
history = load_history(
    as_of=as_of,
    now=now,
    workers=workers,
    mode=mode,
)
```

Only export and save for close mode:

```python
if mode == "close" and export_path is not None:
    write_subchart_csv_atomic(build_subchart_frame(history), export_path)

...

send(config, message)
if mode == "close":
    save_state(state_path, next_state)
```

Always evaluate intraday against the loaded official state or an in-memory replay, but discard `next_state` after constructing the email.

- [ ] **Step 4: Write failing missing-current-day test**

```python
def test_intraday_missing_today_sends_unavailable_without_signal(tmp_path):
    sent = []
    result = run_monitor(
        mode="intraday",
        dry_run=False,
        state_path=tmp_path / "state.json",
        now=INTRADAY_NOW,
        as_of=date(2026, 7, 17),
        load_history=previous_day_history,
        send=lambda config, message: sent.append(message),
        environ=EMAIL_ENV,
    )
    assert result == 0
    assert "盘中数据不可用" in sent[0]["Subject"]
    body = sent[0].get_content()
    assert "不产生盘中买卖预估" in body
    assert "BUY" not in body
    assert "SELL" not in body
    assert not (tmp_path / "state.json").exists()
```

- [ ] **Step 5: Run missing-current-day test and verify RED**

Run:

```text
python -m pytest tests/test_cyb_signal_monitor_cli.py::test_intraday_missing_today_sends_unavailable_without_signal -q
```

Expected: FAIL because yesterday's snapshot is currently evaluated.

- [ ] **Step 6: Implement unavailable branch before evaluation**

After loading history:

```python
latest_date = pd.Timestamp(history["date"].max()).date()
if mode == "intraday" and latest_date < as_of:
    message = build_intraday_unavailable_message(
        config=config,
        run_at=now,
        latest_date=latest_date,
    )
    if dry_run:
        print(message["Subject"])
        print(message.get_content())
        return 0
    send(config, message)
    return 0
```

Do not call `_advance_existing_state`, `replay_history`, export, or save on this branch.

- [ ] **Step 7: Write failing email copy tests**

```python
def test_intraday_email_is_explicitly_provisional():
    message = build_status_message(
        config=CONFIG,
        decision=decision(),
        run_at=INTRADAY_NOW,
        stale=False,
        state_rebuilt=False,
        mode="intraday",
    )
    assert "盘中预估" in message["Subject"]
    assert "若此刻收盘" in message.get_content()
    assert "不是正式收盘信号" in message.get_content()


def test_close_email_is_explicitly_official():
    message = build_status_message(
        config=CONFIG,
        decision=decision(),
        run_at=CLOSE_NOW,
        stale=False,
        state_rebuilt=False,
        mode="close",
    )
    assert "收盘确认" in message["Subject"]
    assert "正式状态" in message.get_content()
    assert "盘中预估" not in message.get_content()
```

- [ ] **Step 8: Implement email mode labels**

Use mode-derived subject prefixes and execution notes. Preserve existing indicator, reason, source and risk sections. `build_error_message` must also include either “盘中预估错误” or “收盘确认错误”.

- [ ] **Step 9: Verify Task 2**

Run:

```text
python -m pytest tests/test_cyb_signal_monitor_cli.py tests/test_qq_email.py -q
python -m pytest -q
```

Expected: all tests PASS. Confirm existing close-mode save-after-email ordering remains covered.

### Task 3: CLI与三个批处理入口

**Files:**
- Modify: `scripts/run_cyb_signal_monitor.py`
- Create: `run_cyb_signal_monitor_intraday.bat`
- Create: `run_cyb_signal_monitor_close.bat`
- Modify: `run_cyb_signal_monitor.bat`
- Create: `tests/test_cyb_monitor_launchers.py`

**Interfaces:**
- CLI: `--mode {intraday,close}`, default `close`
- Intraday BAT invokes `--mode intraday` only
- Close BAT invokes `--mode close` and then `push_tdx_cyb_indicator.py`
- Compatibility BAT invokes the close BAT

- [ ] **Step 1: Write failing CLI parser test**

Extract `build_parser() -> argparse.ArgumentParser` and test:

```python
def test_parser_defaults_to_close_and_accepts_intraday():
    parser = build_parser()
    assert parser.parse_args([]).mode == "close"
    assert parser.parse_args(["--mode", "intraday"]).mode == "intraday"
```

- [ ] **Step 2: Run parser test and verify RED**

Run:

```text
python -m pytest tests/test_cyb_signal_monitor_cli.py::test_parser_defaults_to_close_and_accepts_intraday -q
```

Expected: FAIL because `build_parser` and `--mode` do not exist.

- [ ] **Step 3: Implement parser and pass mode into `run_monitor`**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(...)
    parser.add_argument(
        "--mode",
        choices=("intraday", "close"),
        default="close",
    )
    ...
    return parser
```

`main()` calls `build_parser()` and forwards `args.mode`.

- [ ] **Step 4: Write failing launcher contract tests**

```python
def test_intraday_launcher_does_not_push_tdx():
    text = (ROOT / "run_cyb_signal_monitor_intraday.bat").read_text()
    assert "--mode intraday" in text
    assert "push_tdx_cyb_indicator.py" not in text


def test_close_launcher_pushes_tdx_only_after_monitor_success():
    text = (ROOT / "run_cyb_signal_monitor_close.bat").read_text()
    assert "--mode close" in text
    assert "if errorlevel 1 goto :done" in text
    assert "push_tdx_cyb_indicator.py" in text


def test_compatibility_launcher_calls_close_launcher():
    text = (ROOT / "run_cyb_signal_monitor.bat").read_text()
    assert "run_cyb_signal_monitor_close.bat" in text
```

- [ ] **Step 5: Run launcher tests and verify RED**

Run:

```text
python -m pytest tests/test_cyb_monitor_launchers.py -q
```

Expected: FAIL because the two mode-specific BAT files do not exist.

- [ ] **Step 6: Implement launchers**

Each mode-specific BAT:

```bat
@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python scripts\run_cyb_signal_monitor.py --mode <mode>
if errorlevel 1 goto :done
```

Only the close BAT then runs:

```bat
python scripts\push_tdx_cyb_indicator.py
```

Both capture `%ERRORLEVEL%`, skip `pause` when `CYB_SCHEDULED=1`, and return the captured code. The compatibility BAT calls `run_cyb_signal_monitor_close.bat` and returns its code.

- [ ] **Step 7: Verify Task 3**

Run:

```text
python -m pytest tests/test_cyb_monitor_launchers.py tests/test_cyb_signal_monitor_cli.py -q
python -m pytest -q
```

Expected: all tests PASS.

### Task 4: 可重复安装Windows双计划任务

**Files:**
- Create: `scripts/install_cyb_monitor_tasks.ps1`
- Modify: `tests/test_cyb_monitor_launchers.py`
- System state: replace `CYB Signal Monitor 14-40`
- System state: create `CYB Signal Monitor Intraday 14-40`
- System state: create `CYB Signal Monitor Close 15-20`

**Interfaces:**
- PowerShell installer accepts optional `-ProjectRoot`, defaulting to its parent project directory.
- Installer is idempotent and prints both task names, states and next run times.

- [ ] **Step 1: Write failing installer contract test**

```python
def test_task_installer_declares_both_weekday_tasks():
    text = (ROOT / "scripts/install_cyb_monitor_tasks.ps1").read_text(
        encoding="utf-8"
    )
    assert "CYB Signal Monitor Intraday 14-40" in text
    assert "CYB Signal Monitor Close 15-20" in text
    assert "run_cyb_signal_monitor_intraday.bat" in text
    assert "run_cyb_signal_monitor_close.bat" in text
    assert "'14:40'" in text
    assert "'15:20'" in text
    assert "Monday,Tuesday,Wednesday,Thursday,Friday" in text
    assert "Unregister-ScheduledTask -TaskName 'CYB Signal Monitor 14-40'" in text
```

- [ ] **Step 2: Run installer test and verify RED**

Run:

```text
python -m pytest tests/test_cyb_monitor_launchers.py::test_task_installer_declares_both_weekday_tasks -q
```

Expected: FAIL because the installer does not exist.

- [ ] **Step 3: Implement the idempotent installer**

The script must:

1. Resolve and validate both BAT paths.
2. Build `cmd.exe` actions with `CYB_SCHEDULED=1`.
3. Create weekly triggers for 14:40 and 15:20.
4. Use the current Windows identity with `LogonType Interactive` and limited run level.
5. Apply `StartWhenAvailable`, `WakeToRun`, battery allowance and 30-minute limit.
6. `Register-ScheduledTask -Force` for both new tasks.
7. Remove the old task only after both new registrations succeed.
8. Print `Get-ScheduledTask` and `Get-ScheduledTaskInfo` verification.

- [ ] **Step 4: Verify installer syntax before system mutation**

Run:

```powershell
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  "$PWD\scripts\install_cyb_monitor_tasks.ps1",
  [ref]$null,
  [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | Format-List; exit 1 }
```

Expected: exit code 0 and no parser errors.

- [ ] **Step 5: Run full tests before installing tasks**

Run:

```text
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 6: Install the Windows tasks with user approval**

Run elevated:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "C:\Users\Cindy\Desktop\Finance\AI金融\scripts\install_cyb_monitor_tasks.ps1"
```

Expected:

- `CYB Signal Monitor Intraday 14-40` is `Ready`.
- `CYB Signal Monitor Close 15-20` is `Ready`.
- old `CYB Signal Monitor 14-40` no longer exists.
- next run times are the next Monday-Friday 14:40 and 15:20 respectively.

- [ ] **Step 7: Inspect without manually triggering email**

Run:

```powershell
Get-ScheduledTask -TaskName 'CYB Signal Monitor Intraday 14-40',
  'CYB Signal Monitor Close 15-20' |
  Select-Object TaskName,State,Actions,Triggers,Settings
```

Do not invoke `Start-ScheduledTask` during installation because both scripts send real QQ emails.

- [ ] **Step 8: Final verification**

Run:

```text
python -m pytest -q
python scripts/run_cyb_signal_monitor.py --mode intraday --dry-run
python scripts/run_cyb_signal_monitor.py --mode close --dry-run
```

Expected:

- full suite has zero failures;
- intraday preview clearly says it is provisional and does not modify official state/cache files;
- close dry-run clearly says “收盘确认” and does not send email or save state;
- the two Windows tasks remain `Ready`.

