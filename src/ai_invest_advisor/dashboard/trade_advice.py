from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.dashboard.metrics import to_float


DISCLAIMER = "This is research support, not financial advice."


@dataclass(frozen=True)
class MarketTradeStance:
    label: str
    allocation_hint: str
    reasons: list[str] = field(default_factory=list)


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
    data_warnings: list[str] = field(default_factory=list)


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index)
    return frame[column].map(to_float)


def _text_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _risk_is_clear(risk_flags: str) -> bool:
    text = risk_flags.strip()
    if not text:
        return True
    lowered = text.lower()
    clear_values = {"-", "--", "none", "nan", "n/a", "na", "无", "无明显异常"}
    return lowered in clear_values or text.startswith("鏃犳槑")


def classify_market_stance(market_heat: float, tech_scores: pd.DataFrame) -> MarketTradeStance:
    avg_score = float(_numeric_column(tech_scores, "score").mean()) if not tech_scores.empty else 0.0
    total_net = float(_numeric_column(tech_scores, "net_amount").sum()) if not tech_scores.empty else 0.0
    reasons = [
        f"Market heat is {market_heat:.2f}.",
        f"Average technology-board score is {avg_score:.2f}.",
        f"Total net amount is {total_net:.2f}.",
    ]

    if market_heat >= 70 and avg_score >= 65 and total_net > 0:
        return MarketTradeStance(
            label="积极进攻",
            allocation_hint="科技主线可维持 50%-70% 观察仓，优先选择资金配合且不过热的方向。",
            reasons=reasons,
        )
    if market_heat < 45 or avg_score < 50 or total_net < 0:
        return MarketTradeStance(
            label="防守观望",
            allocation_hint="科技主线建议降到 20%-40% 观察仓，优先控制回撤。",
            reasons=reasons,
        )
    return MarketTradeStance(
        label="均衡持有",
        allocation_hint="科技主线保持 40%-60% 观察仓，等待强弱分化更清晰。",
        reasons=reasons,
    )


def _advice_from_row(row: pd.Series, action: str) -> BoardTradeAdvice:
    score = to_float(row.get("score", 0))
    net_amount = to_float(row.get("net_amount", 0))
    ret5 = to_float(row.get("ret5", 0))
    risk_flags = _text_value(row.get("risk_flags", ""))
    reasons = [f"Score is {score:.2f}.", f"5-day return is {ret5:.2f}%.", f"Net amount is {net_amount:.2f}."]
    if action == "可关注":
        reasons.append("Trend, flow, and short-term heat are aligned.")
    elif action == "等待回调":
        reasons.append("Signal is strong, but short-term heat or risk flags require patience.")
    else:
        reasons.append("Risk/reward is not attractive enough for new exposure.")

    return BoardTradeAdvice(
        board_name=_text_value(row.get("board_name", "")),
        theme=_text_value(row.get("theme", "")),
        action=action,
        score=score,
        net_amount=net_amount,
        ret5=ret5,
        leader=_text_value(row.get("leader", "")),
        leader_pct_change=to_float(row.get("leader_pct_change", 0)),
        reasons=reasons,
        risk_flags=risk_flags or "无明显异常",
    )


def build_trade_advice(
    scores: pd.DataFrame,
    market_heat: float,
    data_warnings: list[str] | None = None,
    top: int = 8,
) -> TradeAdviceReport:
    frame = scores.copy()
    if frame.empty:
        warnings = list(data_warnings or [])
        warnings.append("Technology-board score cache is empty.")
        return TradeAdviceReport(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stance=classify_market_stance(market_heat, frame),
            focus=[],
            wait_for_pullback=[],
            reduce_or_avoid=[],
            data_warnings=warnings,
        )

    frame["score"] = _numeric_column(frame, "score")
    frame["ret5"] = _numeric_column(frame, "ret5")
    frame["net_amount"] = _numeric_column(frame, "net_amount")
    frame["risk_flags"] = frame.get("risk_flags", pd.Series([""] * len(frame))).map(_text_value)

    focus_rows = frame[
        (frame["score"] >= 70)
        & (frame["net_amount"] >= 0)
        & (frame["ret5"] <= 12)
        & frame["risk_flags"].map(_risk_is_clear)
    ].sort_values(["score", "net_amount"], ascending=[False, False])

    wait_rows = frame[
        (frame["score"] >= 60)
        & ~frame.index.isin(focus_rows.index)
        & ((frame["ret5"] > 12) | ~frame["risk_flags"].map(_risk_is_clear))
        & (frame["net_amount"] >= 0)
    ].sort_values(["score", "net_amount"], ascending=[False, False])

    used = focus_rows.index.union(wait_rows.index)
    reduce_rows = frame[~frame.index.isin(used)].sort_values(["score", "net_amount"], ascending=[True, True])

    return TradeAdviceReport(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        stance=classify_market_stance(market_heat, frame),
        focus=[_advice_from_row(row, "可关注") for _, row in focus_rows.head(top).iterrows()],
        wait_for_pullback=[_advice_from_row(row, "等待回调") for _, row in wait_rows.head(top).iterrows()],
        reduce_or_avoid=[_advice_from_row(row, "减仓/回避") for _, row in reduce_rows.head(top).iterrows()],
        data_warnings=list(data_warnings or []),
    )


