from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from ai_invest_advisor.config import Settings, load_settings
from ai_invest_advisor.data.akshare_adapter import fetch_concept_fund_flow, fetch_industry_fund_flow
from ai_invest_advisor.dashboard.metrics import (
    MarketHeat,
    compute_market_heat,
    normalize_fund_flow,
    parse_info_frame,
    score_tech_board,
)

DASHBOARD_CACHE_DIR = Path("data/dashboard/latest")


@dataclass(frozen=True)
class DashboardRefreshResult:
    cache_dir: Path
    market_flow_path: Path
    tech_scores_path: Path
    sentiment_path: Path
    status_path: Path
    market_heat: MarketHeat
    row_count: int
    status: str


def latest_tech_board_dir(root: Path) -> Path:
    candidates = [
        path for path in root.glob("tech_boards_*")
        if path.is_dir() and (path / "tech_concept_boards.csv").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"No technology board data directory found under {root}")
    return sorted(candidates)[-1]


def append_sentiment_snapshot(path: Path, date: str, generated_at: str, market_heat: float, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_row = pd.DataFrame(
        [
            {
                "date": date,
                "generated_at": generated_at,
                "market_heat": round(float(market_heat), 2),
                "label": label,
            }
        ]
    )
    if path.exists():
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_row], ignore_index=True)
    else:
        combined = new_row
    combined.to_csv(path, index=False, encoding="utf-8-sig")


def _load_board_metadata(base: Path) -> pd.DataFrame:
    concept = pd.read_csv(base / "tech_concept_boards.csv")
    concept["board_type"] = "concept"
    industry_path = base / "tech_industry_boards.csv"
    if industry_path.exists():
        industry = pd.read_csv(industry_path)
        industry["board_type"] = "industry"
        return pd.concat([concept, industry], ignore_index=True)
    return concept


def _load_named_frames(paths: list[Path]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in paths:
        frame = pd.read_csv(path)
        if "板块名称" in frame.columns and not frame.empty:
            frames[str(frame["板块名称"].iloc[0])] = frame
    return frames


def _load_info_maps(paths: list[Path]) -> dict[str, dict[str, str]]:
    maps: dict[str, dict[str, str]] = {}
    for path in paths:
        frame = pd.read_csv(path)
        if "板块名称" in frame.columns and not frame.empty:
            maps[str(frame["板块名称"].iloc[0])] = parse_info_frame(frame)
    return maps


def _load_market_flow(cache_dir: Path, allow_network: bool) -> tuple[pd.DataFrame, str]:
    market_flow_path = cache_dir / "market_flow.csv"
    empty_flow = normalize_fund_flow(pd.DataFrame(), "unknown")
    if allow_network:
        try:
            concept = normalize_fund_flow(fetch_concept_fund_flow("即时"), "concept")
            industry = normalize_fund_flow(fetch_industry_fund_flow("即时"), "industry")
            flow = pd.concat([concept, industry], ignore_index=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            flow.to_csv(market_flow_path, index=False, encoding="utf-8-sig")
            return flow, "live"
        except Exception as exc:
            if market_flow_path.exists():
                return pd.read_csv(market_flow_path), f"cached_after_fetch_error: {exc}"
            return empty_flow, f"fetch_error: {exc}"

    if market_flow_path.exists():
        return pd.read_csv(market_flow_path), "cached"
    return empty_flow, "missing_market_flow_cache"


def _score_boards(tech_base: Path, flow: pd.DataFrame) -> pd.DataFrame:
    metadata = _load_board_metadata(tech_base)
    histories = _load_named_frames(
        list((tech_base / "ths_concept" / "history").glob("*.csv"))
        + list((tech_base / "ths_industry" / "history").glob("*.csv"))
    )
    infos = _load_info_maps(
        list((tech_base / "ths_concept" / "info").glob("*.csv"))
        + list((tech_base / "ths_industry" / "info").glob("*.csv"))
    )

    rows: list[dict[str, object]] = []
    for _, board in metadata.iterrows():
        board_name = str(board["板块名称"])
        history = histories.get(board_name, pd.DataFrame())
        flow_match = flow[flow["board_name"] == board_name]
        flow_row = flow_match.iloc[0] if not flow_match.empty else None
        rows.append(
            score_tech_board(
                board_name,
                str(board["主题"]),
                str(board["board_type"]),
                history,
                flow_row,
                infos.get(board_name, {}),
            )
        )
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


def refresh_dashboard_cache(
    settings: Settings | None = None,
    cache_dir: Path = DASHBOARD_CACHE_DIR,
    allow_network: bool = True,
) -> DashboardRefreshResult:
    settings = settings or load_settings()
    cache_dir.mkdir(parents=True, exist_ok=True)
    tech_base = latest_tech_board_dir(settings.tech_boards.output_dir)
    flow, status = _load_market_flow(cache_dir, allow_network)
    heat = compute_market_heat(flow)
    scores = _score_boards(tech_base, flow)

    market_flow_path = cache_dir / "market_flow.csv"
    tech_scores_path = cache_dir / "tech_board_scores.csv"
    sentiment_path = cache_dir / "sentiment_history.csv"
    status_path = cache_dir / "data_status.json"
    if not flow.empty and not market_flow_path.exists():
        flow.to_csv(market_flow_path, index=False, encoding="utf-8-sig")
    scores.to_csv(tech_scores_path, index=False, encoding="utf-8-sig")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latest_date = str(scores["latest_date"].max()) if "latest_date" in scores.columns and not scores.empty else datetime.now().strftime("%Y-%m-%d")
    append_sentiment_snapshot(sentiment_path, latest_date, generated_at, heat.score, heat.label)
    status_payload = {
        "generated_at": generated_at,
        "status": status,
        "tech_board_source": str(tech_base),
        "market_heat": heat.score,
        "market_heat_label": heat.label,
        "row_count": int(len(scores)),
    }
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return DashboardRefreshResult(
        cache_dir=cache_dir,
        market_flow_path=market_flow_path,
        tech_scores_path=tech_scores_path,
        sentiment_path=sentiment_path,
        status_path=status_path,
        market_heat=heat,
        row_count=len(scores),
        status=status,
    )
