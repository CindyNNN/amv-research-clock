from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class MarketHeat:
    score: float
    label: str
    components: dict[str, float]
    explanation: str


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "nan", "None"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def normalize_fund_flow(frame: pd.DataFrame, board_type: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "board_name",
                "board_type",
                "pct_change",
                "inflow",
                "outflow",
                "net_amount",
                "company_count",
                "leader",
                "leader_pct_change",
                "current_price",
            ]
        )

    name_column = "行业" if "行业" in frame.columns else "板块名称"
    normalized = pd.DataFrame()
    normalized["board_name"] = frame[name_column].astype(str)
    normalized["board_type"] = board_type
    normalized["pct_change"] = frame.get("行业-涨跌幅", pd.Series([0] * len(frame))).map(to_float)
    normalized["inflow"] = frame.get("流入资金", pd.Series([0] * len(frame))).map(to_float)
    normalized["outflow"] = frame.get("流出资金", pd.Series([0] * len(frame))).map(to_float)
    normalized["net_amount"] = frame.get("净额", pd.Series([0] * len(frame))).map(to_float)
    normalized["company_count"] = frame.get("公司家数", pd.Series([0] * len(frame))).map(to_float)
    normalized["leader"] = frame.get("领涨股", pd.Series([""] * len(frame))).fillna("").astype(str)
    normalized["leader_pct_change"] = frame.get("领涨股-涨跌幅", pd.Series([0] * len(frame))).map(to_float)
    normalized["current_price"] = frame.get("当前价", pd.Series([0] * len(frame))).map(to_float)
    return normalized


def compute_market_heat(flow: pd.DataFrame) -> MarketHeat:
    if flow.empty:
        return MarketHeat(0.0, "数据不足", {}, "资金流数据为空，无法判断市场情绪。")

    pct = pd.to_numeric(flow["pct_change"], errors="coerce").fillna(0)
    inflow = pd.to_numeric(flow["inflow"], errors="coerce").fillna(0)
    outflow = pd.to_numeric(flow["outflow"], errors="coerce").fillna(0)
    net = pd.to_numeric(flow["net_amount"], errors="coerce").fillna(0)

    up_ratio_score = float((pct > 0).mean() * 100)
    denominator = float((inflow + outflow).sum())
    net_strength = float(net.sum() / denominator) if denominator else 0.0
    net_score = max(0.0, min(100.0, 50 + net_strength * 400))
    avg_change_score = max(0.0, min(100.0, 50 + float(pct.mean()) * 10))
    leader_score = max(0.0, min(100.0, 50 + float(pct.nlargest(min(10, len(pct))).mean()) * 5))

    heat = (
        up_ratio_score * 0.40
        + net_score * 0.25
        + avg_change_score * 0.20
        + leader_score * 0.15
    )
    heat = round(max(0.0, min(100.0, heat)), 2)
    if heat >= 70:
        label = "偏强"
    elif heat >= 45:
        label = "中性震荡"
    else:
        label = "偏弱"

    explanation = (
        f"上涨板块占比 {up_ratio_score:.1f}%，净流入强度 {net_strength:.2%}，"
        f"平均涨跌幅 {pct.mean():.2f}%。"
    )
    return MarketHeat(
        heat,
        label,
        {
            "up_ratio_score": round(up_ratio_score, 2),
            "net_score": round(net_score, 2),
            "avg_change_score": round(avg_change_score, 2),
            "leader_score": round(leader_score, 2),
        },
        explanation,
    )


def parse_info_frame(frame: pd.DataFrame) -> dict[str, str]:
    if frame.empty or "项目" not in frame.columns or "值" not in frame.columns:
        return {}
    return {str(row["项目"]): str(row["值"]) for _, row in frame.iterrows()}


def parse_breadth(value: str | None) -> float | None:
    if not value:
        return None
    parts = str(value).replace("：", "/").replace(":", "/").split("/")
    if len(parts) != 2:
        return None
    up = to_float(parts[0])
    down = to_float(parts[1])
    total = up + down
    return up / total if total else None


def _history_columns(history: pd.DataFrame) -> tuple[str, str, str]:
    date_col = "日期" if "日期" in history.columns else "date"
    close_col = "收盘价" if "收盘价" in history.columns else "close"
    amount_col = "成交额" if "成交额" in history.columns else "amount"
    return date_col, close_col, amount_col


