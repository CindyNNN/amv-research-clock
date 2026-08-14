from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ThemeRule:
    theme: str
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...] = ()


TECH_THEME_RULES: tuple[ThemeRule, ...] = (
    ThemeRule("PCB", ("PCB", "印制电路", "电路板", "覆铜板")),
    ThemeRule("CPO", ("CPO", "光模块", "光通信", "硅光", "高速铜连接", "铜缆高速连接")),
    ThemeRule("机器人", ("机器人", "减速器", "伺服", "机器视觉", "人形机器人", "工业母机")),
    ThemeRule("液冷", ("液冷", "数据中心", "服务器", "算力", "IDC")),
    ThemeRule("商业航天", ("商业航天", "卫星", "低空经济", "航天", "北斗", "无人机")),
    ThemeRule("稀土", ("稀土", "永磁", "磁材")),
    ThemeRule("小金属", ("小金属", "钨", "钼", "锑", "锗", "镓", "铟", "钛", "锆")),
    ThemeRule("半导体", ("半导体", "集成电路", "先进封装", "第三代半导体")),
    ThemeRule("半导体材料", ("半导体材料", "光刻胶", "硅片", "电子化学品", "靶材", "特气", "封装材料")),
    ThemeRule("半导体设备", ("半导体设备", "光刻机", "刻蚀", "薄膜沉积", "量测", "检测设备")),
    ThemeRule("芯片", ("芯片", "AI芯片", "存储芯片", "汽车芯片", "GPU", "CPU", "MCU", "SoC")),
)


def classify_board(name: str) -> list[str]:
    matched: list[str] = []
    normalized = str(name).upper()
    original = str(name)
    for rule in TECH_THEME_RULES:
        has_keyword = any(keyword.upper() in normalized or keyword in original for keyword in rule.keywords)
        has_excluded = any(keyword.upper() in normalized or keyword in original for keyword in rule.exclude_keywords)
        if has_keyword and not has_excluded:
            matched.append(rule.theme)
    return matched


def filter_tech_boards(frame: pd.DataFrame, name_column: str = "板块名称") -> pd.DataFrame:
    if name_column not in frame.columns:
        raise ValueError(f"Board data is missing name column: {name_column}")
    result = frame.copy()
    result["主题"] = result[name_column].map(lambda name: "、".join(classify_board(str(name))))
    result = result[result["主题"].astype(bool)].reset_index(drop=True)
    return result


def theme_keywords_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "主题": rule.theme,
            "关键词": "、".join(rule.keywords),
            "排除词": "、".join(rule.exclude_keywords),
        }
        for rule in TECH_THEME_RULES
    )
