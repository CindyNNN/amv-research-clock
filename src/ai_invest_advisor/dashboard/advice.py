from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DailyAdvice:
    stance: str
    allocation_hint: str
    summary: str
    risk_notes: list[str]


def build_daily_advice(market_heat: float, tech_scores: pd.DataFrame) -> DailyAdvice:
    avg_score = float(tech_scores["score"].mean()) if not tech_scores.empty else 0.0
    net_amount = float(tech_scores["net_amount"].sum()) if "net_amount" in tech_scores.columns and not tech_scores.empty else 0.0

    if market_heat >= 70 and avg_score >= 65 and net_amount > 0:
        return DailyAdvice(
            stance="偏进攻",
            allocation_hint="科技主线可保持较高关注，优先看评分靠前且资金净流入的细分方向。",
            summary="市场热度和科技板块评分同时偏强，适合围绕强势主线做观察清单。",
            risk_notes=["避免追高连续急涨板块。", "单一主题不宜过度集中。"],
        )
    if market_heat < 45 or avg_score < 50 or net_amount < 0:
        return DailyAdvice(
            stance="偏防守",
            allocation_hint="降低进攻性，优先等待资金回流和趋势修复。",
            summary="市场热度或科技板块资金偏弱，当前更适合控制仓位和观察分歧修复。",
            risk_notes=["关注指数和强势板块是否同步走弱。", "避免在资金流出时扩大风险暴露。"],
        )
    return DailyAdvice(
        stance="均衡",
        allocation_hint="保持中性仓位，围绕强势板块做小范围跟踪。",
        summary="市场处于震荡区间，科技板块有结构性机会但需要精选方向。",
        risk_notes=["关注成交额是否持续放大。", "弱势主题反弹不等于趋势反转。"],
    )