def score_tech_board(
    board_name: str,
    theme: str,
    board_type: str,
    history: pd.DataFrame,
    flow: pd.Series | None,
    info: dict[str, str] | None,
) -> dict[str, Any]:
    info = info or {}
    flow = flow if flow is not None else pd.Series(dtype=object)
    date_col, close_col, amount_col = _history_columns(history)
    frame = history.copy()
    frame[date_col] = pd.to_datetime(frame[date_col])
    frame[close_col] = pd.to_numeric(frame[close_col], errors="coerce")
    frame[amount_col] = pd.to_numeric(frame.get(amount_col, 0), errors="coerce")
    frame = frame.dropna(subset=[close_col]).sort_values(date_col).reset_index(drop=True)

    if frame.empty:
        return {
            "board_name": board_name,
            "theme": theme,
            "board_type": board_type,
            "score": 0.0,
            "advice_label": "谨慎回避",
            "leader": "",
            "reasons": "数据不足",
            "risk_flags": "缺少历史K线",
        }

    close = frame[close_col]
    latest_close = float(close.iloc[-1])
    ma20 = float(close.rolling(20, min_periods=1).mean().iloc[-1])
    ma60 = float(close.rolling(60, min_periods=1).mean().iloc[-1])
    ma20_series = close.rolling(20, min_periods=1).mean()
    ma20_slope = float(ma20_series.iloc[-1] / ma20_series.iloc[-6] - 1) if len(ma20_series) >= 6 else 0.0
    ret5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else ret5
    drawdown20 = float(close.iloc[-1] / close.rolling(20, min_periods=1).max().iloc[-1] - 1)
    volatility20 = float(close.pct_change().rolling(20, min_periods=2).std().fillna(0).iloc[-1])

    trend_score = 0.0
    if latest_close >= ma20:
        trend_score += 10
    if latest_close >= ma60:
        trend_score += 10
    if ma20_slope > 0:
        trend_score += 10

    momentum_score = 0.0
    if ret5 > 0:
        momentum_score += min(10, 8 + ret5 * 100)
    if ret20 > 0:
        momentum_score += min(10, 8 + ret20 * 50)

    net_amount = to_float(flow.get("net_amount", info.get("资金净流入(亿)", 0)))
    inflow = to_float(flow.get("inflow", 0))
    outflow = to_float(flow.get("outflow", 0))
    turnover = to_float(info.get("成交额(亿)", 0))
    pct_change = to_float(flow.get("pct_change", str(info.get("板块涨幅", "0")).replace("%", "")))
    leader = str(flow.get("leader", ""))
    leader_pct = to_float(flow.get("leader_pct_change", 0))
    net_denominator = inflow + outflow if inflow + outflow else turnover
    net_ratio = net_amount / net_denominator if net_denominator else 0.0
    fund_score = 0.0
    if net_amount > 0:
        fund_score += 10
    fund_score += max(0.0, min(10.0, 50 * net_ratio))
    if pct_change > 0:
        fund_score += 5

    breadth = parse_breadth(info.get("涨跌家数"))
    breadth_score = (breadth * 15) if breadth is not None else 7.5

    risk_deduction = 0.0
    risk_flags: list[str] = []
    if ret5 > 0.15:
        risk_deduction += 4
        risk_flags.append("5日涨幅偏热")
    if drawdown20 < -0.08:
        risk_deduction += 3
        risk_flags.append("20日回撤较深")
    if volatility20 > 0.05:
        risk_deduction += 3
        risk_flags.append("波动率偏高")

    score = max(0.0, min(100.0, trend_score + momentum_score + fund_score + breadth_score - risk_deduction))
    if score >= 70 and net_amount >= 0 and ret5 <= 0.15:
        advice_label = "积极观察"
    elif score >= 60:
        advice_label = "等待回调"
    else:
        advice_label = "谨慎回避"

    reasons = [
        f"趋势 {trend_score:.0f}/30",
        f"动量 {momentum_score:.0f}/20",
        f"资金 {fund_score:.0f}/25",
        f"广度 {breadth_score:.0f}/15",
    ]
    return {
        "board_name": board_name,
        "theme": theme,
        "board_type": board_type,
        "latest_date": frame[date_col].iloc[-1].strftime("%Y-%m-%d"),
        "latest_close": round(latest_close, 3),
        "pct_change": round(pct_change, 2),
        "ret5": round(ret5 * 100, 2),
        "ret20": round(ret20 * 100, 2),
        "drawdown20": round(drawdown20 * 100, 2),
        "volatility20": round(volatility20 * 100, 2),
        "net_amount": round(net_amount, 2),
        "turnover": round(turnover, 2),
        "leader": leader,
        "leader_pct_change": round(leader_pct, 2),
        "score": round(score, 2),
        "advice_label": advice_label,
        "reasons": "；".join(reasons),
        "risk_flags": "；".join(risk_flags) if risk_flags else "无明显异常",
    }
