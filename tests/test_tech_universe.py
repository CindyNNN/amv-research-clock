import pandas as pd

from ai_invest_advisor.data.tech_universe import classify_board, filter_tech_boards


def test_classify_board_matches_semiconductor_equipment():
    assert "半导体设备" in classify_board("半导体设备")


def test_filter_tech_boards_excludes_traditional_board():
    frame = pd.DataFrame(
        [
            {"板块名称": "PCB", "板块代码": "BK1"},
            {"板块名称": "银行", "板块代码": "BK2"},
            {"板块名称": "机器人概念", "板块代码": "BK3"},
        ]
    )

    result = filter_tech_boards(frame)

    assert result["板块名称"].tolist() == ["PCB", "机器人概念"]
    assert result["主题"].tolist() == ["PCB", "机器人"]
