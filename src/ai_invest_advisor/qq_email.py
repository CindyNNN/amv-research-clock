from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Callable, Literal, Mapping

from ai_invest_advisor.cyb_signal_monitor import (
    AMV_ENTRY_TWO_DAY,
    AMV_EXIT_DAILY,
    EMOTION_EXIT_MIN,
    STRATEGY_LABEL,
    SignalDecision,
    above_ma60_protect,
    entry_hit,
    raw_exit_reasons,
)


MessageMode = Literal["intraday", "close"]


class EmailConfigError(ValueError):
    pass


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
        if not address.lower().endswith("@qq.com") or not auth_code:
            raise EmailConfigError(
                "请设置 CYB_QQ_EMAIL 和 CYB_QQ_AUTH_CODE（QQ邮箱SMTP授权码）"
            )
        return cls(address=address, auth_code=auth_code)


SIGNAL_TEXT = {
    "BUY": "产生买入信号",
    "SELL": "产生卖出信号",
    "HOLD": "模型继续持有，无卖出信号",
    "FLAT": "模型空仓，无买入信号",
}

REASON_TEXT = {
    "AMV_TWO_DAY_SUM_GT_3PCT": "0AMV两日涨和>3%",
    "AMV_DAILY_RET_LE_MINUS_3_5PCT": "0AMV单日跌幅≤-3.5%",
    "EMOTION_GE_60": "市场情绪≥60（过热）",
}


