# 创业板情绪策略邮件监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 Windows 本地一键运行工具，每次更新创业板指与市场情绪、执行“情绪<15且J<30买入；死叉且低于MA20或最高收盘回撤8%卖出”的模型状态机，并通过同一个 QQ 邮箱发送状态邮件。

**Architecture:** 数据模块负责动态下载、缓存、对齐并计算指标；纯状态机模块只接受指标行和旧状态并返回信号与新状态；邮件模块只负责配置、渲染和 SMTP 发送；CLI 负责预览、错误邮件、邮件成功后原子提交状态。首次运行用完整历史回放重建模型状态，同日重复运行不重复迁移。

**Tech Stack:** Python 3.10+、标准库 `urllib`/`smtplib`/`email`/`json`、pandas、pytest、Windows batch。

## Global Constraints

- 不连接券商、不下单、不请求或保存券商凭据。
- QQ SMTP 地址固定为 `smtp.qq.com:465`，发件人与收件人相同。
- QQ 地址读取 `CYB_QQ_EMAIL`，授权码读取 `CYB_QQ_AUTH_CODE`，不得写入代码、状态、日志或邮件。
- 收盘后产生的信号只能标注为下一交易日执行参考。
- 每次运行都发送 BUY、SELL、HOLD、FLAT 或 ERROR 邮件。
- 正常模式仅在邮件成功后原子提交状态；预览模式不发邮件且不改状态。
- 数据源、数据日期、运行时间和风险提示必须出现在每封正常状态邮件中。
- 当前 `.git` 目录为空，不是有效 Git 仓库；本计划以测试通过作为任务检查点，不执行提交步骤。

---

### Task 1: 纯策略状态机与历史回放

**Files:**
- Create: `src/ai_invest_advisor/cyb_signal_monitor.py`
- Create: `tests/test_cyb_signal_monitor.py`

**Interfaces:**
- Produces: `ModelState`, `MarketSnapshot`, `SignalDecision`, `evaluate_snapshot(state, snapshot)`, `replay_history(frame)`, `load_state(path)`, `save_state_atomic(path, state)`。
- Consumes: pandas DataFrame 仅用于 `replay_history`；单日判断不依赖网络、文件或邮件。

- [ ] **Step 1: 写 BUY、HOLD、两类 SELL、FLAT 与同日幂等的失败测试**

```python
from datetime import date

from ai_invest_advisor.cyb_signal_monitor import (
    MarketSnapshot,
    ModelState,
    evaluate_snapshot,
)


def snap(day, *, close=100, emotion=50, j=50, k=50, d=50,
         dead=False, ma20=100):
    return MarketSnapshot(
        date=date.fromisoformat(day),
        close=float(close),
        pct_change=0.0,
        emotion=float(emotion),
        advancers=100,
        unchanged=20,
        decliners=100,
        k=float(k),
        d=float(d),
        j=float(j),
        ma20=float(ma20),
        kdj_dead_cross=dead,
        source_timestamp=f"{day} 15:10:00",
    )


def test_flat_enters_when_emotion_and_j_are_below_thresholds():
    state, decision = evaluate_snapshot(
        ModelState.flat(), snap("2026-07-17", emotion=14.9, j=29.9)
    )
    assert decision.signal == "BUY"
    assert state.holding is True
    assert state.entry_signal_date == "2026-07-17"
    assert state.peak_close == 100.0


def test_holding_without_exit_condition_stays_hold():
    initial = ModelState(
        holding=True, entry_signal_date="2026-07-16",
        entry_signal_close=100.0, peak_close=105.0,
        peak_close_date="2026-07-16"
    )
    state, decision = evaluate_snapshot(
        initial, snap("2026-07-17", close=103, dead=True, ma20=102)
    )
    assert decision.signal == "HOLD"
    assert state.holding is True


def test_dead_cross_below_ma20_sells():
    initial = ModelState(
        holding=True, entry_signal_date="2026-07-01",
        entry_signal_close=100.0, peak_close=110.0,
        peak_close_date="2026-07-10"
    )
    state, decision = evaluate_snapshot(
        initial, snap("2026-07-17", close=101, dead=True, ma20=102)
    )
    assert decision.signal == "SELL"
    assert "KDJ_DEAD_CROSS_BELOW_MA20" in decision.reasons
    assert state.holding is False


def test_eight_percent_close_trailing_drawdown_sells():
    initial = ModelState(
        holding=True, entry_signal_date="2026-07-01",
        entry_signal_close=100.0, peak_close=110.0,
        peak_close_date="2026-07-10"
    )
    state, decision = evaluate_snapshot(
        initial, snap("2026-07-17", close=101.2, dead=False, ma20=100)
    )
    assert decision.signal == "SELL"
    assert "PEAK_CLOSE_DRAWDOWN_8_PERCENT" in decision.reasons
    assert decision.trailing_line == 101.2


def test_flat_without_entry_condition_returns_flat():
    state, decision = evaluate_snapshot(
        ModelState.flat(), snap("2026-07-17", emotion=15, j=29)
    )
    assert decision.signal == "FLAT"
    assert state.holding is False


def test_same_date_repeats_result_without_state_transition():
    first_state, first = evaluate_snapshot(
        ModelState.flat(), snap("2026-07-17", emotion=14, j=20)
    )
    second_state, second = evaluate_snapshot(
        first_state, snap("2026-07-17", emotion=14, j=20)
    )
    assert second.repeated_check is True
    assert second.signal == first.signal
    assert second_state == first_state
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python -m pytest tests/test_cyb_signal_monitor.py -q`

