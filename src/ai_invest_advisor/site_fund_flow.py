"""Theme-board fund-flow snapshot for the public research site.

Research support only; not investment advice.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.amv_cloud import beijing_today
from ai_invest_advisor.dashboard.metrics import normalize_fund_flow
from ai_invest_advisor.data.akshare_adapter import fetch_concept_fund_flow, fetch_industry_fund_flow
from ai_invest_advisor.data.tech_universe import classify_board

ROOT = Path(__file__).resolve().parents[2]
FLOW_PATH = ROOT / "data" / "site" / "theme_fund_flow.csv"


def theme_label(board_name: str) -> str:
    return "、".join(classify_board(board_name))


def filter_theme_flow(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["theme"] = out["board_name"].map(lambda name: theme_label(str(name)))
    out = out[out["theme"].astype(str).str.len() > 0].copy()
    return out.sort_values("net_amount", ascending=False).reset_index(drop=True)


def load_flow_snapshot(path: Path | str | None = None) -> pd.DataFrame:
    target = Path(path) if path is not None else FLOW_PATH
    if not target.exists():
        return pd.DataFrame()
    frame = pd.read_csv(target)
    if "theme" not in frame.columns and "board_name" in frame.columns:
        frame = filter_theme_flow(frame)
    return frame


def save_flow_snapshot(frame: pd.DataFrame, *, as_of: date, path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else FLOW_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["as_of"] = as_of.isoformat()
    out.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def fetch_live_theme_flow() -> pd.DataFrame:
    concept = normalize_fund_flow(fetch_concept_fund_flow("即时"), "concept")
    industry = normalize_fund_flow(fetch_industry_fund_flow("即时"), "industry")
    return filter_theme_flow(pd.concat([concept, industry], ignore_index=True))


def _snapshot_date(frame: pd.DataFrame) -> str | None:
    if frame.empty or "as_of" not in frame.columns:
        return None
    values = frame["as_of"].dropna()
    if values.empty:
        return None
    return str(values.iloc[-1])


def refresh_theme_flow(*, allow_network: bool = True) -> dict[str, Any]:
    banners: list[str] = []
    today = beijing_today()
    live: pd.DataFrame | None = None
    fetched = False
    if allow_network:
        try:
            live = fetch_live_theme_flow()
            if live.empty:
                raise RuntimeError("过滤后没有科技主题板块")
            save_flow_snapshot(live, as_of=today)
            fetched = True
        except Exception:
            live = None
            fetched = False
    cached = load_flow_snapshot()
    if fetched and live is not None:
        frame = live
        snapshot_date = today.isoformat()
        banners.append(f"资金流已按 {snapshot_date} 附近刷新。")
    elif not cached.empty:
        frame = cached
        snapshot_date = _snapshot_date(cached)
        if allow_network:
            banners.append(
                f"资金流今天没刷新，数据停在 {snapshot_date}。"
                if snapshot_date
                else "资金流今天没刷新，页面仍用上一份快照。"
            )
        else:
            banners.append(
                f"这次没上网拉资金流，数据停在 {snapshot_date}。"
                if snapshot_date
                else "这次没上网拉资金流，页面仍用上一份快照。"
            )
    else:
        frame = pd.DataFrame()
        snapshot_date = None
        banners.append("还没有可用的主题资金流数据。")
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            {
                "board_name": str(row.get("board_name", "")),
                "theme": str(row.get("theme", "")),
                "board_type": str(row.get("board_type", "")),
                "pct_change": None if pd.isna(row.get("pct_change")) else float(row["pct_change"]),
                "net_amount": None if pd.isna(row.get("net_amount")) else float(row["net_amount"]),
                "inflow": None if pd.isna(row.get("inflow")) else float(row["inflow"]),
                "outflow": None if pd.isna(row.get("outflow")) else float(row["outflow"]),
                "leader": str(row.get("leader", "") or ""),
                "leader_pct_change": None
                if pd.isna(row.get("leader_pct_change"))
                else float(row["leader_pct_change"]),
            }
        )
    total_net = float(sum(r["net_amount"] or 0.0 for r in rows))
    return {
        "schema_version": 1,
        "page": "flow",
        "disclaimer": "研究辅助，不是投资建议。",
        "disclaimer_long": "这是当天资金快照，只看科技相关主题，不是买卖建议。",
        "as_of": snapshot_date,
        "unit": "亿元",
        "total_net": total_net,
        "count": len(rows),
        "rows": rows,
        "banners": banners,
        "methodology": {
            "范围": "只保留名字对得上 PCB、CPO、机器人、液冷、商业航天、稀土、小金属、半导体相关的概念或行业板块。",
            "口径": "东财即时资金流（概念+行业），净额单位按源数据，一般为亿元。",
            "注意": "和「板块轮动」页用的行业 ETF 不是同一套名单，不能直接对照持仓。",
        },
        "risk_notes": [
            "资金流是盘中或收盘快照，隔天会变，也不代表后面一定涨。",
            "板块名字靠关键词归类，个别板块可能归得不准。",
            "研究辅助，不是投资建议。",
        ],
    }
