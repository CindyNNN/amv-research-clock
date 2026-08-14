from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.cyb_market_data import (  # noqa: E402
    MarketDataError,
    RunMode,
    load_complete_history,
)
from ai_invest_advisor.cyb_signal_monitor import (  # noqa: E402
    ModelState,
    SignalDecision,
    StateFileError,
    evaluate_snapshot,
    load_state,
    replay_history,
    save_state_atomic,
    snapshot_from_row,
)
from ai_invest_advisor.qq_email import (  # noqa: E402
    EmailConfigError,
    QQEmailConfig,
    build_error_message,
    build_intraday_unavailable_message,
    build_status_message,
    send_message,
)
from ai_invest_advisor.ths_indicator_export import (  # noqa: E402
    build_subchart_frame,
    write_subchart_csv_atomic,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_STATE_PATH = (
    ROOT / "data" / "monitor" / "cyb_amv_emotion_strategy_state.json"
)
DEFAULT_THS_EXPORT_PATH = (
    ROOT / "data" / "monitor" / "ths_cyb_emotion_subchart.csv"
)


def _advance_existing_state(
    state: ModelState,
    history: pd.DataFrame,
) -> tuple[ModelState, SignalDecision]:
    data = history.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError("完整历史数据为空")
    if state.processed_date:
        processed = pd.Timestamp(state.processed_date)
        new_rows = data.loc[data["date"] > processed]
    else:
        new_rows = data

    decision: SignalDecision | None = None
    if new_rows.empty:
        state, decision = evaluate_snapshot(
            state,
            snapshot_from_row(data.iloc[-1]),
        )
    else:
        for _, row in new_rows.iterrows():
            state, decision = evaluate_snapshot(
                state,
                snapshot_from_row(row),
            )
    assert decision is not None
    return state, decision


def run_monitor(
    *,
    mode: RunMode = "close",
    dry_run: bool,
    state_path: Path,
    export_path: Path | None = None,
    now: datetime,
    as_of: date | None = None,
    workers: int = 4,
    load_history: Callable = load_complete_history,
    send: Callable = send_message,
    environ: Mapping[str, str] = os.environ,
    save_state: Callable = save_state_atomic,
) -> int:
    if mode not in ("intraday", "close"):
        raise ValueError(f"unsupported run mode: {mode}")
    as_of = as_of or now.date()
    if dry_run:
        config = QQEmailConfig("preview@qq.com", "preview-only")
    else:
        try:
            config = QQEmailConfig.from_env(environ)
        except EmailConfigError as exc:
            print(f"邮箱配置错误：{exc}", file=sys.stderr)
            return 4

    try:
        history = load_history(
            as_of=as_of,
            now=now,
            workers=workers,
            mode=mode,
        )
        latest_date = pd.Timestamp(history["date"].max()).date()
        if mode == "close" and latest_date < as_of:
            raise MarketDataError(
                "收盘确认缺少当天完整数据："
                f"请求日期 {as_of.isoformat()}，"
                f"最新数据 {latest_date.isoformat()}"
            )
        if mode == "intraday" and latest_date < as_of:
            message = build_intraday_unavailable_message(
                config=config,
                run_at=now,
                latest_date=latest_date,
            )
            next_state = None
            decision = None
        else:
            if mode == "close" and export_path is not None:
                write_subchart_csv_atomic(
                    build_subchart_frame(history),
                    export_path,
                )
            state_rebuilt = False
            if state_path.exists():
                try:
                    state = load_state(state_path)
                    next_state, decision = _advance_existing_state(state, history)
                except StateFileError:
                    next_state, decision = replay_history(history)
                    state_rebuilt = True
            else:
                next_state, decision = replay_history(history)
                state_rebuilt = True
            stale = latest_date < as_of
            message = build_status_message(
                config=config,
                decision=decision,
                run_at=now,
                stale=stale,
                state_rebuilt=state_rebuilt,
                mode=mode,
            )
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: {exc}"
        if dry_run:
            print(f"[ERROR]\n{safe_error}", file=sys.stderr)
            return 2
        error_message = build_error_message(
            config=config,
            error=safe_error,
            run_at=now,
            mode=mode,
        )
        try:
            send(config, error_message)
        except Exception as send_exc:
            print(
                f"数据处理失败且错误邮件发送失败：{type(send_exc).__name__}",
                file=sys.stderr,
            )
            return 3
        print(f"数据处理失败，已发送ERROR邮件：{safe_error}", file=sys.stderr)
        return 2

    if dry_run:
        print(message["Subject"])
        print(message.get_content())
        return 0

    try:
        send(config, message)
    except Exception as exc:
        print(
            f"邮件发送失败，状态未改变：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3
    if decision is None:
        print(f"盘中数据不可用邮件已发送：最新数据日期 {latest_date.isoformat()}")
        return 0
    if mode == "close":
        assert next_state is not None
        save_state(state_path, next_state)
    print(
        f"邮件已发送：{decision.signal}，数据日期 "
        f"{decision.snapshot.date.isoformat()}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创业板 AMV+情绪策略每日邮件监控（0AMV入场，情绪/急跌离场，MA60保护）"
    )
    parser.add_argument(
        "--mode",
        choices=("intraday", "close"),
        default="close",
        help="intraday=盘中预估；close=收盘严谨确认",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="更新数据并预览邮件，不发送邮件、不修改策略状态",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="策略模型状态文件路径",
    )
    parser.add_argument(
        "--ths-export",
        type=Path,
        default=DEFAULT_THS_EXPORT_PATH,
        help="同花顺远航版创业板情绪副图数据文件",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="数据截止日期，格式 YYYY-MM-DD",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_monitor(
        mode=args.mode,
        dry_run=args.dry_run,
        state_path=args.state,
        export_path=args.ths_export,
        now=datetime.now(SHANGHAI),
        as_of=args.as_of,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
