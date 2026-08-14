from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ai_invest_advisor.config import load_settings
from ai_invest_advisor.dashboard.advice import build_daily_advice
from ai_invest_advisor.dashboard.chart_views import (
    add_moving_averages,
    build_intraday_reference,
    prepare_ohlc_view,
    prepare_sentiment_daily_view,
)
from ai_invest_advisor.dashboard.metrics import parse_info_frame, to_float
from ai_invest_advisor.dashboard.pipeline import (
    DASHBOARD_CACHE_DIR,
    latest_tech_board_dir,
    refresh_dashboard_cache,
)
from ai_invest_advisor.dashboard.report import generate_daily_report

GREEN = "#20d68b"
RED = "#ff4d5a"
YELLOW = "#f6c453"
CYAN = "#52c7ff"
BLUE = "#3977ff"
BG = "#090d14"
PANEL = "#101722"
PANEL_2 = "#141d2a"
GRID = "#263247"
TEXT = "#e7edf7"
MUTED = "#8792a6"
MA_COLORS = {
    5: YELLOW,
    10: CYAN,
    20: BLUE,
    60: "#b58cff",
}


st.set_page_config(page_title="AI 科技板块投资终端", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    f"""
    <style>
    .stApp {{
        background:
            radial-gradient(circle at 20% -10%, rgba(32,214,139,.10), transparent 28rem),
            linear-gradient(180deg, #080b11 0%, {BG} 100%);
        color: {TEXT};
    }}
    .block-container {{
        padding: 2.75rem 1.2rem 1.6rem;
        max-width: 1680px;
    }}
    h1, h2, h3 {{ letter-spacing: 0; }}
.futu-topbar {{
        display:flex; align-items:center; justify-content:space-between;
        gap:1rem; min-height:54px;
        padding:.55rem .8rem; border:1px solid #202a3a;
        background:linear-gradient(180deg, #151d29, #0e141f);
        border-radius:8px; margin-bottom:.65rem;
    }}
    .brand-title {{ font-size:1.15rem; font-weight:700; color:#f8fbff; }}
    .brand-sub {{ color:{MUTED}; font-size:.78rem; margin-top:.1rem; }}
    .status-dot {{
        display:inline-block; width:8px; height:8px; border-radius:99px;
        background:{GREEN}; margin-right:.35rem;
        box-shadow:0 0 12px rgba(32,214,139,.65);
    }}
    .ticker-strip {{
        display:grid; grid-template-columns: repeat(6, minmax(0, 1fr));
        gap:.45rem; margin:.35rem 0 .75rem;
    }}
    .ticker-card {{
        border:1px solid #202a3a; background:{PANEL};
        border-radius:8px; padding:.55rem .65rem; min-height:68px;
    }}
    .ticker-name {{ color:{TEXT}; font-size:.86rem; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .ticker-meta {{ color:{MUTED}; font-size:.72rem; margin-top:.12rem; }}
    .ticker-value {{ font-size:1rem; font-weight:700; margin-top:.22rem; }}
    .up {{ color:{GREEN}; }}
    .down {{ color:{RED}; }}
    .flat {{ color:{YELLOW}; }}
    .panel {{
        border:1px solid #202a3a;
        background:linear-gradient(180deg, {PANEL_2}, {PANEL});
        border-radius:8px;
        padding:.75rem;
    }}
    .section-label {{ color:{MUTED}; font-size:.74rem; text-transform:uppercase; margin-bottom:.25rem; }}
    .big-number {{ color:#f8fbff; font-size:2rem; font-weight:800; line-height:1.05; }}
    .small-note {{ color:{MUTED}; font-size:.78rem; }}
    div[data-testid="stMetric"] {{
        background:{PANEL}; border:1px solid #202a3a; border-radius:8px;
        padding:.55rem .7rem;
    }}
    [data-testid="stMetricLabel"] {{ color:{MUTED}; }}
    [data-testid="stMetricValue"] {{ color:{TEXT}; font-size:1.25rem; }}
    div[data-testid="stRadio"] label {{ color:{TEXT}; }}
    .stButton > button {{
        border-radius:6px; border:1px solid #2a3a52;
        background:#182234; color:{TEXT};
    }}
    .stButton > button:hover {{
        border-color:{CYAN}; color:#fff;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_history_map() -> dict[str, pd.DataFrame]:
    tech_dir = latest_tech_board_dir(load_settings().tech_boards.output_dir)
    paths = list((tech_dir / "ths_concept" / "history").glob("*.csv")) + list((tech_dir / "ths_industry" / "history").glob("*.csv"))
    frames: dict[str, pd.DataFrame] = {}
    for path in paths:
        frame = pd.read_csv(path)
        if not frame.empty and "板块名称" in frame.columns:
            frames[str(frame["板块名称"].iloc[0])] = frame
    return frames


@st.cache_data(show_spinner=False)
def load_info_map() -> dict[str, dict[str, str]]:
    tech_dir = latest_tech_board_dir(load_settings().tech_boards.output_dir)
    paths = list((tech_dir / "ths_concept" / "info").glob("*.csv")) + list((tech_dir / "ths_industry" / "info").glob("*.csv"))
    maps: dict[str, dict[str, str]] = {}
    for path in paths:
        frame = pd.read_csv(path)
        if not frame.empty and "板块名称" in frame.columns:
            maps[str(frame["板块名称"].iloc[0])] = parse_info_frame(frame)
    return maps


def color_class(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def display_text(value: object, fallback: str = "-") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text if text and text.lower() != "nan" else fallback


def sample_tick_labels(labels: list[str], max_ticks: int = 8) -> list[str]:
    if len(labels) <= max_ticks:
        return labels
    if max_ticks <= 1:
        return [labels[-1]]
    step = (len(labels) - 1) / (max_ticks - 1)
    indices = sorted({round(index * step) for index in range(max_ticks)})
    return [labels[index] for index in indices]


def render_topbar(status: dict[str, object], latest_date: str) -> None:
    generated_at = escape(str(status.get("generated_at", "-")))
    status_text = escape(str(status.get("status", "-")))
    st.markdown(
        "<div class=\"futu-topbar\">"
        "<div><div class=\"brand-title\">AI 科技板块投资终端</div>"
        "<div class=\"brand-sub\">A股全市场情绪 + 科技细分主线</div></div>"
        "<div class=\"small-note\"><span class=\"status-dot\"></span>"
        f"交易日 {escape(latest_date)} ｜ 更新时间 {generated_at} ｜ 数据 {status_text}"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_ticker_strip(scores: pd.DataFrame) -> None:
    cards = []
    for _, row in scores.head(6).iterrows():
        net = float(row["net_amount"])
        ret5 = float(row["ret5"])
        board_name = escape(str(row["board_name"]))
        theme = escape(str(row["theme"]))
        advice_label = escape(str(row["advice_label"]))
        cards.append(
            "<div class=\"ticker-card\">"
            f"<div class=\"ticker-name\">{board_name}</div>"
            f"<div class=\"ticker-meta\">{theme} ｜ {advice_label}</div>"
            f"<div class=\"ticker-value {color_class(net)}\">评分 {float(row['score']):.1f}</div>"
            f"<div class=\"ticker-meta\">5日 <span class=\"{color_class(ret5)}\">{ret5:.2f}%</span>"
            f" ｜ 净额 <span class=\"{color_class(net)}\">{net:.2f}</span></div>"
            "</div>"
        )
    st.markdown(f"<div class=\"ticker-strip\">{''.join(cards)}</div>", unsafe_allow_html=True)


def sentiment_chart(sentiment: pd.DataFrame) -> go.Figure:
    daily = prepare_sentiment_daily_view(sentiment)
    fig = go.Figure()
    if not daily.empty:
        fig.add_trace(
            go.Scatter(
                x=daily["date_label"],
                y=daily["market_heat"],
                customdata=daily[["label", "generated_at"]],
                mode="lines+markers",
                line=dict(color=GREEN, width=2),
                marker=dict(size=5, color=GREEN),
                hovertemplate=(
                    "交易日 %{x}<br>"
                    "热度 %{y:.2f}<br>"
                    "%{customdata[0]}<br>"
                    "更新 %{customdata[1]}<extra></extra>"
                ),
            )
        )
    fig.add_hline(y=70, line_color=RED, line_width=1.2)
    fig.add_hline(y=45, line_color=GREEN, line_width=1, line_dash="dash")
    fig.update_layout(
        height=220,
        margin=dict(l=8, r=8, t=26, b=8),
        title=dict(text="Market Heat 日线", font=dict(size=12, color=MUTED), x=0.02, y=0.96),
        paper_bgcolor=PANEL,
        plot_bgcolor="#0c121c",
        font=dict(color=TEXT),
        yaxis=dict(range=[0, 100], gridcolor=GRID, zeroline=False),
        xaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=daily["date_label"].tolist() if not daily.empty else [],
            tickmode="array",
            tickvals=sample_tick_labels(daily["date_label"].tolist()) if not daily.empty else [],
            gridcolor=GRID,
            zeroline=False,
        ),
        showlegend=False,
    )
    return fig


def theme_flow_chart(scores: pd.DataFrame) -> go.Figure:
    theme_flow = scores.groupby("theme", as_index=False)["net_amount"].sum().sort_values("net_amount")
    colors = [GREEN if value >= 0 else RED for value in theme_flow["net_amount"]]
    fig = go.Figure(go.Bar(x=theme_flow["net_amount"], y=theme_flow["theme"], orientation="h", marker_color=colors))
    fig.update_layout(
        height=260,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor=PANEL,
        plot_bgcolor="#0c121c",
        font=dict(color=TEXT),
        xaxis=dict(title="净额", gridcolor=GRID, zerolinecolor="#46536a"),
        yaxis=dict(title=""),
        showlegend=False,
    )
    return fig


def intraday_chart(board_name: str, history: pd.DataFrame, info: dict[str, str]) -> go.Figure:
    intraday = build_intraday_reference(history, info)
    yesterday = to_float(info.get("昨收"), float(intraday["price"].iloc[0]))
    color = GREEN if float(intraday["price"].iloc[-1]) >= yesterday else RED
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=pd.to_datetime(intraday["time"]),
            y=intraday["price"],
            mode="lines",
            name=board_name,
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor="rgba(32,214,139,.08)" if color == GREEN else "rgba(255,77,90,.08)",
            hovertemplate="%{x|%H:%M}<br>%{y:.2f}<extra></extra>",
        )
    )
    fig.add_hline(y=yesterday, line_dash="dash", line_color="#8a96aa", annotation_text="昨收")
    fig.update_layout(
        height=470,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor=PANEL,
        plot_bgcolor="#0c121c",
        font=dict(color=TEXT),
        yaxis=dict(gridcolor=GRID, zeroline=False),
        xaxis=dict(gridcolor=GRID, zeroline=False),
        showlegend=False,
    )
    return fig


def kline_chart(history: pd.DataFrame, period: str) -> go.Figure:
    ma_windows = [5, 10, 20, 60] if period == "日线" else [5, 10, 20]
    frame = add_moving_averages(prepare_ohlc_view(history, period), ma_windows)
    x_values = frame["date_label"].tolist()
    tick_values = sample_tick_labels(x_values, max_ticks=9)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.02, row_heights=[0.75, 0.25])
    up_color = GREEN
    down_color = RED
    fig.add_trace(
        go.Candlestick(
            x=x_values,
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            increasing_line_color=up_color,
            increasing_fillcolor=up_color,
            decreasing_line_color=down_color,
            decreasing_fillcolor=down_color,
            name=period,
        ),
        row=1,
        col=1,
    )
    for window in ma_windows:
        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=frame[f"ma{window}"],
                mode="lines",
                name=f"MA{window}",
                line=dict(color=MA_COLORS[window], width=1.15),
                connectgaps=False,
                hovertemplate=f"MA{window} %{{y:.2f}}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    volume_colors = [up_color if close >= open_ else down_color for open_, close in zip(frame["open"], frame["close"])]
    fig.add_trace(
        go.Bar(x=x_values, y=frame["amount"], marker_color=volume_colors, name="成交额"),
        row=2,
        col=1,
    )
    fig.update_layout(
        height=470,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor=PANEL,
        plot_bgcolor="#0c121c",
        font=dict(color=TEXT),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1, font=dict(size=10)),
        showlegend=True,
    )
    fig.update_xaxes(
        type="category",
        categoryorder="array",
        categoryarray=x_values,
        tickmode="array",
        tickvals=tick_values,
        ticktext=tick_values,
        gridcolor=GRID,
        zeroline=False,
    )
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def market_board_table(scores: pd.DataFrame) -> pd.DataFrame:
    table = scores[
        [
            "board_name",
            "theme",
            "score",
            "advice_label",
            "net_amount",
            "ret5",
            "ret20",
            "leader",
            "risk_flags",
        ]
    ].copy()
    table["leader"] = table["leader"].map(display_text)
    table["risk_flags"] = table["risk_flags"].map(display_text)
    table = table.where(pd.notna(table), "-")
    return table.rename(
        columns={
            "board_name": "板块",
            "theme": "主题",
            "score": "评分",
            "advice_label": "建议",
            "net_amount": "净额",
            "ret5": "5日%",
            "ret20": "20日%",
            "leader": "领涨股",
            "risk_flags": "风险",
        }
    )


def main() -> None:
    status_path = DASHBOARD_CACHE_DIR / "data_status.json"
    scores_path = DASHBOARD_CACHE_DIR / "tech_board_scores.csv"
    sentiment_path = DASHBOARD_CACHE_DIR / "sentiment_history.csv"

    scores = read_csv(scores_path)
    sentiment = read_csv(sentiment_path)
    status = load_status(status_path)
    if scores.empty or sentiment.empty:
        st.warning("还没有 dashboard 缓存。请点击刷新，或先运行 `python -m ai_invest_advisor.cli refresh-dashboard`。")
        if st.button("刷新数据"):
            refresh_dashboard_cache(load_settings(), allow_network=True)
            st.rerun()
        return

    histories = load_history_map()
    infos = load_info_map()
    daily_sentiment = prepare_sentiment_daily_view(sentiment)
    latest_sentiment = daily_sentiment.iloc[-1] if not daily_sentiment.empty else sentiment.iloc[-1]
    latest_heat = float(latest_sentiment["market_heat"])
    heat_label = str(latest_sentiment["label"])
    advice = build_daily_advice(latest_heat, scores)
    strongest = scores.iloc[0]

    render_topbar(status, str(strongest["latest_date"]))
    render_ticker_strip(scores)

    action_left, action_mid, action_right = st.columns([0.18, 0.18, 0.64])
    if action_left.button("刷新行情", use_container_width=True):
        with st.spinner("正在刷新资金流与板块评分..."):
            refresh_dashboard_cache(load_settings(), allow_network=True)
        st.rerun()
    if action_mid.button("生成日报", use_container_width=True):
        path = generate_daily_report()
        st.success(f"日报已生成：{path}")
    action_right.caption("板块分时仅作盘中参考；当前无真实分钟线缓存时，使用今开/高/低/收生成参考线。")

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Market Heat", f"{latest_heat:.2f}", heat_label)
    kpi2.metric("科技净额", f"{float(scores['net_amount'].sum()):.2f}")
    kpi3.metric("最强板块", str(strongest["board_name"]), str(strongest["theme"]))
    kpi4.metric("今日策略", advice.stance, "研究辅助")

    left, center, right = st.columns([0.22, 0.53, 0.25])

    with left:
        st.markdown("#### 板块榜单")
        themes = ["全部"] + sorted(scores["theme"].dropna().unique().tolist())
        theme_filter = st.selectbox("主题", themes, index=0)
        board_scope = scores if theme_filter == "全部" else scores[scores["theme"] == theme_filter]
        board_names = board_scope["board_name"].tolist()
        selected_board = st.radio("选择板块", board_names, index=0, label_visibility="collapsed")
        st.markdown("#### 市场情绪（日线）")
        st.plotly_chart(sentiment_chart(sentiment), use_container_width=True)
        st.markdown("#### 主题资金")
        st.plotly_chart(theme_flow_chart(board_scope), use_container_width=True)

    with center:
        selected_row = scores[scores["board_name"] == selected_board].iloc[0]
        board_history = histories.get(selected_board, pd.DataFrame())
        board_info = infos.get(selected_board, {})
        price_class = color_class(float(selected_row["ret5"]))
        st.markdown(
            f"""
            <div class="panel">
              <div class="section-label">Selected Board</div>
              <div style="display:flex;align-items:flex-end;gap:.75rem;">
                <div class="big-number">{selected_board}</div>
                <div class="{price_class}" style="font-weight:700;">5日 {float(selected_row['ret5']):.2f}%</div>
                <div class="{color_class(float(selected_row['net_amount']))}" style="font-weight:700;">净额 {float(selected_row['net_amount']):.2f}</div>
              </div>
              <div class="small-note">{selected_row['theme']} ｜ {selected_row['advice_label']} ｜ 领涨股 {display_text(selected_row.get('leader'))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        period = st.radio("周期", ["分时", "日线", "周线"], horizontal=True, label_visibility="collapsed")
        if period == "分时":
            st.plotly_chart(intraday_chart(selected_board, board_history, board_info), use_container_width=True)
        else:
            st.plotly_chart(kline_chart(board_history, period), use_container_width=True)

        st.markdown("#### 科技板块评分矩阵")
        st.dataframe(market_board_table(board_scope), use_container_width=True, hide_index=True)

    with right:
        st.markdown("#### 今日市场解释")
        st.markdown(
            f"""
            <div class="panel">
              <div class="section-label">Market Heat</div>
              <div class="big-number">{latest_heat:.2f}</div>
              <div class="small-note">{heat_label}。本指标由上涨占比、资金强度、平均涨跌幅和领涨强度构成，不等同同花顺官方口径。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("#### 科技板块建议")
        for label, color in [("积极观察", GREEN), ("等待回调", YELLOW), ("谨慎回避", RED)]:
            subset = scores[scores["advice_label"] == label].head(4)
            st.markdown(f"<div style='color:{color};font-weight:800;margin-top:.65rem'>{label}</div>", unsafe_allow_html=True)
            if subset.empty:
                st.caption("暂无")
            for _, row in subset.iterrows():
                st.write(f"{row['board_name']} ｜ {row['theme']} ｜ {row['score']:.1f}")

        st.markdown("#### 每日投资建议")
        st.markdown(f"### {advice.stance}")
        st.write(advice.summary)
        st.info(advice.allocation_hint)
        for note in advice.risk_notes:
            st.warning(note)
        st.caption("This is research support, not financial advice.")


if __name__ == "__main__":
    main()
