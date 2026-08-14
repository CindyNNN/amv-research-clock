from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ai_invest_advisor.cyb_market_data import MarketDataError
from ai_invest_advisor.cyb_signal_monitor import (
    ModelState,
    load_state,
    save_state_atomic,
)
from scripts.run_cyb_signal_monitor import build_parser, run_monitor


FIXED_NOW = datetime(
    2026,
    7,
    17,
    16,
    0,
    tzinfo=ZoneInfo("Asia/Shanghai"),
)
INTRADAY_NOW = datetime(
    2026,
    7,
    17,
    14,
    40,
    tzinfo=ZoneInfo("Asia/Shanghai"),
)
EMAIL_ENV = {
    "CYB_QQ_EMAIL": "example@qq.com",
    "CYB_QQ_AUTH_CODE": "auth-code",
}


def test_parser_defaults_to_close_and_accepts_intraday():
    parser = build_parser()

    assert parser.parse_args([]).mode == "close"
    assert parser.parse_args(["--mode", "intraday"]).mode == "intraday"


def buy_history(**kwargs):
    return pd.DataFrame(
        [
            {
                "date": "2026-07-17",
                "open": 3400.0,
                "close": 3428.63,
                "high": 3450.0,
                "low": 3380.0,
                "pct_chg": -1.2,
                "emotion": 45.0,
                "advancers": 482,
                "unchanged": 39,
                "decliners": 5001,
                "k": 14.40,
                "d": 18.45,
                "j": 6.31,
                "ma20": 4014.70,
                "ma60": 3900.0,
                "kdj_dead_cross": False,
                "amv_ret_1d": 0.01,
                "amv_ret_2d_sum": 0.05,
                "source_timestamp": "2026-07-17 15:10:00",
            }
        ]
    )


def test_dry_run_prints_but_does_not_send_or_save(tmp_path, capsys):
    def failing_send(*args, **kwargs):
        raise AssertionError("dry-run must not send")

    state_path = tmp_path / "state.json"

    result = run_monitor(
        dry_run=True,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=buy_history,
        send=failing_send,
        environ={},
    )

    assert result == 0
    assert not state_path.exists()
    assert "BUY" in capsys.readouterr().out


def test_run_monitor_exports_ths_subchart_on_dry_run(tmp_path):
    export_path = tmp_path / "ths.csv"

    result = run_monitor(
        dry_run=True,
        state_path=tmp_path / "state.json",
        export_path=export_path,
        now=FIXED_NOW,
        load_history=buy_history,
        environ={},
    )

    assert result == 0
    assert export_path.exists()
    assert "BUY" in export_path.read_text(encoding="utf-8")


def test_intraday_sends_preview_without_saving_state_or_export(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "subchart.csv"
    save_state_atomic(state_path, ModelState.flat())
    original_state = state_path.read_bytes()
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
    assert state_path.read_bytes() == original_state
    assert not export_path.exists()
    assert "盘中预估" in sent[0]["Subject"]
    assert "不是正式收盘信号" in sent[0].get_content()


def test_intraday_missing_today_sends_unavailable_without_signal(tmp_path):
    sent = []

    def previous_day_history(**kwargs):
        frame = buy_history()
        frame.loc[:, "date"] = "2026-07-16"
        return frame

    state_path = tmp_path / "state.json"
    result = run_monitor(
        mode="intraday",
        dry_run=False,
        state_path=state_path,
        now=INTRADAY_NOW,
        as_of=INTRADAY_NOW.date(),
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
    assert not state_path.exists()


def test_close_missing_today_sends_error_and_preserves_state(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "subchart.csv"
    original = ModelState.flat()
    save_state_atomic(state_path, original)
    before = state_path.read_bytes()
    sent = []

    def previous_day_history(**kwargs):
        frame = buy_history()
        frame.loc[:, "date"] = "2026-07-16"
        return frame

    result = run_monitor(
        mode="close",
        dry_run=False,
        state_path=state_path,
        export_path=export_path,
        now=FIXED_NOW,
        as_of=FIXED_NOW.date(),
        load_history=previous_day_history,
        send=lambda config, message: sent.append(message),
        environ=EMAIL_ENV,
    )

    assert result == 2
    assert "[ERROR]" in sent[0]["Subject"]
    assert "收盘确认错误" in sent[0]["Subject"]
    assert state_path.read_bytes() == before
    assert not export_path.exists()


def test_normal_run_saves_only_after_successful_email(tmp_path):
    events = []
    state_path = tmp_path / "state.json"

    def fake_send(config, message):
        assert not state_path.exists()
        events.append("sent")

    def fake_save(path, state):
        events.append("saved")
        save_state_atomic(path, state)

    result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=buy_history,
        send=fake_send,
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
        save_state=fake_save,
    )

    assert result == 0
    assert events == ["sent", "saved"]
    assert load_state(state_path).holding is True


def test_send_failure_preserves_state(tmp_path):
    state_path = tmp_path / "state.json"

    def failing_send(config, message):
        raise OSError("smtp unavailable")

    result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=buy_history,
        send=failing_send,
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
    )

    assert result == 3
    assert not state_path.exists()


def test_data_error_sends_error_email_and_preserves_state(tmp_path):
    original = ModelState.flat()
    state_path = tmp_path / "state.json"
    save_state_atomic(state_path, original)
    sent = []

    def fail_history(**kwargs):
        raise MarketDataError("latest breadth missing")

    result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=fail_history,
        send=lambda config, message: sent.append(message),
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
    )

    assert result == 2
    assert "[ERROR]" in sent[0]["Subject"]
    assert load_state(state_path) == original


def test_existing_same_day_state_is_reported_as_repeat(tmp_path):
    state_path = tmp_path / "state.json"
    sent = []
    first_result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=buy_history,
        send=lambda config, message: sent.append(message),
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
    )
    first_state = load_state(state_path)
    second_result = run_monitor(
        dry_run=False,
        state_path=state_path,
        now=FIXED_NOW,
        load_history=buy_history,
        send=lambda config, message: sent.append(message),
        environ={
            "CYB_QQ_EMAIL": "example@qq.com",
            "CYB_QQ_AUTH_CODE": "auth-code",
        },
    )

    assert first_result == second_result == 0
    assert "同日重复检查：是" in sent[-1].get_content()
    assert load_state(state_path) == first_state