Expected: collection error containing `No module named 'ai_invest_advisor.cyb_signal_monitor'`。

- [ ] **Step 3: 实现最小状态与决策模型**

```python
@dataclass(frozen=True)
class ModelState:
    holding: bool = False
    entry_signal_date: str | None = None
    entry_signal_close: float | None = None
    peak_close: float | None = None
    peak_close_date: str | None = None
    processed_date: str | None = None
    last_signal: str = "FLAT"
    last_reasons: tuple[str, ...] = ()

    @classmethod
    def flat(cls) -> "ModelState":
        return cls()


@dataclass(frozen=True)
class MarketSnapshot:
    date: date
    close: float
    pct_change: float
    emotion: float
    advancers: int
    unchanged: int
    decliners: int
    k: float
    d: float
    j: float
    ma20: float
    kdj_dead_cross: bool
    source_timestamp: str


@dataclass(frozen=True)
class SignalDecision:
    signal: Literal["BUY", "SELL", "HOLD", "FLAT"]
    reasons: tuple[str, ...]
    repeated_check: bool
    trailing_line: float | None
    peak_drawdown: float | None
    snapshot: MarketSnapshot
    state_before: ModelState
    state_after: ModelState
```

`evaluate_snapshot` 必须先检查 `processed_date`；同日直接复用 `last_signal` 和
`last_reasons`。新交易日持仓时先更新最高收盘，再判断：

```python
dead_cross_exit = snapshot.kdj_dead_cross and snapshot.close < snapshot.ma20
trailing_line = peak_close * 0.92
trailing_exit = snapshot.close <= trailing_line
```

空仓买入必须使用严格 `<15` 和 `<30`。卖出后清空入场与峰值字段，但保留
`processed_date`、`last_signal` 和 `last_reasons`。

- [ ] **Step 4: 增加历史回放和状态文件失败测试**

```python
def test_replay_history_matches_incremental_evaluation():
    frame = pd.DataFrame([
        {"date": "2026-07-15", "close": 100, "pct_chg": 0,
         "emotion": 10, "advancers": 100, "unchanged": 20,
         "decliners": 900, "k": 20, "d": 25, "j": 10,
         "ma20": 105, "kdj_dead_cross": False,
         "source_timestamp": "2026-07-15 15:10:00"},
        {"date": "2026-07-16", "close": 110, "pct_chg": 10,
         "emotion": 60, "advancers": 600, "unchanged": 20,
         "decliners": 400, "k": 60, "d": 50, "j": 80,
         "ma20": 104, "kdj_dead_cross": False,
         "source_timestamp": "2026-07-16 15:10:00"},
        {"date": "2026-07-17", "close": 101.2, "pct_chg": -8,
         "emotion": 30, "advancers": 300, "unchanged": 20,
         "decliners": 700, "k": 40, "d": 45, "j": 30,
         "ma20": 103, "kdj_dead_cross": True,
         "source_timestamp": "2026-07-17 15:10:00"},
    ])
    state, decision = replay_history(frame)
    assert state.holding is False
    assert decision.signal == "SELL"


def test_atomic_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    expected = ModelState.flat()
    save_state_atomic(path, expected)
    assert load_state(path) == expected
```

