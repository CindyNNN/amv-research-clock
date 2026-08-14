from pathlib import Path

import pandas as pd

from ai_invest_advisor.dashboard.advice import build_daily_advice
from ai_invest_advisor.dashboard.metrics import (
    compute_market_heat,
    normalize_fund_flow,
    score_tech_board,
)
from ai_invest_advisor.dashboard.pipeline import append_sentiment_snapshot
from ai_invest_advisor.dashboard.pipeline import _load_market_flow


def test_normalize_fund_flow_uses_stable_columns():
    raw = pd.DataFrame(
        [
            {
                "行业": "工业母机",
                "行业-涨跌幅": 2.36,
                "流入资金": 306.29,
                "流出资金": 308.64,
                "净额": -2.35,
                "公司家数": 112,
                "领涨股": "德恩精工",
                "领涨股-涨跌幅": 20.02,
                "当前价": 32.32,
            }
        ]
    )

    normalized = normalize_fund_flow(raw, board_type="concept")

    assert normalized.loc[0, "board_name"] == "工业母机"
    assert normalized.loc[0, "pct_change"] == 2.36
    assert normalized.loc[0, "net_amount"] == -2.35
    assert normalized.loc[0, "leader"] == "德恩精工"
    assert normalized.loc[0, "board_type"] == "concept"


def test_market_heat_classifies_strong_neutral_and_weak():
    strong = pd.DataFrame(
        {
            "pct_change": [3.2, 2.4, 1.8, 1.2],
            "inflow": [200, 180, 160, 140],
            "outflow": [120, 110, 100, 90],
            "net_amount": [80, 70, 60, 50],
        }
    )
    neutral = pd.DataFrame(
        {
            "pct_change": [0.4, -0.2, 0.1, -0.1],
            "inflow": [100, 100, 100, 100],
            "outflow": [98, 101, 100, 100],
            "net_amount": [2, -1, 0, 0],
        }
    )
    weak = pd.DataFrame(
        {
            "pct_change": [-3.0, -2.1, -1.5, -0.8],
            "inflow": [80, 70, 60, 50],
            "outflow": [140, 130, 120, 110],
            "net_amount": [-60, -60, -60, -60],
        }
    )

    assert compute_market_heat(strong).label == "偏强"
    assert compute_market_heat(neutral).label == "中性震荡"
    assert compute_market_heat(weak).label == "偏弱"


def test_score_tech_board_combines_trend_funds_and_risk():
    history = pd.DataFrame(
        {
            "日期": pd.date_range("2026-01-01", periods=70, freq="D"),
            "收盘价": list(range(100, 170)),
            "成交额": [1000 + index * 5 for index in range(70)],
        }
    )
    flow = pd.Series(
        {
            "board_name": "机器人概念",
            "theme": "机器人",
            "pct_change": 2.5,
            "inflow": 500,
            "outflow": 420,
            "net_amount": 80,
            "leader": "样本龙头",
            "leader_pct_change": 12.3,
        }
    )
    info = {"涨跌家数": "80/20", "资金净流入(亿)": "80", "成交额(亿)": "920"}

    score = score_tech_board("机器人概念", "机器人", "concept", history, flow, info)

    assert score["score"] >= 70
    assert score["advice_label"] == "积极观察"
    assert score["leader"] == "样本龙头"
    assert "趋势" in score["reasons"]


def test_daily_advice_changes_with_heat_and_scores():
    strong_scores = pd.DataFrame({"score": [82, 78], "net_amount": [30, 20]})
    weak_scores = pd.DataFrame({"score": [38, 42], "net_amount": [-20, -10]})

    assert build_daily_advice(76, strong_scores).stance == "偏进攻"
    assert build_daily_advice(32, weak_scores).stance == "偏防守"


def test_append_sentiment_snapshot_keeps_existing_rows(tmp_path: Path):
    path = tmp_path / "sentiment_history.csv"
    path.write_text("date,generated_at,market_heat,label\n2026-06-14,old,44.0,偏弱\n", encoding="utf-8")

    append_sentiment_snapshot(path, "2026-06-15", "now", 61.5, "中性震荡")

    rows = pd.read_csv(path)
    assert rows["date"].tolist() == ["2026-06-14", "2026-06-15"]
    assert rows.loc[1, "market_heat"] == 61.5


def test_load_market_flow_missing_cache_returns_stable_empty_columns(tmp_path: Path):
    flow, status = _load_market_flow(tmp_path, allow_network=False)

    assert status == "missing_market_flow_cache"
    assert "board_name" in flow.columns
    assert "net_amount" in flow.columns
