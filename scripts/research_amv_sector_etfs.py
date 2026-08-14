"""Download theme ETF daily bars via Tencent and run 0AMV+emotion research."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_index_backtest import run_index_backtest  # noqa: E402
from ai_invest_advisor.amv_index_data import (  # noqa: E402
    DEFAULT_AMV_PATH,
    align_index_with_amv,
    build_amv_signals,
    download_index_daily,
    load_amv_daily,
)
from ai_invest_advisor.cyb_emotion_amv_combo import (  # noqa: E402
    CombinedRule,
    apply_combined_rule,
    load_emotion_frame,
)
from ai_invest_advisor.cyb_market_data import add_indicators  # noqa: E402
from ai_invest_advisor.sector_etf_universe import SECTOR_ETF_UNIVERSE  # noqa: E402

OUT = ROOT / "reports" / "backtests" / "amv_sector_etf"
CACHE = ROOT / "data" / "backtests" / "amv_index_gate"
UNIVERSE_DIR = ROOT / "data" / "sector_etfs"
COST = 0.001
START = date(2020, 1, 2)
PRICE_END = date(2026, 8, 14)
NEXT_LONG_ACTIONS = {"schedule_entry", "hold", "entry", "hold_min_hold"}
RULE = CombinedRule(
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


def trusted_amv_last() -> date:
    """Drop trailing 0AMV bars whose close was copied from the prior day."""
    amv = load_amv_daily(DEFAULT_AMV_PATH).sort_values("date")
    while len(amv) >= 2 and float(amv["close"].iloc[-1]) == float(amv["close"].iloc[-2]):
        amv = amv.iloc[:-1]
    return pd.Timestamp(amv["date"].iloc[-1]).date()


def research_end() -> date:
    emotion_last = pd.Timestamp(load_emotion_frame()["date"].max()).date()
    return min(emotion_last, trusted_amv_last(), PRICE_END)


def build_frame(spec, *, force: bool, end: date) -> pd.DataFrame:
    price = download_index_daily(
        spec, start=START, end=PRICE_END, cache_dir=CACHE, force=force
    )
    amv = build_amv_signals(load_amv_daily(DEFAULT_AMV_PATH))
    aligned = align_index_with_amv(price, amv, spec=spec)
    emotion = load_emotion_frame()
    frame = aligned.merge(emotion[["date", "emotion"]], on="date", how="inner")
    frame = add_indicators(frame)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame[(frame["date"] >= pd.Timestamp(START)) & (frame["date"] <= pd.Timestamp(end))]
    if frame.empty:
        raise RuntimeError(f"{spec.code} has no overlap with 0AMV+emotion")
    return frame.sort_values("date").reset_index(drop=True)


def eval_one(item, frame: pd.DataFrame) -> dict:
    gated = apply_combined_rule(frame, RULE)
    daily, trades, bench, summary = run_index_backtest(gated, cost=COST)
    excess = float(summary["total_return"]) - float(summary["benchmark_total_return"])
    daily.to_csv(OUT / f"{item.spec.code}_gated_daily.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUT / f"{item.spec.code}_gated_trades.csv", index=False, encoding="utf-8-sig")
    return {
        "code": item.spec.code,
        "name": item.spec.name,
        "theme": item.theme,
        "proxy_note": item.proxy_note,
        "tencent_symbol": item.spec.tencent_symbol,
        "start": summary["start"],
        "end": summary["end"],
        "bars": int(summary["bars"]),
        "gated_return": float(summary["total_return"]),
        "hold_return": float(summary["benchmark_total_return"]),
        "excess": excess,
        "gated_dd": float(summary["max_drawdown"]),
        "hold_dd": float(summary.get("benchmark_max_drawdown", float("nan"))),
        "sharpe": float(summary["sharpe"]),
        "exposure": float(summary["exposure"]),
        "trades": int(summary["trades"]),
        "annualized": float(summary["annualized_return"]),
        "last_close": float(frame["close"].iloc[-1]),
        "last_date": str(pd.Timestamp(frame["date"].iloc[-1]).date()),
        "first_date": str(pd.Timestamp(frame["date"].iloc[0]).date()),
    }


def _want_long_next(action: str) -> bool:
    return str(action) in NEXT_LONG_ACTIONS


def rotation(
    prices: dict[str, pd.DataFrame],
    gate_daily: pd.DataFrame,
    *,
    use_gate: bool,
    lookback: int = 20,
    frequency: str = "D",
) -> dict:
    """Expanding-universe top-1 20d momentum; optional ChiNext 0AMV risk-on gate.

    frequency='D' re-ranks every session (noisy). frequency='M' freezes the
    leader at month-end, which matches common GitHub A-share ETF rotation code.
    """
    calendar = gate_daily[["date", "action", "position"]].copy()
    calendar["date"] = pd.to_datetime(calendar["date"]).dt.normalize()
    calendar = calendar.sort_values("date").reset_index(drop=True)
    codes = [c for c in prices if c != "159915"]
    close_map: dict[str, pd.Series] = {}
    open_map: dict[str, pd.Series] = {}
    for code in codes:
        part = prices[code][["date", "open", "close"]].copy()
        part["date"] = pd.to_datetime(part["date"]).dt.normalize()
        close_map[code] = part.set_index("date")["close"]
        open_map[code] = part.set_index("date")["open"]

    equity = 1.0
    held = None
    month_key = None
    month_pick = None
    rows = []
    trades = 0
    dates = calendar["date"].tolist()
    for i, row in calendar.iterrows():
        dt = row["date"]
        if i == 0:
            rows.append({"date": dt, "equity": equity, "held": None})
            continue
        prev = calendar.iloc[i - 1]
        prev_dt = prev["date"]
        want_long = True if not use_gate else _want_long_next(prev["action"])
        scores = {}
        for code in codes:
            series = close_map[code]
            if prev_dt not in series.index:
                continue
            loc = series.index.get_loc(prev_dt)
            if not isinstance(loc, int) or loc < lookback:
                continue
            past = series.iloc[loc - lookback]
            now = series.iloc[loc]
            if pd.isna(past) or pd.isna(now) or past <= 0:
                continue
            scores[code] = float(now / past - 1.0)
        ranked = max(scores, key=scores.get) if scores else None
        if frequency == "M":
            ym = str(pd.Timestamp(dt).to_period("M"))
            if month_key != ym:
                month_pick = ranked
                month_key = ym
            pick = month_pick if want_long else None
        else:
            pick = ranked if want_long else None

        if held and (not want_long or pick != held):
            if dt in open_map[held].index and prev_dt in close_map[held].index:
                equity *= float(open_map[held].loc[dt]) / float(close_map[held].loc[prev_dt])
            equity *= 1.0 - COST
            held = None
            trades += 1
        if want_long and pick and held is None:
            if dt not in open_map[pick].index or dt not in close_map[pick].index:
                rows.append({"date": dt, "equity": equity, "held": None})
                continue
            equity *= 1.0 - COST
            held = pick
            equity *= float(close_map[held].loc[dt]) / float(open_map[held].loc[dt])
            trades += 1
        elif held:
            if dt in close_map[held].index and prev_dt in close_map[held].index:
                equity *= float(close_map[held].loc[dt]) / float(close_map[held].loc[prev_dt])
        rows.append({"date": dt, "equity": equity, "held": held})

    daily = pd.DataFrame(rows)
    eq = daily["equity"]
    dd = float((eq / eq.cummax() - 1.0).min())
    last_held = daily["held"].iloc[-1]
    held_counts = (
        daily["held"].dropna().value_counts(normalize=True).head(8).to_dict()
        if daily["held"].notna().any()
        else {}
    )
    return {
        "total_return": float(eq.iloc[-1] - 1.0),
        "max_drawdown": dd,
        "trades": trades,
        "last_held": None if pd.isna(last_held) else last_held,
        "held_share": {str(k): float(v) for k, v in held_counts.items()},
        "daily": daily,
        "n_dates": int(len(dates)),
    }


def monthly_last(daily: pd.DataFrame, value_col: str = "equity") -> tuple[list[str], list[float]]:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["ym"] = frame["date"].dt.to_period("M").astype(str)
    last = frame.groupby("ym", sort=True).tail(1)
    return last["ym"].tolist(), [round(float(x), 4) for x in last[value_col].tolist()]


def write_report(payload: dict, table: pd.DataFrame) -> None:
    lines = [
        "# 0AMV 行业 ETF 轮动研究",
        "",
        f"- 生成时间: {payload['generated_at']}",
        f"- 规则: `{payload['rule']['name']}`（两日和 >3% 入场；情绪 ≥70 离场；标的收盘在 MA60 上方忽略离场）",
        f"- 窗口: {payload['start']} 至 {payload['end']}（0AMV 有效收盘截断；情绪可更长）",
        f"- 成本: 单边 {payload['cost']*10000:.0f}bp，收盘确认、次日开盘成交",
        f"- 行情: 腾讯 fqkline 前复权；0AMV: Compass；情绪: 本地全A宽度",
        "",
        "## 单品种：同一 0AMV 规则 vs 持有",
        "",
        "| 代码 | 名称 | 主题 | 起点 | 持有 | 门控 | 超额 | 门控回撤 | 交易 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in table.to_dict(orient="records"):
        lines.append(
            f"| {row['code']} | {row['name']} | {row['theme']} | {row['first_date']} | "
            f"{row['hold_return']*100:.1f}% | {row['gated_return']*100:.1f}% | "
            f"{row['excess']*100:.1f}ppt | {row['gated_dd']*100:.1f}% | {row['trades']} |"
        )
    rot_g = payload["rotation_gated"]
    rot_a = payload["rotation_always"]
    rot_mg = payload["rotation_monthly_gated"]
    rot_ma = payload["rotation_monthly_always"]
    cyb = payload["cyb_gated"]
    lines += [
        "",
        "## 轮动（创业板 0AMV 门控 = 风险开关）",
        "",
        f"- 日频 Top1：开门持有 20 日最强 1 只。累计 **{rot_g['total_return']*100:.1f}%**，回撤 **{rot_g['max_drawdown']*100:.1f}%**，换仓 {rot_g['trades']} 次。",
        f"- 月频 Top1：月初按上月末 20 日动量定仓。累计 **{rot_mg['total_return']*100:.1f}%**，回撤 **{rot_mg['max_drawdown']*100:.1f}%**，换仓 {rot_mg['trades']} 次。",
        f"- 日频、无门控：累计 **{rot_a['total_return']*100:.1f}%**，回撤 **{rot_a['max_drawdown']*100:.1f}%**。",
        f"- 月频、无门控：累计 **{rot_ma['total_return']*100:.1f}%**，回撤 **{rot_ma['max_drawdown']*100:.1f}%**。",
        f"- 对照：创业板 ETF 同一规则单独门控。累计 **{cyb['total_return']*100:.1f}%**。",
        "",
        "PCB / 液冷没有足够流动性的独立 ETF，分别用通信、人工智能 ETF 作代理，不能当成纯板块。",
        "",
        "研究支持，不是投资建议。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)

    universe_rows = [
        {
            "code": item.spec.code,
            "name": item.spec.name,
            "tencent_symbol": item.spec.tencent_symbol,
            "theme": item.theme,
            "proxy_note": item.proxy_note,
        }
        for item in SECTOR_ETF_UNIVERSE
    ]
    pd.DataFrame(universe_rows).to_csv(
        UNIVERSE_DIR / "universe.csv", index=False, encoding="utf-8-sig"
    )

    rows = []
    frames: dict[str, pd.DataFrame] = {}
    end = research_end()
    print("research window", START, "->", end, "amv_last", trusted_amv_last(), flush=True)
    for item in SECTOR_ETF_UNIVERSE:
        print("eval", item.spec.code, item.spec.name, flush=True)
        frame = build_frame(item.spec, force=args.force_download, end=end)
        frames[item.spec.code] = frame
        row = eval_one(item, frame)
        rows.append(row)
        print(
            f"  {row['first_date']}->{row['last_date']} hold {row['hold_return']*100:.1f}% "
            f"gated {row['gated_return']*100:.1f}% ex {row['excess']*100:.1f}ppt "
            f"dd {row['gated_dd']*100:.1f}%",
            flush=True,
        )

    table = pd.DataFrame(rows).sort_values("excess", ascending=False)
    table.to_csv(OUT / "per_etf.csv", index=False, encoding="utf-8-sig")

    cyb_daily = pd.read_csv(OUT / "159915_gated_daily.csv", parse_dates=["date"])
    rot_gated = rotation(frames, cyb_daily, use_gate=True, frequency="D")
    rot_always = rotation(frames, cyb_daily, use_gate=False, frequency="D")
    rot_month_g = rotation(frames, cyb_daily, use_gate=True, frequency="M")
    rot_month_a = rotation(frames, cyb_daily, use_gate=False, frequency="M")
    rot_gated["daily"].to_csv(OUT / "rotation_gated_daily.csv", index=False, encoding="utf-8-sig")
    rot_always["daily"].to_csv(OUT / "rotation_always_daily.csv", index=False, encoding="utf-8-sig")
    rot_month_g["daily"].to_csv(OUT / "rotation_monthly_gated_daily.csv", index=False, encoding="utf-8-sig")
    rot_month_a["daily"].to_csv(OUT / "rotation_monthly_always_daily.csv", index=False, encoding="utf-8-sig")

    cats, cyb_eq = monthly_last(cyb_daily)
    _, rot_g_eq = monthly_last(rot_gated["daily"])
    _, rot_a_eq = monthly_last(rot_always["daily"])
    _, rot_mg_eq = monthly_last(rot_month_g["daily"])
    _, rot_ma_eq = monthly_last(rot_month_a["daily"])
    hold_cyb = frames["159915"].copy()
    hold_cyb["equity"] = hold_cyb["close"] / float(hold_cyb["close"].iloc[0])
    _, hold_eq = monthly_last(hold_cyb)

    cyb_row = next(r for r in rows if r["code"] == "159915")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rule": RULE.to_dict(),
        "cost": COST,
        "start": str(START),
        "end": str(end),
        "source": "Tencent fqkline qfq + Compass 0AMV + local all-A emotion",
        "per_etf": table.to_dict(orient="records"),
        "rotation_gated": {
            "total_return": rot_gated["total_return"],
            "max_drawdown": rot_gated["max_drawdown"],
            "trades": rot_gated["trades"],
            "last_held": rot_gated["last_held"],
            "held_share": rot_gated["held_share"],
        },
        "rotation_always": {
            "total_return": rot_always["total_return"],
            "max_drawdown": rot_always["max_drawdown"],
            "trades": rot_always["trades"],
            "last_held": rot_always["last_held"],
            "held_share": rot_always["held_share"],
        },
        "rotation_monthly_gated": {
            "total_return": rot_month_g["total_return"],
            "max_drawdown": rot_month_g["max_drawdown"],
            "trades": rot_month_g["trades"],
            "last_held": rot_month_g["last_held"],
            "held_share": rot_month_g["held_share"],
        },
        "rotation_monthly_always": {
            "total_return": rot_month_a["total_return"],
            "max_drawdown": rot_month_a["max_drawdown"],
            "trades": rot_month_a["trades"],
            "last_held": rot_month_a["last_held"],
            "held_share": rot_month_a["held_share"],
        },
        "cyb_gated": {
            "total_return": cyb_row["gated_return"],
            "hold_return": cyb_row["hold_return"],
            "excess": cyb_row["excess"],
            "max_drawdown": cyb_row["gated_dd"],
        },
        "monthly": {
            "categories": cats,
            "cyb_gated": cyb_eq,
            "rotation_gated": rot_g_eq,
            "rotation_always": rot_a_eq,
            "rotation_monthly_gated": rot_mg_eq,
            "rotation_monthly_always": rot_ma_eq,
            "cyb_hold": hold_eq,
        },
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
    )
    write_report(payload, table)
    print(
        "rotation gated",
        round(rot_gated["total_return"] * 100, 1),
        "dd",
        round(rot_gated["max_drawdown"] * 100, 1),
        "trades",
        rot_gated["trades"],
        "last",
        rot_gated["last_held"],
        flush=True,
    )
    print(
        "rotation always",
        round(rot_always["total_return"] * 100, 1),
        "dd",
        round(rot_always["max_drawdown"] * 100, 1),
        "trades",
        rot_always["trades"],
        flush=True,
    )
    print(
        "rotation monthly gated",
        round(rot_month_g["total_return"] * 100, 1),
        "dd",
        round(rot_month_g["max_drawdown"] * 100, 1),
        "trades",
        rot_month_g["trades"],
        "last",
        rot_month_g["last_held"],
        flush=True,
    )
    print(
        "rotation monthly always",
        round(rot_month_a["total_return"] * 100, 1),
        "dd",
        round(rot_month_a["max_drawdown"] * 100, 1),
        "trades",
        rot_month_a["trades"],
        flush=True,
    )
    print(table[["code", "name", "theme", "hold_return", "gated_return", "excess"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
