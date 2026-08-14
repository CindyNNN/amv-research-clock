"""Liquid A-share industry/theme ETFs mapped to the project's tech universe.

History is fetched with Tencent fqkline (same path as 159915). Eastmoney
`fund_etf_hist_em` was observed unavailable in Aug 2026 (see GitHub
Loss-of-time/tmp3). There is no dedicated large PCB or liquid-cooling ETF;
those themes use the closest liquid proxy and must not be treated as a pure
board tracker.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai_invest_advisor.amv_index_data import IndexSpec


@dataclass(frozen=True)
class SectorEtfSpec:
    spec: IndexSpec
    theme: str
    proxy_note: str = ""


SECTOR_ETF_UNIVERSE: tuple[SectorEtfSpec, ...] = (
    SectorEtfSpec(IndexSpec("159915", "创业板ETF", "sz159915"), "创业板对照"),
    SectorEtfSpec(IndexSpec("512480", "半导体ETF", "sh512480"), "半导体"),
    SectorEtfSpec(IndexSpec("159995", "芯片ETF", "sz159995"), "芯片"),
    SectorEtfSpec(IndexSpec("159516", "半导体设备ETF", "sz159516"), "半导体设备"),
    SectorEtfSpec(IndexSpec("515880", "通信ETF", "sh515880"), "CPO通信", "通信/光模块代理，非纯CPO"),
    SectorEtfSpec(IndexSpec("562500", "机器人ETF", "sh562500"), "机器人"),
    SectorEtfSpec(IndexSpec("159667", "工业母机ETF", "sz159667"), "机器人", "工业母机，机器人链条上游"),
    SectorEtfSpec(IndexSpec("512660", "军工ETF", "sh512660"), "商业航天", "军工代理，含航天但不是纯商业航天"),
    SectorEtfSpec(IndexSpec("516150", "稀土ETF", "sh516150"), "稀土"),
    SectorEtfSpec(IndexSpec("512400", "有色金属ETF", "sh512400"), "小金属", "有色代理，不全是小金属"),
    SectorEtfSpec(IndexSpec("159819", "人工智能ETF", "sz159819"), "液冷算力", "AI/算力代理，不是纯液冷"),
)