def _new_message(config: QQEmailConfig, subject: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.address
    message["To"] = config.address
    message["Subject"] = subject
    return message


def _optional_number(value: float | None, digits: int = 2) -> str:
    return "不适用" if value is None else f"{value:.{digits}f}"


def build_status_message(
    *,
    config: QQEmailConfig,
    decision: SignalDecision,
    run_at: datetime,
    stale: bool,
    state_rebuilt: bool,
    mode: MessageMode = "close",
) -> EmailMessage:
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported message mode: {mode}")
    snapshot = decision.snapshot
    mode_label = "盘中预估" if mode == "intraday" else "收盘确认"
    message = _new_message(
        config,
        (
            f"[创业板AMV+情绪][{mode_label}][{decision.signal}] "
            f"{snapshot.date.isoformat()}"
        ),
    )
    position_state = (
        decision.state_before
        if decision.signal == "SELL"
        else decision.state_after
    )
    reasons = (
        "；".join(REASON_TEXT.get(reason, reason) for reason in decision.reasons)
        if decision.reasons
        else "无"
    )
    suppressed = (
        "；".join(
            REASON_TEXT.get(reason, reason) for reason in decision.suppressed_exits
        )
        if decision.suppressed_exits
        else "无"
    )
    mode_lines = (
        [
            "运行模式：盘中预估",
            (
                "状态说明：若此刻收盘，预估模型状态为"
                f"{'持仓' if decision.state_after.holding else '空仓'}。"
            ),
            "重要提示：这不是正式收盘信号，价格、情绪和0AMV可能在收盘前变化。",
            "",
        ]
        if mode == "intraday"
        else [
            "运行模式：收盘确认",
            (
                "状态说明：本邮件判断后的正式状态为"
                f"{'持仓' if decision.state_after.holding else '空仓'}。"
            ),
            "",
        ]
    )
    lines = mode_lines + [
        f"策略：{STRATEGY_LABEL}",
        f"信号：{decision.signal} — {SIGNAL_TEXT[decision.signal]}",
        f"触发原因：{reasons}",
        f"被MA60保护压制的离场条件：{suppressed}",
        f"数据日期：{snapshot.date.isoformat()}",
        f"运行时间：{run_at.isoformat(timespec='seconds')}",
        f"数据是否陈旧：{'是' if stale else '否'}",
        f"同日重复检查：{'是' if decision.repeated_check else '否'}",
        f"状态已从历史重建：{'是' if state_rebuilt else '否'}",
        "",
        "当日指标",
        f"- 创业板指收盘：{snapshot.close:.2f}",
        f"- 当日涨跌幅：{snapshot.pct_change:.2f}%",
        f"- 市场情绪：{snapshot.emotion:.2f}",
        (
            f"- 上涨 / 平盘 / 下跌：{snapshot.advancers} / "
            f"{snapshot.unchanged} / {snapshot.decliners}"
        ),
        f"- 0AMV单日涨跌：{snapshot.amv_ret_1d:.2%}",
        f"- 0AMV两日涨和：{snapshot.amv_ret_2d_sum:.2%}",
        f"- MA20 / MA60：{snapshot.ma20:.2f} / {snapshot.ma60:.2f}",
        f"- 收盘是否高于MA60：{'是' if above_ma60_protect(snapshot) else '否'}",
        f"- K / D / J（参考）：{snapshot.k:.2f} / {snapshot.d:.2f} / {snapshot.j:.2f}",
        "",
        "模型状态",
        f"- 邮件判断后的模型状态：{'持仓' if decision.state_after.holding else '空仓'}",
        f"- 买入信号日期：{position_state.entry_signal_date or '不适用'}",
        (
            f"- 买入信号日收盘："
            f"{_optional_number(position_state.entry_signal_close)}"
        ),
        f"- 持仓最高收盘：{_optional_number(position_state.peak_close)}",
        (
            "- 当前峰值回撤："
            + (
                "不适用"
                if decision.peak_drawdown is None
                else f"{decision.peak_drawdown:.2%}"
            )
        ),
        "",
        "条件检查",
        (
            f"- 买入条件（0AMV两日涨和>{AMV_ENTRY_TWO_DAY:.0%}）："
            f"{'是' if entry_hit(snapshot) else '否'}"
        ),
        (
            f"- 离场腿：0AMV单日≤{AMV_EXIT_DAILY:.1%} 或 情绪≥{EMOTION_EXIT_MIN:.0f}："
            f"{'是' if bool(raw_exit_reasons(snapshot)) else '否'}"
        ),
        (
            "- MA60保护（收盘>MA60则忽略离场）："
            f"{'生效中' if above_ma60_protect(snapshot) else '未触发'}"
        ),
        "",
        "数据来源",
        "- 创业板指：腾讯证券公开行情接口，代码399006",
        "- 市场情绪：同花顺全市场涨跌分布接口",
        "- 活跃市值0AMV：指南针本地缓存 day.vdat / 导出 CSV",
        f"- 情绪源更新时间：{snapshot.source_timestamp}",
        "",
        "执行说明：信号在收盘数据形成后才能确认，仅供下一交易日开盘执行参考"
        "（可交易标的建议用创业板ETF如159915跟踪）。",
        "风险提示：本邮件仅用于量化研究支持，不构成投资建议；历史规则存在过拟合风险。",
    ]
    message.set_content("\n".join(lines), subtype="plain", charset="utf-8")
    return message


def build_intraday_unavailable_message(
    *,
    config: QQEmailConfig,
    run_at: datetime,
    latest_date,
) -> EmailMessage:
    message = _new_message(
        config,
        f"[创业板AMV+情绪][盘中数据不可用] {run_at.date().isoformat()}",
    )
    latest_text = latest_date.isoformat() if latest_date is not None else "无"
    message.set_content(
        "\n".join(
            [
                "本次14:40任务未取得当天有效的创业板指数/情绪/0AMV数据。",
                f"运行时间：{run_at.isoformat(timespec='seconds')}",
                f"当前可用的最新数据日期：{latest_text}",
                "今日可能为非交易日，或者盘中行情/0AMV缓存尚未更新。",
                "本次不产生盘中买卖预估，也不会修改正式持仓状态。",
                "",
                "风险提示：盘中预估仅用于量化研究支持，不构成投资建议。",
            ]
        ),
        subtype="plain",
        charset="utf-8",
    )
    return message


def build_error_message(
    *,
    config: QQEmailConfig,
    error: str,
    run_at: datetime,
    mode: MessageMode = "close",
) -> EmailMessage:
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported message mode: {mode}")
    mode_label = "盘中预估错误" if mode == "intraday" else "收盘确认错误"
    message = _new_message(
        config,
        f"[创业板AMV+情绪][{mode_label}][ERROR] {run_at.date().isoformat()}",
    )
    message.set_content(
        "\n".join(
            [
                "本次运行未生成买卖信号，模型状态未改变。",
                f"运行时间：{run_at.isoformat(timespec='seconds')}",
                f"错误：{error}",
                "",
                "请检查网络、同花顺情绪接口，以及指南针0AMV本地缓存是否已更新。",
                "本邮件仅用于量化研究支持，不构成投资建议。",
            ]
        ),
        subtype="plain",
        charset="utf-8",
    )
    return message


def send_message(
    config: QQEmailConfig,
    message: EmailMessage,
    smtp_factory: Callable = smtplib.SMTP_SSL,
) -> None:
    with smtp_factory(
        config.host,
        config.port,
        timeout=30,
        context=ssl.create_default_context(),
    ) as smtp:
        smtp.login(config.address, config.auth_code)
        smtp.send_message(
            message,
            from_addr=config.address,
            to_addrs=[config.address],
        )
