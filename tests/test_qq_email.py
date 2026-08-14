from datetime import date, datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest

from ai_invest_advisor.cyb_signal_monitor import (
    MarketSnapshot,
    ModelState,
    evaluate_snapshot,
)
from ai_invest_advisor.qq_email import (
    EmailConfigError,
    QQEmailConfig,
    build_error_message,
    build_intraday_unavailable_message,
    build_status_message,
    send_message,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def buy_decision():
    snapshot = MarketSnapshot(
        date=date(2026, 7, 17),
        close=3428.63,
        pct_change=-1.2,
        emotion=45.0,
        advancers=482,
        unchanged=39,
        decliners=5001,
        k=14.40,
        d=18.45,
        j=6.31,
        ma20=4014.70,
        ma60=3900.0,
        kdj_dead_cross=False,
        amv_ret_1d=0.01,
        amv_ret_2d_sum=0.05,
        source_timestamp="2026-07-17 15:10:00",
    )
    return evaluate_snapshot(ModelState.flat(), snapshot)[1]


def test_qq_config_requires_address_and_auth_code():
    with pytest.raises(EmailConfigError):
        QQEmailConfig.from_env({})

    with pytest.raises(EmailConfigError):
        QQEmailConfig.from_env(
            {
                "CYB_QQ_EMAIL": "not-qq@example.com",
                "CYB_QQ_AUTH_CODE": "secret",
            }
        )


def test_status_message_contains_audit_fields_without_auth_code():
    config = QQEmailConfig("example@qq.com", "secret-auth-code")

    message = build_status_message(
        config=config,
        decision=buy_decision(),
        run_at=datetime(2026, 7, 17, 16, 0, tzinfo=SHANGHAI),
        stale=False,
        state_rebuilt=True,
    )

    assert message["To"] == "example@qq.com"
    assert "[BUY]" in message["Subject"]
    assert "创业板AMV+情绪" in message["Subject"]
    body = message.get_content()
    assert "0AMV两日涨和" in body
    assert "市场情绪" in body
    assert "MA60" in body
    assert "下一交易日" in body
    assert "状态已从历史重建" in body
    assert "不构成投资建议" in body
    assert "secret-auth-code" not in message.as_string()


def test_intraday_email_is_explicitly_provisional():
    message = build_status_message(
        config=QQEmailConfig("example@qq.com", "secret-auth-code"),
        decision=buy_decision(),
        run_at=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        stale=False,
        state_rebuilt=False,
        mode="intraday",
    )

    assert "盘中预估" in message["Subject"]
    body = message.get_content()
    assert "若此刻收盘" in body
    assert "不是正式收盘信号" in body


def test_close_email_is_explicitly_official():
    message = build_status_message(
        config=QQEmailConfig("example@qq.com", "secret-auth-code"),
        decision=buy_decision(),
        run_at=datetime(2026, 7, 17, 15, 20, tzinfo=SHANGHAI),
        stale=False,
        state_rebuilt=False,
        mode="close",
    )

    assert "收盘确认" in message["Subject"]
    assert "正式状态" in message.get_content()
    assert "盘中预估" not in message.get_content()


def test_intraday_unavailable_message_is_non_actionable():
    message = build_intraday_unavailable_message(
        config=QQEmailConfig("example@qq.com", "secret-auth-code"),
        run_at=datetime(2026, 7, 17, 14, 40, tzinfo=SHANGHAI),
        latest_date=date(2026, 7, 16),
    )

    assert "盘中数据不可用" in message["Subject"]
    body = message.get_content()
    assert "不产生盘中买卖预估" in body
    assert "不构成投资建议" in body


def test_error_message_mentions_amv():
    message = build_error_message(
        config=QQEmailConfig("example@qq.com", "secret"),
        error="boom",
        run_at=datetime(2026, 7, 17, 16, 0, tzinfo=SHANGHAI),
    )
    assert "0AMV" in message.get_content()
    assert "ERROR" in message["Subject"]


def test_send_message_uses_ssl_login(monkeypatch):
    config = QQEmailConfig("example@qq.com", "auth-code")
    message = EmailMessage()
    message["From"] = config.address
    message["To"] = config.address
    message["Subject"] = "test"
    message.set_content("body")

    instances = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.host = host
            self.port = port
            self.login_args = None
            self.sent_message = None
            self.from_addr = None
            self.to_addrs = None
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            self.login_args = (user, password)

        def send_message(self, msg, from_addr=None, to_addrs=None):
            self.sent_message = msg
            self.from_addr = from_addr
            self.to_addrs = to_addrs

    send_message(config, message, smtp_factory=FakeSMTP)

    smtp = instances[0]
    assert smtp.host == "smtp.qq.com"
    assert smtp.port == 465
    assert smtp.login_args == ("example@qq.com", "auth-code")
    assert smtp.sent_message is message
    assert smtp.from_addr == "example@qq.com"
    assert smtp.to_addrs == ["example@qq.com"]