- [ ] **Step 5: 运行测试确认新测试失败，再实现 `replay_history`、JSON 校验和原子替换**

Run: `python -m pytest tests/test_cyb_signal_monitor.py -q`

Expected before implementation: failure naming `replay_history` or `save_state_atomic`。

Implementation requirements:

```python
def save_state_atomic(path: Path, state: ModelState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
```

损坏状态文件由 `load_state` 抛出 `StateFileError`，CLI 决定重新回放，状态模块
不得静默吞掉错误。

- [ ] **Step 6: 运行状态机测试**

Run: `python -m pytest tests/test_cyb_signal_monitor.py -q`

Expected: all tests pass。

---

### Task 2: 动态市场数据更新与指标计算

**Files:**
- Create: `src/ai_invest_advisor/cyb_market_data.py`
- Create: `tests/test_cyb_market_data.py`

**Interfaces:**
- Consumes: 本地缓存路径、`as_of: date`、可注入的 `fetch_json(url)`。
- Produces: `build_index_url(as_of)`, `parse_index_payload(payload)`,
  `parse_breadth_payload(day, payload)`, `add_indicators(frame)`,
  `load_complete_history(paths, as_of, now)`。

- [ ] **Step 1: 写动态 URL、涨跌分布口径和指标失败测试**

```python
def test_index_url_uses_requested_end_date():
    assert "2026-07-17" in build_index_url(date(2026, 7, 17))


def test_breadth_uses_quoted_universe_denominator():
    distribution = [0] * 63
    distribution[0] = 90
    distribution[31] = 10
    distribution[32] = 900
    row = parse_breadth_payload(
        date(2026, 7, 17),
        {"status_code": 0, "result": {
            "distribution": distribution,
            "limit_up": 5,
            "limit_down": 2,
            "last_update_time": "2026-07-17 15:10:00",
        }},
    )
    assert row["emotion"] == 9.0
    assert row["quoted_total"] == 1000


def test_add_indicators_matches_expected_columns(sample_ohlc_breadth):
    result = add_indicators(sample_ohlc_breadth)
    assert {"ma20", "k", "d", "j", "kdj_dead_cross"} <= set(result.columns)
    assert result["ma20"].iloc[-1] == pytest.approx(
        result["close"].tail(20).mean()
    )
```

测试文件中的 `sample_ohlc_breadth` 固定夹具必须显式构造 25 个连续交易日，不读取
网络：

```python
@pytest.fixture
def sample_ohlc_breadth():
    dates = pd.bdate_range("2026-06-15", periods=25)
    close = pd.Series(range(100, 125), dtype=float)
    return pd.DataFrame({
        "date": dates,
        "open": close - 0.5,
        "close": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "emotion": [30.0] * 25,
        "advancers": [1500] * 25,
        "unchanged": [100] * 25,
        "decliners": [3400] * 25,
        "source_timestamp": [
            f"{day:%Y-%m-%d} 15:10:00" for day in dates
        ],
    })
```

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `python -m pytest tests/test_cyb_market_data.py -q`

Expected: collection error for `ai_invest_advisor.cyb_market_data`。

- [ ] **Step 3: 实现腾讯与同花顺解析、KDJ 和 MA20**

复用现有回测已验证公式：

```python
def chinese_sma(values: pd.Series, n: int, initial: float = 50.0) -> pd.Series:
    previous = initial
    output = []
    for value in values.astype(float):
        if not math.isnan(value):
            previous = ((n - 1) * previous + value) / n
        output.append(previous)
    return pd.Series(output, index=values.index, dtype=float)
```

`add_indicators` 使用 9 日最高/最低生成 RSV，再依次生成 K、D、J 与死叉；MA20
必须 `rolling(20, min_periods=20).mean()`。解析后验证 OHLC 无缺失、日期唯一、
情绪在 `[0,100]`。

- [ ] **Step 4: 写缓存增量、收盘前排除当日数据和双源对齐失败测试**

