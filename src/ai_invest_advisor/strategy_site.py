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
    "本站所有净值、仓位、超额与图表仅供研究观察，不构成投资建议、"
    "收益承诺或交易指令。不代客下单，不索取券商账号。"
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
        banners.append("情绪数据已尝试用同花顺涨跌分布刷新。")
    except Exception as exc:
        banners.append(f"同花顺情绪刷新失败，沿用仓库内 combined CSV：{exc}")
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
        note = "已发出入场信号，次日开盘成交"
    elif action == "schedule_exit":
        note = "已发出离场信号，次日开盘成交"
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
    return {
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
            "expected_update": "每个交易日北京时间 20:00 左右（GitHub Actions 12:00 UTC）",
        },
        "banners": status.banners,
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
        "default_benchmark": "sz159915",
        "yearly_excess": yearly,
        "series": series,
        "methodology": methodology,
        "risk_notes": [
            "历史回测含手续费假设（单边 10bp），不代表未来可实现收益。",
            "0AMV 无法在 GitHub 自动计算，需手机 Actions 手工粘贴收盘价。",
            "收盘确认、次日开盘成交；样本末日持仓按收盘盯市。",
            DISCLAIMER,
        ],
    }


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
            f"已写入 0AMV {result['date']} close={result['close']:.2f}"
            + ("（与上一有效日收盘相同，不作为新信号）" if result["duplicate_close"] else "")
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
            f"{'、'.join(dupes)} 的 0AMV 收盘与前一日相同，不作为有效信号；"
            f"策略序列截至 {trusted_end.isoformat()}。"
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
            f"今日 0AMV 未更新，信号沿用上一有效日 {trusted_end.isoformat()}。"
            "指数/ETF 对比层仍刷新到各自最新日期。",
        )
    if emotion_stale:
        banners.append(
            f"情绪序列截至 {emotion_last.isoformat()}，可能落后于行情。"
        )

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
        "underlying": "创业板 ETF 159915",
        "rule": "amv_emo70_ma60",
        "entry": "0AMV 两日涨跌幅之和 > 3%，收盘确认、次日开盘满仓买入",
        "exit": "市场情绪 ≥ 70 离场；若标的收盘价在 MA60 上方则忽略该次离场",
        "position": "二元满仓/空仓，不慢加仓",
        "cost": "单边 10bp",
        "window": f"{START.isoformat()} 至 {strategy_end.isoformat()}",
        "interpretation": DISCLAIMER_LONG,
    }
    committee_method = {
        "underlying": "创业板 ETF 159915",
        "rule": "五份独立袖，入场同 0AMV，离场线 50/55/60/65/70，等权平均",
        "note": "观察仓位，不替代满仓时钟。日频/月频行业轮动不作为替代方案推广。",
        "cost": "单边 10bp（按成交份额计）",
        "window": f"{START.isoformat()} 至 {strategy_end.isoformat()}",
        "interpretation": DISCLAIMER_LONG,
    }

    clock_json = strategy_payload(
        key="cyb-clock",
        name="创业板 0AMV 满仓",
        name_en="ChiNext 0AMV Full-Lot Clock",
        rule_name="amv_emo70_ma60",
        daily=clock_daily,
        summary=clock_sum,
        overlays=overlay_aligned,
        overlay_meta=overlay_meta,
        status=status,
        methodology=clock_method,
        n_units=5,
    )
    committee_json = strategy_payload(
        key="cyb-committee",
        name="五份离场委员会",
        name_en="Five-Tranche Emotion Committee",
        rule_name="sleeves_emo_50_70",
        daily=committee,
        summary=committee_sum,
        overlays=overlay_aligned,
        overlay_meta=overlay_meta,
        status=status,
        methodology=committee_method,
        observation_only=True,
        n_units=5,
    )

    etf_ohlc = ohlc_records(
        etf[
            (pd.to_datetime(etf["date"]) >= pd.Timestamp(START))
            & (pd.to_datetime(etf["date"]) <= pd.Timestamp(strategy_end))
        ]
    )
    clock_json["etf_ohlc"] = etf_ohlc
    committee_json["etf_ohlc"] = etf_ohlc

    catalog = {
        "schema_version": 1,
        "data_product": "amv_research_catalog",
        "generated_at_utc": generated_at,
        "language": ["zh-CN"],
        "disclaimer": DISCLAIMER,
        "disclaimer_long": DISCLAIMER_LONG,
        "site_name": "创业板 0AMV 研究时钟",
        "github_repo": github_repo(),
        "pages_url": pages_url(),
        "expected_update": "每个交易日北京时间 20:00 左右",
        "amv_phone_workflow": (
            "点页面右上角「录入 0AMV」，填收盘价后用仓库所有者 GitHub 账号提交 Issue；"
            "Actions 会写入并更新网页。"
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
        "indices": [
            {
                "key": "cyb-clock",
                "tab": "创业板 0AMV 满仓",
                "index_name_cn": "创业板 0AMV 满仓",
                "rule": "amv_emo70_ma60",
                "role": "live_clock",
                "agent_url": "data/agent/cyb-clock.json",
                "full_series_url": "data/cyb-clock.json",
            },
            {
                "key": "cyb-committee",
                "tab": "五份离场委员会",
                "index_name_cn": "五份离场委员会",
                "rule": "sleeves_emo_50_70",
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
                "name": "创业板 0AMV 满仓",
                "latest_point": clock_json["performance"]["latest_point"],
                "annualized_return": clock_json["performance"]["annualized_return"],
                "max_drawdown": clock_json["performance"]["max_drawdown"]["value"],
                "sharpe": clock_json["performance"]["sharpe"],
                "exposure": clock_json["performance"]["exposure"],
                "position": clock_json["position"]["label"],
            },
            {
                "key": "cyb-committee",
                "name": "五份离场委员会",
                "latest_point": committee_json["performance"]["latest_point"],
                "annualized_return": committee_json["performance"]["annualized_return"],
                "max_drawdown": committee_json["performance"]["max_drawdown"]["value"],
                "sharpe": committee_json["performance"]["sharpe"],
                "exposure": committee_json["performance"]["exposure"],
                "position": committee_json["position"]["label"],
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
    _json_dump(data_dir / "cyb-clock.json", clock_json)
    _json_dump(data_dir / "cyb-committee.json", committee_json)
    copy_static_site(Path(out_dir))
    (Path(out_dir) / ".nojekyll").write_text("", encoding="utf-8")
    return {
        "strategy_end": strategy_end.isoformat(),
        "clock_nav": clock_json["performance"]["latest_point"],
        "committee_nav": committee_json["performance"]["latest_point"],
        "clock_position": clock_json["position"]["label"],
        "committee_position": committee_json["position"]["label"],
        "banners": banners,
        "out_dir": str(out_dir),
    }
