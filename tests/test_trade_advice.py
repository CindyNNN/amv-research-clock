from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ai_invest_advisor.dashboard.trade_advice import (
    build_trade_advice,
    generate_trade_advice_report,
    render_trade_advice_markdown,
)


def test_build_trade_advice_routes_focus_wait_and_avoid():
    scores = pd.DataFrame(
        [
            {
                "board_name": "AI",
                "theme": "AI",
                "score": 82,
                "ret5": 5,
                "net_amount": 20,
                "risk_flags": "无明显异常",
                "leader": "A",
                "leader_pct_change": 6,
            },
            {
                "board_name": "Robot",
                "theme": "Robot",
                "score": 75,
                "ret5": 16,
                "net_amount": 30,
                "risk_flags": "5日涨幅偏热",
                "leader": "B",
                "leader_pct_change": 10,
            },
            {
                "board_name": "Weak",
                "theme": "Weak",
                "score": 42,
                "ret5": -4,
                "net_amount": -15,
                "risk_flags": "资金流出",
                "leader": "C",
                "leader_pct_change": -2,
            },
        ]
    )

    report = build_trade_advice(scores, market_heat=76, top=5)

    assert report.stance.label == "积极进攻"
    assert [item.board_name for item in report.focus] == ["AI"]
    assert [item.board_name for item in report.wait_for_pullback] == ["Robot"]
    assert [item.board_name for item in report.reduce_or_avoid] == ["Weak"]


def test_build_trade_advice_uses_defensive_stance_when_funds_are_negative():
    scores = pd.DataFrame(
        [
            {
                "board_name": "Cloud",
                "theme": "Cloud",
                "score": 58,
                "ret5": 2,
                "net_amount": -10,
                "risk_flags": "资金流出",
                "leader": "D",
                "leader_pct_change": 1,
            },
            {
                "board_name": "Chip",
                "theme": "Chip",
                "score": 45,
                "ret5": -3,
                "net_amount": -8,
                "risk_flags": "趋势偏弱",
                "leader": "E",
                "leader_pct_change": -1,
            },
        ]
    )

    report = build_trade_advice(scores, market_heat=62, top=5)

    assert report.stance.label == "防守观望"
    assert report.stance.allocation_hint == "科技主线建议降到 20%-40% 观察仓，优先控制回撤。"
    assert [item.board_name for item in report.reduce_or_avoid] == ["Chip", "Cloud"]


def test_render_trade_advice_markdown_includes_required_sections():
    scores = pd.DataFrame(
        [
            {
                "board_name": "AI",
                "theme": "AI",
                "score": 82,
                "ret5": 5,
                "net_amount": 20,
                "risk_flags": "\u65e0\u660e\u663e\u5f02\u5e38",
                "leader": "A",
                "leader_pct_change": 6,
            }
        ]
    )
    report = build_trade_advice(scores, market_heat=76, top=5)

    markdown = render_trade_advice_markdown(report, {"status": "cached"})

    assert "# \u79d1\u6280\u677f\u5757\u4ea4\u6613\u5efa\u8bae" in markdown
    assert "## \u53ef\u5173\u6ce8" in markdown
    assert "## \u7b49\u5f85\u56de\u8c03" in markdown
    assert "## \u51cf\u4ed3/\u56de\u907f" in markdown
    assert "This is research support, not financial advice." in markdown


def test_generate_trade_advice_report_reads_cache_and_writes_markdown(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {
                "board_name": "AI",
                "theme": "AI",
                "score": 82,
                "ret5": 5,
                "net_amount": 20,
                "risk_flags": "\u65e0\u660e\u663e\u5f02\u5e38",
                "leader": "A",
                "leader_pct_change": 6,
            }
        ]
    ).to_csv(cache_dir / "tech_board_scores.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"date": "2026-06-17", "generated_at": "now", "market_heat": 76, "label": "\u504f\u5f3a"}]).to_csv(
        cache_dir / "sentiment_history.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (cache_dir / "data_status.json").write_text(json.dumps({"status": "cached"}), encoding="utf-8")

    path = generate_trade_advice_report(cache_dir=cache_dir, output_dir=output_dir, top=5)

    content = path.read_text(encoding="utf-8")
    assert path.name == "2026-06-17-tech-trade-advice.md"
    assert "\u79ef\u6781\u8fdb\u653b" in content
    assert "AI" in content


def test_generate_trade_advice_report_warns_when_sentiment_is_missing(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {
                "board_name": "Weak",
                "theme": "Weak",
                "score": 42,
                "ret5": -4,
                "net_amount": -15,
                "risk_flags": "\u8d44\u91d1\u6d41\u51fa",
                "leader": "C",
                "leader_pct_change": -2,
            }
        ]
    ).to_csv(cache_dir / "tech_board_scores.csv", index=False, encoding="utf-8-sig")

    path = generate_trade_advice_report(cache_dir=cache_dir, output_dir=output_dir, top=5)

    content = path.read_text(encoding="utf-8")
    assert "sentiment_history.csv is missing" in content
    assert "\u9632\u5b88\u89c2\u671b" in content
