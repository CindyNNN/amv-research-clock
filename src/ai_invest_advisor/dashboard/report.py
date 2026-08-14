from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ai_invest_advisor.dashboard.advice import build_daily_advice
from ai_invest_advisor.dashboard.pipeline import DASHBOARD_CACHE_DIR

DISCLAIMER = "This is research support, not financial advice."


def _table(frame: pd.DataFrame, columns: list[str], limit: int = 10) -> str:
    if frame.empty:
        return "暂无数据。"
    visible = frame[columns].head(limit).copy()
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = ["| " + " | ".join(str(value) for value in row) + " |" for row in visible.to_numpy()]
    return "\n".join([header, divider, *rows])


def generate_daily_report(
    cache_dir: Path = DASHBOARD_CACHE_DIR,
    report_dir: Path = Path("reports/daily"),
    report_date: str | None = None,
) -> Path:
    scores = pd.read_csv(cache_dir / "tech_board_scores.csv")
    sentiment = pd.read_csv(cache_dir / "sentiment_history.csv")
    latest = sentiment.iloc[-1]
    heat = float(latest["market_heat"])
    advice = build_daily_advice(heat, scores)
    report_date = report_date or str(latest["date"])
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report_date}-tech-dashboard.md"

    top_positive = scores[scores["advice_label"] == "积极观察"].sort_values("score", ascending=False)
    wait_list = scores[scores["advice_label"] == "等待回调"].sort_values("score", ascending=False)
    avoid_list = scores[scores["advice_label"] == "谨慎回避"].sort_values("score", ascending=True)
    content = f"""# 科技板块投资 Dashboard 日报

生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
交易日：{report_date}

## 市场情绪

- Market Heat：{heat:.2f}
- 状态：{latest["label"]}
- 今日建议：{advice.stance}
- 仓位倾向：{advice.allocation_hint}

## 积极观察

{_table(top_positive, ["board_name", "theme", "score", "net_amount", "leader", "leader_pct_change"], 8)}

## 等待回调

{_table(wait_list, ["board_name", "theme", "score", "ret5", "risk_flags"], 8)}

## 谨慎回避

{_table(avoid_list, ["board_name", "theme", "score", "net_amount", "risk_flags"], 8)}

## 风险提示

- {'；'.join(advice.risk_notes)}
- {DISCLAIMER}
"""
    path.write_text(content, encoding="utf-8")
    return path
