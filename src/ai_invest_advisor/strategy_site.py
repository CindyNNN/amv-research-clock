"""Build the public TooColdCC-style research site JSON.

Research support only; not investment advice. Never trades.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.amv_cloud import (
    CLOUD_AMV_PATH,
    append_amv_close,
    beijing_today,
    duplicate_tail_dates,
    github_repo,
    load_cloud_amv,
    pages_url,
    seed_cloud_amv_from_local,
    trusted_amv_frame,
    trusted_amv_last,
)
from ai_invest_advisor.amv_index_backtest import buy_and_hold_equity, run_index_backtest, summarize_backtest
from ai_invest_advisor.amv_index_data import (
    DEFAULT_CACHE_DIR,
    IndexSpec,
    align_index_with_amv,
    build_amv_signals,
    download_index_daily,
    load_amv_daily,
)
from ai_invest_advisor.cyb_emotion_amv_combo import CombinedRule, apply_combined_rule, load_emotion_frame
from ai_invest_advisor.cyb_market_data import add_indicators, load_complete_history
from ai_invest_advisor.sector_etf_rotation import RotRule, rotate
from ai_invest_advisor.sector_etf_universe import SECTOR_ETF_UNIVERSE
from ai_invest_advisor.site_fund_flow import refresh_theme_flow

ROOT = Path(__file__).resolve().parents[2]
SITE_DIR = ROOT / "site"
SITE_DATA = SITE_DIR / "data"
AGENT_DIR = SITE_DATA / "agent"
COST = 0.001
START = date(2020, 1, 2)
NAV_BASE = 1000.0
EMOTION_SLEEVES = (50.0, 55.0, 60.0, 65.0, 70.0)
CLOCK_RULE = CombinedRule(
    name="amv_emo70_ma60",
    entry_mode="amv",
    emotion_entry_max=None,
    j_entry_max=None,
    amv_entry_two_day=0.03,
    exit_mode="emotion",
    amv_exit_threshold=None,
    emotion_exit_min=70.0,
    exit_ignore_if_above_ma=60,
    min_hold_days=0,
)
OVERLAYS: tuple[IndexSpec, ...] = (
    IndexSpec("000001", "上证指数", "sh000001"),
    IndexSpec("399001", "深证成指", "sz399001"),
    IndexSpec("399006", "创业板指", "sz399006"),
    IndexSpec("000300", "沪深300", "sh000300"),
    IndexSpec("159915", "创业板ETF", "sz159915"),
)
ETF_SPEC = OVERLAYS[-1]
DISCLAIMER = "研究辅助，不是投资建议。"
DISCLAIMER_LONG = (
    "这里的净值、仓位、图表只供自己复盘看，不是投资建议，也不保证以后还能这样。"
    "不会帮你下单，也不会要券商账号。"
)
DEFAULT_RISK_NOTES = [
    "回测里按单边千分之一算了费用，过去的结果不代表以后也能赚到。",
    "0AMV 要自己从指南针抄收盘价过来，网站不会自动算。",
    "信号按当天收盘确认，第二天开盘才成交；最后一天的仓位按收盘估算。",
    DISCLAIMER,
]
ROT_RULE = RotRule(
    name="m120_k2_raw_gate",
    lookback=120,
    top_k=2,
    skip_negative=False,
    use_gate=True,
)


@dataclass
class BuildStatus:
    banners: list[str]
    amv_as_of: str
    amv_trusted_as_of: str
    amv_last_close: float
    amv_trusted_close: float
    emotion_as_of: str | None
    overlay_as_of: str | None
    amv_stale: bool
    emotion_stale: bool
    generated_at_utc: str


def _iso(value: Any) -> str:
    return str(pd.Timestamp(value).date())


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def download_with_fallback(
    spec: IndexSpec,
    *,
    start: date,
    end: date,
    force: bool,
    banners: list[str],
) -> pd.DataFrame | None:
    cache_path = DEFAULT_CACHE_DIR / f"index_{spec.tencent_symbol}_daily.csv"
    try:
        return download_index_daily(
            spec, start=start, end=end, cache_dir=DEFAULT_CACHE_DIR, force=force
        )
    except Exception as exc:
        if cache_path.exists():
            banners.append(
                f"{spec.name} 在线拉取失败，改用本地缓存 {cache_path.name}：{exc}"
            )
            frame = pd.read_csv(cache_path, parse_dates=["date"])
            frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
            return frame.sort_values("date").reset_index(drop=True)
        banners.append(f"{spec.name} 无数据，对比层将缺少该标的：{exc}")
        return None


def try_refresh_emotion(*, as_of: date, banners: list[str]) -> pd.DataFrame:
    now = datetime.now()
    try:
        load_complete_history(as_of=as_of, now=now, mode="close")
        banners.append("市场情绪已按收盘尽量刷新。")
    except Exception as exc:
        banners.append(f"市场情绪今天没刷上，仍用仓库里上一份。原因：{exc}")
    emotion = load_emotion_frame()
    return emotion


def yearly_excess(
    strategy: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    equity_col: str = "nav",
    bench_col: str = "nav",
) -> list[dict[str, Any]]:
    left = strategy[["date", equity_col]].rename(columns={equity_col: "s"})
    right = benchmark[["date", bench_col]].rename(columns={bench_col: "b"})
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    right["date"] = pd.to_datetime(right["date"]).dt.normalize()
    merged = left.merge(right, on="date", how="inner").sort_values("date")
    if merged.empty:
        return []
    merged["year"] = merged["date"].dt.year
    rows: list[dict[str, Any]] = []
    first_year = int(merged["year"].iloc[0])
    last_year = int(merged["year"].iloc[-1])
    last_date = merged["date"].iloc[-1]
    for year, group in merged.groupby("year"):
        year = int(year)
        start_row = merged[merged["date"] < group["date"].iloc[0]]
        start_s = float(start_row["s"].iloc[-1]) if not start_row.empty else float(group["s"].iloc[0])
        start_b = float(start_row["b"].iloc[-1]) if not start_row.empty else float(group["b"].iloc[0])
        if start_s <= 0 or start_b <= 0:
            continue
        index_return = float(group["s"].iloc[-1] / start_s - 1.0)
        bench_return = float(group["b"].iloc[-1] / start_b - 1.0)
        excess = (1.0 + index_return) / (1.0 + bench_return) - 1.0 if bench_return != -1 else float("nan")
        coverage_start = pd.Timestamp(group["date"].iloc[0])
        coverage_end = pd.Timestamp(group["date"].iloc[-1])
        incomplete_start = coverage_start > pd.Timestamp(year, 1, 15)
        incomplete_end = coverage_end < pd.Timestamp(year, 12, 15)
        partial = year == last_year or incomplete_start or incomplete_end
        if year == first_year and incomplete_start and year != last_year:
            continue
        rows.append(
            {
                "year": year,
                "index_return": index_return,
                "benchmark_return": bench_return,
                "excess": None if math.isnan(excess) else excess,
                "partial": bool(partial),
                "start_date": str(coverage_start.date()),
                "end_date": str(coverage_end.date()),
            }
        )
    if rows and last_date is not None:
        rows[-1]["as_of"] = str(pd.Timestamp(last_date).date())
    return rows


def period_return(
    nav: pd.Series,
    dates: pd.Series,
    *,
    bars: int | None = None,
    ytd: bool = False,
) -> dict[str, Any]:
    frame = pd.DataFrame({"date": pd.to_datetime(dates), "nav": pd.to_numeric(nav)})
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame.empty:
        return {"return": None, "start_date": None, "end_date": None}
    end = frame.iloc[-1]
    if ytd:
        start_cut = pd.Timestamp(end["date"].year - 1, 12, 31)
        before = frame[frame["date"] <= start_cut]
        start = before.iloc[-1] if not before.empty else frame.iloc[0]
    elif bars is None:
        start = frame.iloc[0]
    else:
        idx = max(0, len(frame) - 1 - bars)
        start = frame.iloc[idx]
    start_nav = float(start["nav"])
    end_nav = float(end["nav"])
    ret = None if start_nav <= 0 else end_nav / start_nav - 1.0
    return {
        "return": ret,
        "start_date": str(pd.Timestamp(start["date"]).date()),
        "end_date": str(pd.Timestamp(end["date"]).date()),
    }


def rebase_to_nav(equity: pd.Series, *, base: float = NAV_BASE) -> pd.Series:
    first = float(equity.iloc[0])
    if first <= 0:
        raise ValueError("equity start must be positive")
    return equity.astype(float) / first * base


def max_drawdown_span(nav: pd.Series, dates: pd.Series) -> dict[str, Any]:
    equity = pd.to_numeric(nav)
    dd = equity / equity.cummax() - 1.0
    trough_i = int(dd.idxmin())
    peak_i = int(equity.loc[:trough_i].idxmax())
    return {
        "value": float(dd.min()),
        "peak_date": _iso(dates.loc[peak_i]),
        "trough_date": _iso(dates.loc[trough_i]),
    }


def live_position(daily: pd.DataFrame, *, n_units: int = 5) -> dict[str, Any]:
    last = daily.iloc[-1]
    if "units" in daily.columns:
        units = int(round(float(last["units"])))
    else:
        units = int(round(float(last["position"]) * n_units))
    if n_units == 5 and units in (0, 5) and "units" not in daily.columns:
        label = "满仓" if units == 5 else "空仓"
    elif units <= 0:
        label = "空仓"
    elif units >= n_units:
        label = "满仓"
    else:
        label = f"{units}/{n_units}"
    note = ""
    action = str(last.get("action", ""))
    if action == "schedule_entry":
        note = "今天收盘确认买入，明天开盘才会成交"
    elif action == "schedule_exit":
        note = "今天收盘确认卖出，明天开盘才会成交"
    return {
        "units": units,
        "n": n_units,
        "weight": units / n_units,
        "label": label,
        "note": note,
        "action": action,
    }


def ohlc_records(price: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for _, row in price.iterrows():
        rows.append(
            {
                "date": _iso(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    return rows


def overlay_nav_frame(price: pd.DataFrame, dates: pd.Series) -> pd.DataFrame:
    hold = buy_and_hold_equity(price, cost=0.0)
    hold["date"] = pd.to_datetime(hold["date"]).dt.normalize()
    hold["nav"] = rebase_to_nav(hold["equity"])
    wanted = pd.to_datetime(dates).dt.normalize()
    aligned = pd.DataFrame({"date": wanted}).merge(hold[["date", "nav"]], on="date", how="left")
    ohlc = price.copy()
    ohlc["date"] = pd.to_datetime(ohlc["date"]).dt.normalize()
    aligned = aligned.merge(
        ohlc[["date", "open", "high", "low", "close"]],
        on="date",
        how="left",
    )
    aligned["nav"] = aligned["nav"].ffill()
    return aligned


def strategy_nav_overlay(daily: pd.DataFrame, dates: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["date"]).dt.normalize(),
            "nav": rebase_to_nav(daily["equity"]),
        }
    )
    wanted = pd.to_datetime(dates).dt.normalize()
    aligned = pd.DataFrame({"date": wanted}).merge(frame, on="date", how="left")
    aligned["nav"] = aligned["nav"].ffill()
    for col in ("open", "high", "low", "close"):
        aligned[col] = float("nan")
    return aligned


def sector_etf_lookup() -> dict[str, Any]:
    return {item.spec.code: item for item in SECTOR_ETF_UNIVERSE}


def rotation_holdings(daily: pd.DataFrame) -> dict[str, Any]:
    if daily.empty:
        return {"as_of": None, "last_rebalance": None, "empty": True, "rows": []}
    last = daily.iloc[-1]
    codes = [code for code in str(last.get("held") or "").split(",") if code]
    traded = daily.copy()
    if "traded" in traded.columns:
        traded = traded[pd.to_numeric(traded["traded"], errors="coerce").fillna(0).eq(1)]
    else:
        traded = traded.iloc[0:0]
    last_change = _iso(traded.iloc[-1]["date"]) if not traded.empty else None
    weight = 1.0 / len(codes) if codes else 0.0
    lookup = sector_etf_lookup()
    rows = []
    for code in codes:
        item = lookup.get(code)
        rows.append(
            {
                "code": code,
                "name": item.spec.name if item else code,
                "theme": item.theme if item else "",
                "note": item.proxy_note if item else "",
                "weight": weight,
            }
        )
    return {
        "as_of": _iso(last["date"]),
        "last_rebalance": last_change,
        "empty": not rows,
        "rows": rows,
    }


def build_frame(
    *,
    etf: pd.DataFrame,
    amv: pd.DataFrame,
    emotion: pd.DataFrame,
    end: date,
) -> pd.DataFrame:
    signals = build_amv_signals(amv)
    aligned = align_index_with_amv(etf, signals, spec=ETF_SPEC)
    frame = aligned.merge(emotion[["date", "emotion"]], on="date", how="inner")
    frame = add_indicators(frame)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame[
        (frame["date"] >= pd.Timestamp(START)) & (frame["date"] <= pd.Timestamp(end))
    ]
    if frame.empty:
        raise RuntimeError("159915 与 0AMV/情绪无共同交易日")
    return frame.sort_values("date").reset_index(drop=True)


def sleeve_rule(emotion_exit: float) -> CombinedRule:
    return CombinedRule(
        name=f"emo{int(emotion_exit)}",
        entry_mode="amv",
        emotion_entry_max=None,
        j_entry_max=None,
        amv_entry_two_day=0.03,
        exit_mode="emotion",
        amv_exit_threshold=None,
        emotion_exit_min=float(emotion_exit),
        exit_ignore_if_above_ma=60,
        min_hold_days=0,
    )


def mean_official_sleeves(dailies: list[pd.DataFrame]) -> pd.DataFrame:
    base = dailies[0][["date"]].copy()
    base["equity"] = sum(frame["equity"] for frame in dailies) / len(dailies)
    base["position"] = sum(frame["position"] for frame in dailies) / len(dailies)
    base["units"] = sum(frame["position"] for frame in dailies)
    if "close" in dailies[0].columns:
        base["close"] = dailies[0]["close"]
        base["open"] = dailies[0]["open"]
    return base


def strategy_payload(
    *,
    key: str,
    name: str,
    name_en: str,
    rule_name: str,
    daily: pd.DataFrame,
    summary: dict[str, Any],
    overlays: dict[str, pd.DataFrame],
    overlay_meta: list[dict[str, str]],
    status: BuildStatus,
    methodology: dict[str, Any],
    observation_only: bool = False,
    n_units: int = 5,
    headline: str = "",
    default_benchmark: str = "sz159915",
    extra: dict[str, Any] | None = None,
    risk_notes: list[str] | None = None,
) -> dict[str, Any]:
    nav = rebase_to_nav(daily["equity"])
    dates = pd.to_datetime(daily["date"])
    series = []
    for i, row in daily.iterrows():
        item = {
            "date": _iso(row["date"]),
            "nav": float(nav.loc[i]),
            "equity": float(row["equity"]),
            "position": float(row["position"]) if "position" in row else float(row.get("units", 0)) / n_units,
        }
        if "units" in daily.columns:
            item["units"] = float(row["units"])
        series.append(item)
    overlay_json = {}
    yearly = {}
    strat_nav = pd.DataFrame({"date": dates, "nav": nav})
    for spec_key, frame in overlays.items():
        overlay_json[spec_key] = [
            {
                "date": _iso(row["date"]),
                "nav": None if pd.isna(row["nav"]) else float(row["nav"]),
                "open": None if pd.isna(row.get("open")) else float(row["open"]),
                "high": None if pd.isna(row.get("high")) else float(row["high"]),
                "low": None if pd.isna(row.get("low")) else float(row["low"]),
                "close": None if pd.isna(row.get("close")) else float(row["close"]),
            }
            for _, row in frame.iterrows()
        ]
        bench = frame.dropna(subset=["nav"])
        yearly[spec_key] = yearly_excess(strat_nav, bench)
    dd = max_drawdown_span(nav, dates)
    position = live_position(daily, n_units=n_units)
    payload = {
        "schema_version": 1,
        "data_product": "amv_research_index",
        "disclaimer": DISCLAIMER,
        "disclaimer_long": DISCLAIMER_LONG,
        "generated_at_utc": status.generated_at_utc,
        "index": {
            "key": key,
            "index_name_cn": name,
            "index_name_en": name_en,
            "rule": rule_name,
            "data_start_date": _iso(dates.iloc[0]),
            "data_end_date": _iso(dates.iloc[-1]),
            "base_date": _iso(dates.iloc[0]),
            "base_point": NAV_BASE,
            "observation_only": observation_only,
            "headline": headline,
        },
        "freshness": {
            "amv_as_of": status.amv_as_of,
            "amv_trusted_as_of": status.amv_trusted_as_of,
            "amv_last_close": status.amv_last_close,
            "amv_trusted_close": status.amv_trusted_close,
            "emotion_as_of": status.emotion_as_of,
            "overlay_as_of": status.overlay_as_of,
            "amv_stale": status.amv_stale,
            "emotion_stale": status.emotion_stale,
            "expected_update": "每个交易日晚上八点左右更新",
        },
        "banners": [],
        "performance": {
            "latest_point": float(nav.iloc[-1]),
            "annualized_return": float(summary["annualized_return"]),
            "sharpe": float(summary["sharpe"]),
            "exposure": float(summary["exposure"]),
            "max_drawdown": dd,
            "total_return": float(summary["total_return"]),
            "1d": period_return(nav, dates, bars=1),
            "5d": period_return(nav, dates, bars=5),
            "20d": period_return(nav, dates, bars=20),
            "ytd": period_return(nav, dates, ytd=True),
            "1y": period_return(nav, dates, bars=252),
            "since_inception": period_return(nav, dates),
        },
        "position": position,
        "overlays": overlay_meta,
        "overlay_series": overlay_json,
        "default_benchmark": default_benchmark,
        "yearly_excess": yearly,
        "series": series,
        "methodology": methodology,
        "risk_notes": list(risk_notes or DEFAULT_RISK_NOTES),
    }
    if extra:
        payload.update(extra)
    return payload


def build_rotation_json(
    *,
    clock_daily: pd.DataFrame,
    overlay_frames: dict[str, pd.DataFrame],
    status: BuildStatus,
    force_download: bool,
    price_end: date,
    strategy_end: date,
) -> dict[str, Any]:
    rot_banners: list[str] = []
    prices: dict[str, pd.DataFrame] = {}
    if "sz159915" in overlay_frames:
        prices["159915"] = overlay_frames["sz159915"]
    for item in SECTOR_ETF_UNIVERSE:
        if item.spec.code == "159915":
            continue
        frame = download_with_fallback(
            item.spec, start=START, end=price_end, force=force_download, banners=rot_banners
        )
        if frame is None or frame.empty:
            continue
        prices[item.spec.code] = frame
    if len([code for code in prices if code != "159915"]) < 2:
        rot_banners.append("行业 ETF 行情不够，轮动对照暂时画不出来。")
    result = rotate(prices, clock_daily, rule=ROT_RULE, cost=COST)
    rot_daily = result["daily"].copy()
    rot_daily["position"] = rot_daily["n_held"] / float(ROT_RULE.top_k)
    rot_daily["units"] = rot_daily["n_held"]
    rot_sum = summarize_backtest(
        rot_daily,
        pd.DataFrame(),
        cost=COST,
        code="sector-rotation",
        name="板块轮动",
        tencent_symbol="",
    )
    rot_overlays: dict[str, pd.DataFrame] = {
        "cyb-clock": strategy_nav_overlay(clock_daily, rot_daily["date"]),
    }
    rot_overlay_meta: list[dict[str, str]] = [
        {"key": "cyb-clock", "code": "", "name": "创业板满仓策略"},
    ]
    wanted = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz159915": "创业板ETF",
    }
    for key, name in wanted.items():
        if key not in overlay_frames:
            continue
        rot_overlays[key] = overlay_nav_frame(overlay_frames[key], rot_daily["date"])
        code = next((spec.code for spec in OVERLAYS if spec.tencent_symbol == key), "")
        rot_overlay_meta.append({"key": key, "code": code, "name": name})
    holdings = rotation_holdings(rot_daily)
    clock_nav = float(rebase_to_nav(clock_daily["equity"]).iloc[-1])
    rot_nav = float(rebase_to_nav(rot_daily["equity"]).iloc[-1])
    vs_clock = None if clock_nav <= 0 else rot_nav / clock_nav - 1.0
    method = {
        "怎么选": "创业板有仓的时候，按过去 120 个交易日涨幅，等权拿前两名行业 ETF，每月换一次。",
        "怎么成交": "按昨天收盘排名，今天开盘买。创业板空仓时，这边也不拿板块。",
        "备选 ETF": (
            "半导体、芯片、设备、通信、机器人、工业母机、军工、稀土、有色、人工智能等。"
            "有的只是近似：通信 ETF 不等于纯 CPO，人工智能 ETF 不等于纯液冷，"
            "军工 ETF 不等于纯商业航天，有色 ETF 不等于纯小金属。"
        ),
        "怎么看": "这是对照观察，日常仍看创业板那一页。按年检验下来，多数年份只略好一点点。",
        "费用": "每次换仓按单边千分之一计。",
        "窗口": f"{START.isoformat()} 到 {strategy_end.isoformat()}",
    }
    payload = strategy_payload(
        key="sector-rotation",
        name="板块轮动对照",
        name_en="Sector rotation overlay",
        rule_name="创业板有仓才选板块",
        daily=rot_daily,
        summary=rot_sum,
        overlays=rot_overlays,
        overlay_meta=rot_overlay_meta,
        status=status,
        methodology=method,
        observation_only=True,
        n_units=ROT_RULE.top_k,
        headline="这是对照观察，日常仍看创业板这一页",
        default_benchmark="cyb-clock",
        extra={
            "holdings": holdings,
            "hide_kline": True,
            "default_overlays": ["cyb-clock", "sh000001"],
            "page": "rotation",
        },
        risk_notes=[
            "有的 ETF 只是主题近似，不是纯板块。",
            "按年检验下来，多数年份只略好一点点，不适合当成日常主仓。",
            *DEFAULT_RISK_NOTES,
        ],
    )
    payload["performance"]["vs_cyb_clock"] = vs_clock
    payload["banners"] = rot_banners
    if holdings["empty"]:
        payload["position"]["label"] = "空仓"
        payload["position"]["note"] = "创业板有仓才选板块。"
    else:
        payload["position"]["label"] = "、".join(row["name"] for row in holdings["rows"])
        last_change = holdings.get("last_rebalance")
        payload["position"]["note"] = (
            f"最近一次换仓 {last_change}，两只等权。" if last_change else "两只等权。"
        )
    return payload


def copy_static_site(site_dir: Path = SITE_DIR) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    src = ROOT / "site"
    for name in ("index.html", "app.js", "styles.css", "README.md"):
        path = src / name
        if path.exists():
            continue
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")


def build_site(
    *,
    amv_close: float | None = None,
    amv_date: date | None = None,
    force_download: bool = True,
    out_dir: Path = SITE_DIR,
) -> dict[str, Any]:
    banners: list[str] = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed_cloud_amv_from_local()
    if amv_close is not None:
        as_of = amv_date or beijing_today()
        result = append_amv_close(as_of=as_of, close=float(amv_close), source="github_workflow")
        banners.append(
            f"已记下 0AMV {result['date']} 收盘 {result['close']:.2f}"
            + ("（和上一有效日一样，仓位不会变）" if result["duplicate_close"] else "")
        )
    amv = load_cloud_amv()
    trusted = trusted_amv_frame(amv)
    amv_last = pd.Timestamp(amv["date"].iloc[-1]).date()
    trusted_end = pd.Timestamp(trusted["date"].iloc[-1]).date()
    amv_last_close = float(amv["close"].iloc[-1])
    amv_trusted_close = float(trusted["close"].iloc[-1])
    dupes = duplicate_tail_dates(amv)
    if dupes:
        banners.append(
            f"{'、'.join(dupes)} 的 0AMV 收盘和前一天相同，不当新信号；"
            f"策略仍算到 {trusted_end.isoformat()}。"
        )
    emotion = try_refresh_emotion(as_of=beijing_today(), banners=banners)
    emotion_last = pd.Timestamp(emotion["date"].max()).date()
    overlay_frames: dict[str, pd.DataFrame] = {}
    overlay_as_of: date | None = None
    price_end = max(beijing_today(), amv_last)
    for spec in OVERLAYS:
        frame = download_with_fallback(
            spec, start=START, end=price_end, force=force_download, banners=banners
        )
        if frame is None or frame.empty:
            continue
        overlay_frames[spec.tencent_symbol] = frame
        last = pd.Timestamp(frame["date"].iloc[-1]).date()
        overlay_as_of = last if overlay_as_of is None else max(overlay_as_of, last)

    etf = overlay_frames.get("sz159915")
    if etf is None:
        raise RuntimeError("缺少创业板 ETF 159915 行情，无法生成站点")

    strategy_end = min(trusted_end, emotion_last, pd.Timestamp(etf["date"].iloc[-1]).date())
    amv_stale = overlay_as_of is not None and trusted_end < overlay_as_of
    emotion_stale = overlay_as_of is not None and emotion_last < overlay_as_of
    if amv_stale:
        banners.insert(
            0,
            f"今天的 0AMV 还没更新，信号仍按上一有效日 {trusted_end.isoformat()}。"
            "上证、深成这些对比线会继续刷到最新。",
        )
    if emotion_stale:
        banners.append(f"市场情绪数据停在 {emotion_last.isoformat()}，可能比行情慢一拍。")

    status = BuildStatus(
        banners=banners,
        amv_as_of=amv_last.isoformat(),
        amv_trusted_as_of=trusted_end.isoformat(),
        amv_last_close=amv_last_close,
        amv_trusted_close=amv_trusted_close,
        emotion_as_of=emotion_last.isoformat(),
        overlay_as_of=overlay_as_of.isoformat() if overlay_as_of else None,
        amv_stale=amv_stale,
        emotion_stale=emotion_stale,
        generated_at_utc=generated_at,
    )

    raw = build_frame(etf=etf, amv=amv, emotion=emotion, end=strategy_end)
    clock_frame = apply_combined_rule(raw, CLOCK_RULE)
    clock_daily, clock_trades, _bh, clock_sum = run_index_backtest(
        clock_frame, cost=COST, force_eod_exit=False
    )
    clock_sum = summarize_backtest(
        clock_daily,
        clock_trades,
        cost=COST,
        code="159915",
        name="创业板ETF",
        tencent_symbol="sz159915",
        benchmark_daily=buy_and_hold_equity(clock_frame, cost=COST),
    )

    sleeve_dailies = []
    for emo in EMOTION_SLEEVES:
        gated = apply_combined_rule(raw, sleeve_rule(emo))
        daily, _tr, _bh, _s = run_index_backtest(gated, cost=COST, force_eod_exit=False)
        sleeve_dailies.append(daily)
    committee = mean_official_sleeves(sleeve_dailies)
    committee_sum = summarize_backtest(
        committee.assign(position=committee["position"].clip(0, 1)),
        pd.DataFrame(),
        cost=COST,
        code="159915",
        name="五份离场委员会",
        tencent_symbol="sz159915",
        benchmark_daily=buy_and_hold_equity(clock_frame, cost=COST),
    )
    committee_sum["exposure"] = float(committee["position"].mean())

    overlay_meta = [
        {"key": spec.tencent_symbol, "code": spec.code, "name": spec.name}
        for spec in OVERLAYS
        if spec.tencent_symbol in overlay_frames
    ]
    overlay_aligned: dict[str, pd.DataFrame] = {}
    for spec in OVERLAYS:
        if spec.tencent_symbol not in overlay_frames:
            continue
        overlay_aligned[spec.tencent_symbol] = overlay_nav_frame(
            overlay_frames[spec.tencent_symbol], clock_daily["date"]
        )

    clock_method = {
        "标的": "创业板 ETF（159915）",
        "怎么进出": "0AMV 两日涨幅加起来超过 3% 就进；市场情绪到 70 就出。如果价格还在 60 日均线上方，这次离场先不算。",
        "仓位": "要么满仓，要么空仓，不慢慢加。",
        "成交": "当天收盘确认，第二天开盘成交。",
        "费用": "单边千分之一。",
        "窗口": f"{START.isoformat()} 到 {strategy_end.isoformat()}",
        "说明": DISCLAIMER_LONG,
    }
    committee_method = {
        "标的": "创业板 ETF（159915）",
        "怎么做": "五份仓一起看：进场都看 0AMV，离场线分别是情绪 50、55、60、65、70，最后取平均仓位。",
        "怎么看": "只作观察，日常仍看满仓那一页。",
        "费用": "单边千分之一。",
        "窗口": f"{START.isoformat()} 到 {strategy_end.isoformat()}",
        "说明": DISCLAIMER_LONG,
    }

    clock_json = strategy_payload(
        key="cyb-clock",
        name="创业板满仓",
        name_en="ChiNext full-lot",
        rule_name="满仓进出",
        daily=clock_daily,
        summary=clock_sum,
        overlays=overlay_aligned,
        overlay_meta=overlay_meta,
        status=status,
        methodology=clock_method,
        n_units=5,
        headline="日常跟仓看这一页",
    )
    committee_json = strategy_payload(
        key="cyb-committee",
        name="五份仓观察",
        name_en="Five-sleeve overlay",
        rule_name="五份仓",
        daily=committee,
        summary=committee_sum,
        overlays=overlay_aligned,
        overlay_meta=overlay_meta,
        status=status,
        methodology=committee_method,
        observation_only=True,
        n_units=5,
        headline="只作观察，日常仍看满仓那一页",
    )

    etf_ohlc = ohlc_records(
        etf[
            (pd.to_datetime(etf["date"]) >= pd.Timestamp(START))
            & (pd.to_datetime(etf["date"]) <= pd.Timestamp(strategy_end))
        ]
    )
    clock_json["etf_ohlc"] = etf_ohlc
    committee_json["etf_ohlc"] = etf_ohlc

    rotation_json = build_rotation_json(
        clock_daily=clock_daily,
        overlay_frames=overlay_frames,
        status=status,
        force_download=force_download,
        price_end=price_end,
        strategy_end=strategy_end,
    )
    flow_json = refresh_theme_flow(allow_network=force_download)

    catalog = {
        "schema_version": 1,
        "data_product": "amv_research_catalog",
        "generated_at_utc": generated_at,
        "language": ["zh-CN"],
        "disclaimer": DISCLAIMER,
        "disclaimer_long": DISCLAIMER_LONG,
        "site_name": "创业板研究观察",
        "github_repo": github_repo(),
        "pages_url": pages_url(),
        "expected_update": "每个交易日晚上八点左右",
        "amv_phone_workflow": (
            "在创业板这一页点右上角「录入 0AMV」，填收盘价后用仓库所有者的 GitHub 账号提交；"
            "大约一两分钟后刷新网页就能看到。"
        ),
        "banners": banners,
        "freshness": {
            "amv_as_of": status.amv_as_of,
            "amv_trusted_as_of": status.amv_trusted_as_of,
            "amv_last_close": status.amv_last_close,
            "amv_trusted_close": status.amv_trusted_close,
            "emotion_as_of": status.emotion_as_of,
            "overlay_as_of": status.overlay_as_of,
            "strategy_end": strategy_end.isoformat(),
            "amv_stale": amv_stale,
            "emotion_stale": emotion_stale,
        },
        "pages": [
            {"id": "cyb", "tab": "创业板指数策略"},
            {
                "id": "rotation",
                "tab": "板块轮动策略",
                "agent_url": "data/agent/sector-rotation.json",
            },
            {
                "id": "flow",
                "tab": "板块资金流入",
                "agent_url": "data/agent/theme-fund-flow.json",
            },
        ],
        "indices": [
            {
                "key": "cyb-clock",
                "tab": "满仓",
                "index_name_cn": "创业板满仓",
                "rule": "满仓进出",
                "role": "live",
                "agent_url": "data/agent/cyb-clock.json",
                "full_series_url": "data/cyb-clock.json",
            },
            {
                "key": "cyb-committee",
                "tab": "五份仓",
                "index_name_cn": "五份仓观察",
                "rule": "五份仓",
                "role": "observation",
                "agent_url": "data/agent/cyb-committee.json",
                "full_series_url": "data/cyb-committee.json",
            },
        ],
    }
    compare = {
        "schema_version": 1,
        "generated_at_utc": generated_at,
        "disclaimer": DISCLAIMER,
        "as_of": strategy_end.isoformat(),
        "indices": [
            {
                "key": "cyb-clock",
                "name": "创业板满仓",
                "latest_point": clock_json["performance"]["latest_point"],
                "annualized_return": clock_json["performance"]["annualized_return"],
                "max_drawdown": clock_json["performance"]["max_drawdown"]["value"],
                "sharpe": clock_json["performance"]["sharpe"],
                "exposure": clock_json["performance"]["exposure"],
                "position": clock_json["position"]["label"],
            },
            {
                "key": "cyb-committee",
                "name": "五份仓观察",
                "latest_point": committee_json["performance"]["latest_point"],
                "annualized_return": committee_json["performance"]["annualized_return"],
                "max_drawdown": committee_json["performance"]["max_drawdown"]["value"],
                "sharpe": committee_json["performance"]["sharpe"],
                "exposure": committee_json["performance"]["exposure"],
                "position": committee_json["position"]["label"],
            },
            {
                "key": "sector-rotation",
                "name": "板块轮动对照",
                "latest_point": rotation_json["performance"]["latest_point"],
                "annualized_return": rotation_json["performance"]["annualized_return"],
                "max_drawdown": rotation_json["performance"]["max_drawdown"]["value"],
                "sharpe": rotation_json["performance"]["sharpe"],
                "exposure": rotation_json["performance"]["exposure"],
                "position": rotation_json["position"]["label"],
            },
        ],
    }

    data_dir = Path(out_dir) / "data"
    agent_dir = data_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(agent_dir / "catalog.json", catalog)
    _json_dump(agent_dir / "compare.json", compare)
    _json_dump(agent_dir / "cyb-clock.json", clock_json)
    _json_dump(agent_dir / "cyb-committee.json", committee_json)
    _json_dump(agent_dir / "sector-rotation.json", rotation_json)
    _json_dump(agent_dir / "theme-fund-flow.json", flow_json)
    _json_dump(data_dir / "cyb-clock.json", clock_json)
    _json_dump(data_dir / "cyb-committee.json", committee_json)
    _json_dump(data_dir / "sector-rotation.json", rotation_json)
    copy_static_site(Path(out_dir))
    (Path(out_dir) / ".nojekyll").write_text("", encoding="utf-8")
    return {
        "strategy_end": strategy_end.isoformat(),
        "clock_nav": clock_json["performance"]["latest_point"],
        "committee_nav": committee_json["performance"]["latest_point"],
        "rotation_nav": rotation_json["performance"]["latest_point"],
        "clock_position": clock_json["position"]["label"],
        "committee_position": committee_json["position"]["label"],
        "rotation_position": rotation_json["position"]["label"],
        "flow_as_of": flow_json.get("as_of"),
        "banners": banners,
        "out_dir": str(out_dir),
    }