```python
def test_before_1515_excludes_current_calendar_day(...):
    frame = load_complete_history(
        paths, date(2026, 7, 17),
        datetime(2026, 7, 17, 14, 30, tzinfo=SHANGHAI),
        fetch_json=fake_fetch,
    )
    assert frame["date"].max().date() == date(2026, 7, 16)


def test_missing_latest_breadth_is_rejected(...):
    with pytest.raises(MarketDataError, match="情绪"):
        load_complete_history(
            paths, date(2026, 7, 17),
            datetime(2026, 7, 17, 16, 0, tzinfo=SHANGHAI),
            fetch_json=fake_fetch_without_latest_breadth,
        )
```

- [ ] **Step 5: 实现 `MarketDataPaths` 和增量缓存**

`MarketDataPaths` 指向：

```python
index_cache = ROOT / "data/backtests/cyb_emotion_kdj/cyb_399006_daily.csv"
breadth_cache = ROOT / "data/backtests/cyb_emotion_kdj/all_a_breadth_ths_2022_present.csv"
legacy_cache = ROOT / "data/backtests/cyb_emotion_kdj/all_a_breadth_legacy_2020_2022.csv"
```

每次运行刷新腾讯指数缓存，只对指数交易日中 2022-01-01 之后且情绪缓存缺失的
日期请求同花顺。缓存写入使用临时文件替换。合并时 `validate="one_to_one"`，
最新可用日缺任一数据即抛 `MarketDataError`。

- [ ] **Step 6: 运行数据模块测试**

Run: `python -m pytest tests/test_cyb_market_data.py -q`

Expected: all tests pass without network access。

---

### Task 3: QQ 邮件配置、正文与发送

**Files:**
- Create: `src/ai_invest_advisor/qq_email.py`
- Create: `tests/test_qq_email.py`

**Interfaces:**
- Consumes: `SignalDecision`、运行时间、数据陈旧标记、环境变量映射。
- Produces: `QQEmailConfig.from_env(environ)`, `build_status_message(...)`,
  `build_error_message(...)`, `send_message(config, message, smtp_factory)`。

- [ ] **Step 1: 写配置安全和正常邮件内容失败测试**

```python
def test_qq_config_requires_address_and_auth_code():
    with pytest.raises(EmailConfigError):
        QQEmailConfig.from_env({})


def test_status_message_contains_audit_fields(buy_decision):
    message = build_status_message(
        config=QQEmailConfig("example@qq.com", "secret"),
        decision=buy_decision,
        run_at=datetime(2026, 7, 17, 16, 0, tzinfo=SHANGHAI),
        stale=False,
        state_rebuilt=False,
    )
    assert message["To"] == "example@qq.com"
    assert "[BUY]" in message["Subject"]
    body = message.get_content()
    assert "市场情绪" in body
    assert "K / D / J" in body
    assert "MA20" in body
    assert "下一交易日" in body
    assert "不构成投资建议" in body
    assert "secret" not in message.as_string()
```

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `python -m pytest tests/test_qq_email.py -q`

Expected: collection error for `ai_invest_advisor.qq_email`。

- [ ] **Step 3: 实现配置和纯邮件渲染**

```python
@dataclass(frozen=True)
class QQEmailConfig:
    address: str
    auth_code: str
    host: str = "smtp.qq.com"
    port: int = 465

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "QQEmailConfig":
        address = environ.get("CYB_QQ_EMAIL", "").strip()
        auth_code = environ.get("CYB_QQ_AUTH_CODE", "").strip()
        if not address.endswith("@qq.com") or not auth_code:
            raise EmailConfigError(
                "请设置 CYB_QQ_EMAIL 和 CYB_QQ_AUTH_CODE（QQ SMTP授权码）"
            )
        return cls(address=address, auth_code=auth_code)
```

邮件正文按设计文档列出完整审计字段。持仓相关字段在 FLAT 时显示“不适用”，
不得省略数据来源、时间和风险提示。

- [ ] **Step 4: 写 SMTP SSL 交互失败测试**

```python
def test_send_uses_ssl_login_and_same_sender_recipient(fake_smtp):
    config = QQEmailConfig("example@qq.com", "auth-code")
    message = EmailMessage()
    message["From"] = config.address
    message["To"] = config.address
    message["Subject"] = "test"
    message.set_content("body")
    send_message(config, message, smtp_factory=fake_smtp.factory)
    assert fake_smtp.host == "smtp.qq.com"
    assert fake_smtp.port == 465
    assert fake_smtp.login_args == ("example@qq.com", "auth-code")
    assert fake_smtp.sent_message is message
```