def _advice_table(items: list[BoardTradeAdvice]) -> str:
    if not items:
        return "暂无建议。"
    lines = [
        "| 板块 | 主题 | 动作 | 评分 | 5日涨幅 | 净额 | 龙头 | 风险 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in items:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.board_name,
                    item.theme,
                    item.action,
                    f"{item.score:.2f}",
                    f"{item.ret5:.2f}%",
                    f"{item.net_amount:.2f}",
                    item.leader,
                    item.risk_flags,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_trade_advice_markdown(
    report: TradeAdviceReport,
    data_status: dict[str, object] | None = None,
) -> str:
    status_lines = []
    for key, value in (data_status or {}).items():
        status_lines.append(f"- {key}: {value}")
    status_text = "\n".join(status_lines) if status_lines else "- No data status file available."

    warning_text = "\n".join(f"- {warning}" for warning in report.data_warnings) if report.data_warnings else "- No warnings."
    stance_reasons = "\n".join(f"- {reason}" for reason in report.stance.reasons)

    return f"""# 科技板块交易建议

生成时间：{report.generated_at}

## 市场姿态

- 姿态：{report.stance.label}
- 仓位建议：{report.stance.allocation_hint}

{stance_reasons}

## 可关注

{_advice_table(report.focus)}

## 等待回调

{_advice_table(report.wait_for_pullback)}

## 减仓/回避

{_advice_table(report.reduce_or_avoid)}

## 数据状态

{status_text}

## 风险提示

{warning_text}
- {DISCLAIMER}
"""


def _load_market_heat(cache_dir: Path, warnings: list[str]) -> tuple[float, str]:
    sentiment_path = cache_dir / "sentiment_history.csv"
    if not sentiment_path.exists():
        warnings.append("sentiment_history.csv is missing; market heat defaults to 0.")
        return 0.0, datetime.now().strftime("%Y-%m-%d")

    sentiment = pd.read_csv(sentiment_path)
    if sentiment.empty:
        warnings.append("sentiment_history.csv is empty; market heat defaults to 0.")
        return 0.0, datetime.now().strftime("%Y-%m-%d")

    latest = sentiment.iloc[-1]
    report_date = _text_value(latest.get("date", "")) or datetime.now().strftime("%Y-%m-%d")
    return to_float(latest.get("market_heat", 0)), report_date


def _load_data_status(cache_dir: Path, warnings: list[str]) -> dict[str, object]:
    status_path = cache_dir / "data_status.json"
    if not status_path.exists():
        warnings.append("data_status.json is missing.")
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        warnings.append("data_status.json is malformed.")
        return {}


def generate_trade_advice_report(cache_dir: Path, output_dir: Path, top: int = 8) -> Path:
    scores_path = cache_dir / "tech_board_scores.csv"
    if not scores_path.exists():
        raise FileNotFoundError(f"{scores_path} is missing. Run refresh-dashboard first.")

    warnings: list[str] = []
    scores = pd.read_csv(scores_path)
    market_heat, report_date = _load_market_heat(cache_dir, warnings)
    data_status = _load_data_status(cache_dir, warnings)
    report = build_trade_advice(scores, market_heat=market_heat, data_warnings=warnings, top=top)
    markdown = render_trade_advice_markdown(report, data_status)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report_date}-tech-trade-advice.md"
    output_path.write_text(markdown, encoding="utf-8")
    return output_path
