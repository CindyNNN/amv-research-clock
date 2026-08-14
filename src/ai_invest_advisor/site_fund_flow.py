"""Theme-board fund-flow snapshot for the public research site.

Research support only; not investment advice.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.amv_cloud import beijing_today
from ai_invest_advisor.dashboard.metrics import to_float
from ai_invest_advisor.data.akshare_adapter import fetch_concept_fund_flow, fetch_industry_fund_flow
from ai_invest_advisor.data.tech_universe import classify_board

ROOT = Path(__file__).resolve().parents[2]
FLOW_PATH = ROOT / "data" / "site" / "theme_fund_flow.csv"
FLOW_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("1d", "即时", "当日"),
    ("3d", "3日排行", "3日"),
    ("5d", "5日排行", "5日"),
    ("10d", "10日排行", "10日"),
    ("20d", "20日排行", "20日"),
)
WINDOW_FIELDS = tuple(key for key, _symbol, _label in FLOW_WINDOWS)


def theme_label(board_name: str) -> str:
    return "、".join(classify_board(board_name))


def filter_theme_flow(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    if "theme" not in out.columns:
        out["theme"] = out["board_name"].map(lambda name: theme_label(str(name)))
    else:
        missing = out["theme"].isna() | (out["theme"].astype(str).str.len() == 0)
        if missing.any():
            out.loc[missing, "theme"] = out.loc[missing, "board_name"].map(
                lambda name: theme_label(str(name))
            )
    out = out[out["theme"].astype(str).str.len() > 0].copy()
    sort_col = "net_amount" if "net_amount" in out.columns else "net_1d"
    if sort_col in out.columns:
        out = out.sort_values(sort_col, ascending=False)
    return out.reset_index(drop=True)


def normalize_theme_flow(frame: pd.DataFrame, board_type: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "board_name",
                "board_type",
                "pct_change",
                "inflow",
                "outflow",
                "net_amount",
                "leader",
                "leader_pct_change",
            ]
        )
    name_column = "行业" if "行业" in frame.columns else "板块名称"
    pct_column = "行业-涨跌幅" if "行业-涨跌幅" in frame.columns else "阶段涨跌幅"
    out = pd.DataFrame()
    out["board_name"] = frame[name_column].astype(str)
    out["board_type"] = board_type
    pct = frame.get(pct_column, pd.Series([None] * len(frame)))
    if getattr(pct, "dtype", None) == object:
        pct = pct.astype(str).str.replace("%", "", regex=False)
    out["pct_change"] = pd.to_numeric(pct, errors="coerce")
    out["inflow"] = frame.get("流入资金", pd.Series([None] * len(frame))).map(
        lambda value: to_float(value, default=float("nan"))
    )
    out["outflow"] = frame.get("流出资金", pd.Series([None] * len(frame))).map(
        lambda value: to_float(value, default=float("nan"))
    )
    out["net_amount"] = frame.get("净额", pd.Series([None] * len(frame))).map(
        lambda value: to_float(value, default=float("nan"))
    )
    if "领涨股" in frame.columns:
        out["leader"] = frame["领涨股"].fillna("").astype(str)
        leader_pct = frame.get("领涨股-涨跌幅", pd.Series([None] * len(frame)))
        if getattr(leader_pct, "dtype", None) == object:
            leader_pct = leader_pct.astype(str).str.replace("%", "", regex=False)
        out["leader_pct_change"] = pd.to_numeric(leader_pct, errors="coerce")
    else:
        out["leader"] = ""
        out["leader_pct_change"] = float("nan")
    return out


def load_flow_snapshot(path: Path | str | None = None) -> pd.DataFrame:
    target = Path(path) if path is not None else FLOW_PATH
    if not target.exists():
        return pd.DataFrame()
    frame = pd.read_csv(target)
    if "theme" not in frame.columns and "board_name" in frame.columns:
        frame = filter_theme_flow(frame)
    if "net_1d" not in frame.columns and "net_amount" in frame.columns:
        frame["net_1d"] = frame["net_amount"]
    if "pct_1d" not in frame.columns and "pct_change" in frame.columns:
        frame["pct_1d"] = frame["pct_change"]
    return frame


def save_flow_snapshot(frame: pd.DataFrame, *, as_of: date, path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else FLOW_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["as_of"] = as_of.isoformat()
    out.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def fetch_period_theme_flow(symbol: str) -> pd.DataFrame:
    concept = normalize_theme_flow(fetch_concept_fund_flow(symbol), "concept")
    industry = normalize_theme_flow(fetch_industry_fund_flow(symbol), "industry")
    return filter_theme_flow(pd.concat([concept, industry], ignore_index=True))


def merge_flow_windows(windows: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not windows:
        return pd.DataFrame()
    pieces = []
    for key, frame in windows.items():
        if frame is None or frame.empty:
            continue
        part = frame[["board_name", "board_type"]].copy()
        part["theme"] = frame["theme"] if "theme" in frame.columns else ""
        pieces.append(part)
    if not pieces:
        return pd.DataFrame()
    merged = pd.concat(pieces, ignore_index=True).drop_duplicates(
        subset=["board_name", "board_type"]
    )
    for key, _symbol, _label in FLOW_WINDOWS:
        frame = windows.get(key)
        if frame is None or frame.empty:
            continue
        sub = frame[["board_name", "board_type", "pct_change", "net_amount"]].rename(
            columns={"pct_change": f"pct_{key}", "net_amount": f"net_{key}"}
        )
        merged = merged.merge(sub, on=["board_name", "board_type"], how="left")
        if key == "1d":
            extra_cols = [
                col
                for col in ("pct_change", "net_amount", "inflow", "outflow", "leader", "leader_pct_change")
                if col in frame.columns
            ]
            extra = frame[["board_name", "board_type", *extra_cols]].copy()
            merged = merged.merge(extra, on=["board_name", "board_type"], how="left")
    if "theme" in merged.columns:
        missing = merged["theme"].isna() | (merged["theme"].astype(str).str.len() == 0)
        if missing.any():
            merged.loc[missing, "theme"] = merged.loc[missing, "board_name"].map(
                lambda name: theme_label(str(name))
            )
    sort_col = "net_amount" if "net_amount" in merged.columns else "net_1d"
    if sort_col in merged.columns:
        merged = merged.sort_values(sort_col, ascending=False)
    return merged.reset_index(drop=True)


def _snapshot_date(frame: pd.DataFrame) -> str | None:
    if frame.empty or "as_of" not in frame.columns:
        return None
    values = frame["as_of"].dropna()
    if values.empty:
        return None
    return str(values.iloc[-1])


def _num(row: pd.Series, key: str) -> float | None:
    if key not in row.index:
        return None
    value = row[key]
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return None
    return float(value)


def flow_row_payload(row: pd.Series) -> dict[str, Any]:
    net_amount = _num(row, "net_amount")
    if net_amount is None:
        net_amount = _num(row, "net_1d")
    pct_change = _num(row, "pct_change")
    if pct_change is None:
        pct_change = _num(row, "pct_1d")
    return {
        "board_name": str(row.get("board_name", "")),
        "theme": str(row.get("theme", "") or ""),
        "board_type": str(row.get("board_type", "") or ""),
        "pct_change": pct_change,
        "net_amount": net_amount,
        "inflow": _num(row, "inflow"),
        "outflow": _num(row, "outflow"),
        "leader": str(row.get("leader", "") or ""),
        "leader_pct_change": _num(row, "leader_pct_change"),
        "net_1d": _num(row, "net_1d") if _num(row, "net_1d") is not None else net_amount,
        "pct_1d": _num(row, "pct_1d") if _num(row, "pct_1d") is not None else pct_change,
        "net_3d": _num(row, "net_3d"),
        "pct_3d": _num(row, "pct_3d"),
        "net_5d": _num(row, "net_5d"),
        "pct_5d": _num(row, "pct_5d"),
        "net_10d": _num(row, "net_10d"),
        "pct_10d": _num(row, "pct_10d"),
        "net_20d": _num(row, "net_20d"),
        "pct_20d": _num(row, "pct_20d"),
    }


def window_meta() -> list[dict[str, str]]:
    return [
        {
            "id": key,
            "label": label,
            "symbol": symbol,
            "net_field": "net_amount" if key == "1d" else f"net_{key}",
            "pct_field": "pct_change" if key == "1d" else f"pct_{key}",
        }
        for key, symbol, label in FLOW_WINDOWS
    ]


def fetch_live_theme_flow_windows() -> tuple[dict[str, pd.DataFrame], list[str]]:
    windows: dict[str, pd.DataFrame] = {}
    banners: list[str] = []
    for key, symbol, label in FLOW_WINDOWS:
        try:
            frame = fetch_period_theme_flow(symbol)
            if frame.empty:
                raise RuntimeError("过滤后没有科技主题板块")
            windows[key] = frame
        except Exception:
            banners.append(f"{label}资金流没拉到。")
    return windows, banners


def refresh_theme_flow(*, allow_network: bool = True) -> dict[str, Any]:
    banners: list[str] = []
    today = beijing_today()
    live: pd.DataFrame | None = None
    fetched = False
    missing_windows: list[str] = []
    if allow_network:
        windows, period_banners = fetch_live_theme_flow_windows()
        missing_windows = [
            label for key, _symbol, label in FLOW_WINDOWS if key not in windows
        ]
        if "1d" in windows:
            live = merge_flow_windows(windows)
            save_flow_snapshot(live, as_of=today)
            fetched = True
            banners.extend(period_banners)
        else:
            banners.extend(period_banners)
    cached = load_flow_snapshot()
    if fetched and live is not None:
        frame = live
        snapshot_date = today.isoformat()
        if missing_windows:
            banners.insert(0, f"资金流已按 {snapshot_date} 附近刷新，但{'、'.join(missing_windows)}还没有。")
        else:
            banners.insert(0, f"资金流已按 {snapshot_date} 附近刷新。")
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
    rows = [flow_row_payload(row) for _, row in frame.iterrows()]
    totals = {
        "1d": float(sum(r["net_1d"] or 0.0 for r in rows)),
        "3d": float(sum(r["net_3d"] or 0.0 for r in rows)),
        "5d": float(sum(r["net_5d"] or 0.0 for r in rows)),
        "10d": float(sum(r["net_10d"] or 0.0 for r in rows)),
        "20d": float(sum(r["net_20d"] or 0.0 for r in rows)),
    }
    return {
        "schema_version": 2,
        "page": "flow",
        "disclaimer": "研究辅助，不是投资建议。",
        "disclaimer_long": "这是资金快照，只看科技相关主题，不是买卖建议。",
        "as_of": snapshot_date,
        "unit": "亿元",
        "total_net": totals["1d"],
        "totals": totals,
        "count": len(rows),
        "windows": window_meta(),
        "default_window": "1d",
        "rows": rows,
        "banners": list(dict.fromkeys(banners)),
        "methodology": {
            "范围": "只保留名字对得上 PCB、CPO、机器人、液冷、商业航天、稀土、小金属、半导体相关的概念或行业板块。",
            "口径": "同花顺概念/行业资金流：当日是即时净额，3日、5日、10日、20日是阶段累计净额，单位一般为亿元。",
            "注意": "和「板块轮动」页用的行业 ETF 不是同一套名单，不能直接对照持仓。同一只股票也可能出现在多个板块里，上面的合计净流入会重复计算。",
        },
        "risk_notes": [
            "资金流是盘中或收盘快照，隔天会变，也不代表后面一定涨。",
            "3日到20日是阶段累计，不是把当日简单加总。",
            "板块名字靠关键词归类，个别板块可能归得不准。",
            "研究辅助，不是投资建议。",
        ],
    }