- [ ] **Step 5: 实现 `SMTP_SSL` 发送并运行邮件测试**

```python
def send_message(config, message, smtp_factory=smtplib.SMTP_SSL):
    with smtp_factory(
        config.host, config.port, timeout=30,
        context=ssl.create_default_context(),
    ) as smtp:
        smtp.login(config.address, config.auth_code)
        smtp.send_message(
            message, from_addr=config.address, to_addrs=[config.address]
        )
```

Run: `python -m pytest tests/test_qq_email.py -q`

Expected: all tests pass，不发生真实网络连接。

---

### Task 4: CLI 编排、错误邮件与事务式状态提交

**Files:**
- Create: `scripts/run_cyb_signal_monitor.py`
- Create: `tests/test_cyb_signal_monitor_cli.py`

**Interfaces:**
- Consumes: Tasks 1–3 的状态机、数据与邮件接口。
- Produces:
  `run_monitor(*, dry_run, state_path, now, as_of=None, workers=4,
  load_history=load_complete_history, send=send_message,
  environ=os.environ, save_state=save_state_atomic) -> int`
  和命令行 `main()`。可注入参数只用于隔离网络、SMTP 和文件提交测试。

- [ ] **Step 1: 写预览模式与邮件后提交状态失败测试**

```python
def test_dry_run_prints_but_does_not_send_or_save(tmp_path, capsys):
    result = run_monitor(
        dry_run=True,
        state_path=tmp_path / "state.json",
        now=FIXED_NOW,
        load_history=fake_history,
        send=failing_if_called,
        environ={},
    )
    assert result == 0
    assert not (tmp_path / "state.json").exists()
    assert "BUY" in capsys.readouterr().out


def test_normal_run_saves_only_after_successful_email(tmp_path):
    events = []
    result = run_monitor(
        dry_run=False,
        state_path=tmp_path / "state.json",
        now=FIXED_NOW,
        load_history=fake_history,
        send=lambda config, message: events.append("sent"),
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
        save_state=lambda path, state: events.append("saved"),
    )
    assert result == 0
    assert events == ["sent", "saved"]
```

- [ ] **Step 2: 运行测试并确认脚本接口不存在**

Run: `python -m pytest tests/test_cyb_signal_monitor_cli.py -q`

Expected: import or attribute failure naming `run_monitor`。

- [ ] **Step 3: 实现正常流程和参数**

CLI 参数：

```text
--dry-run
--state PATH
--as-of YYYY-MM-DD
--workers INTEGER
```

默认状态路径：
`data/monitor/cyb_emotion_strategy_state.json`。

状态不存在或 `StateFileError` 时，用完整历史 `replay_history` 重建；状态存在时，
只对 `processed_date` 之后的完整指标行依次调用 `evaluate_snapshot`，保证漏跑多日
后仍按顺序更新峰值和信号。

- [ ] **Step 4: 写数据失败发送 ERROR 且不改状态的失败测试**

```python
def test_data_error_sends_error_email_and_preserves_state(tmp_path):
    original = ModelState.flat()
    state_path = tmp_path / "state.json"
    save_state_atomic(state_path, original)
    sent = []
    result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=lambda **kwargs: (_ for _ in ()).throw(
            MarketDataError("latest breadth missing")
        ),
        send=lambda config, message: sent.append(message),
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
    )
    assert result == 2
    assert "[ERROR]" in sent[0]["Subject"]
    assert load_state(state_path) == original
```

- [ ] **Step 5: 实现错误分支和安全日志**

数据错误在邮箱配置有效时发送 ERROR 邮件；SMTP 错误只向终端打印不含授权码的
消息并返回退出码 3。任何异常路径不得写入状态。预览模式的数据错误显示 ERROR
预览并返回退出码 2。

- [ ] **Step 6: 运行 CLI 测试**

Run: `python -m pytest tests/test_cyb_signal_monitor_cli.py -q`

Expected: all tests pass。

---

### Task 5: Windows 一键入口与用户说明

**Files:**
- Create: `run_cyb_signal_monitor.bat`
- Create: `docs/cyb_email_monitor_usage.md`
- Create: `tests/test_cyb_monitor_files.py`

**Interfaces:**
- Consumes: Task 4 CLI。
- Produces: 双击运行入口和 QQ 授权码配置说明。

- [ ] **Step 1: 写入口与文档存在性失败测试**

```python
def test_windows_launcher_invokes_monitor_script():
    text = Path("run_cyb_signal_monitor.bat").read_text(
        encoding="utf-8"
    )
    assert "scripts\\run_cyb_signal_monitor.py" in text
    assert "PYTHONPATH" in text


def test_usage_document_names_required_environment_variables():
    text = Path("docs/cyb_email_monitor_usage.md").read_text(
        encoding="utf-8"
    )
    assert "CYB_QQ_EMAIL" in text
    assert "CYB_QQ_AUTH_CODE" in text
    assert "--dry-run" in text
```

- [ ] **Step 2: 运行测试并确认文件缺失**

Run: `python -m pytest tests/test_cyb_monitor_files.py -q`

Expected: `FileNotFoundError`。

- [ ] **Step 3: 创建批处理入口**

```bat
@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python scripts\run_cyb_signal_monitor.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 运行失败，退出码：%EXIT_CODE%
pause
exit /b %EXIT_CODE%
```

- [ ] **Step 4: 编写 QQ 邮箱配置与运行说明**

文档给出以下 PowerShell 用户级环境变量命令，不包含真实地址或授权码：

```powershell
[Environment]::SetEnvironmentVariable(
  "CYB_QQ_EMAIL", "你的QQ号@qq.com", "User"
)
[Environment]::SetEnvironmentVariable(
  "CYB_QQ_AUTH_CODE", "你的QQ邮箱SMTP授权码", "User"
)
```

文档说明在 QQ 邮箱网页版开启 SMTP 服务并生成授权码、重启终端后先运行：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts\run_cyb_signal_monitor.py --dry-run
```

然后双击 `run_cyb_signal_monitor.bat` 正式发送。说明状态文件是模型状态而非真实
账户，删除状态文件会触发历史重建。

- [ ] **Step 5: 运行文件测试**

Run: `python -m pytest tests/test_cyb_monitor_files.py -q`

Expected: all tests pass。

---

### Task 6: 集成验证与真实邮件前安全检查

**Files:**
- Modify only if verification reveals a defect in files created by Tasks 1–5.

**Interfaces:**
- Verifies all prior task outputs together。

- [ ] **Step 1: 运行新增测试**

Run:

```powershell
python -m pytest `
  tests\test_cyb_signal_monitor.py `
  tests\test_cyb_market_data.py `
  tests\test_qq_email.py `
  tests\test_cyb_signal_monitor_cli.py `
  tests\test_cyb_monitor_files.py -q
```

Expected: all new tests pass。

- [ ] **Step 2: 运行完整项目测试**

Run: `python -m pytest -q`

Expected: all tests pass，无失败。

- [ ] **Step 3: 编译新增 Python 文件**

Run:

```powershell
python -m py_compile `
  src\ai_invest_advisor\cyb_signal_monitor.py `
  src\ai_invest_advisor\cyb_market_data.py `
  src\ai_invest_advisor\qq_email.py `
  scripts\run_cyb_signal_monitor.py
```

Expected: exit code 0。

- [ ] **Step 4: 运行不发送邮件的真实数据预览**

Run:

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts\run_cyb_signal_monitor.py --dry-run
```

Expected:

- 成功更新到最近完整交易日；
- 输出 BUY、SELL、HOLD 或 FLAT；
- 输出数据日期、情绪、K/D/J、MA20 和持仓信息；
- 不创建或修改 `data/monitor/cyb_emotion_strategy_state.json`；
- 不读取或打印 QQ 授权码。

- [ ] **Step 5: 检查凭据未进入工作区**

Run:

```powershell
rg -n "CYB_QQ_AUTH_CODE|auth_code" src scripts docs tests
```

Expected: 只出现环境变量名称、字段名和测试用假值，不出现用户真实 QQ 邮箱或真实
授权码。

- [ ] **Step 6: 由用户配置授权码后执行一次真实邮件烟雾测试**

Run: 双击 `run_cyb_signal_monitor.bat`。

Expected:

- QQ 邮箱收到一封状态邮件；
- 邮件主题包含正确的数据日期和信号类型；
- 邮件成功后才创建或更新状态文件；
- 再次运行仍收到邮件，但同一数据日标记为重复检查且状态不二次迁移。

真实邮件烟雾测试需要用户在本机配置 QQ SMTP 授权码；实现阶段不得向用户索取或
回显该授权码。
