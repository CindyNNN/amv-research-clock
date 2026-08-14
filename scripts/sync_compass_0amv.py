from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.compass_autoupdate import (  # noqa: E402
    CompassSyncError,
    expected_as_of_date,
    is_compass_running,
    result_to_dict,
    sync_and_export,
)
from ai_invest_advisor.compass_bridge import cache_status  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="自动启动指南针（可选）、等待0AMV缓存更新，并导出指标"
    )
    parser.add_argument("--compass-root", default=r"C:\Softwares\compass")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "compass")
    parser.add_argument("--as-of", default=None, help="期望最后交易日 YYYY-MM-DD")
    parser.add_argument("--start", default="2018-01-01", help="导出日线起始日")
    parser.add_argument("--timeout", type=int, default=420, help="等待缓存刷新秒数（含输入0AMV/点下载）")
    parser.add_argument("--poll", type=float, default=5.0, help="轮询间隔秒")
    parser.add_argument("--no-launch", action="store_true", help="不自动启动指南针")
    parser.add_argument("--no-wait", action="store_true", help="不等待刷新，直接读缓存")
    parser.add_argument(
        "--no-ui-browse",
        action="store_true",
        help="不自动输入0AMV打开行情（跳过轻量刷新）",
    )
    parser.add_argument(
        "--no-ui-download",
        action="store_true",
        help="不自动点击指南针「下载→接收最新」（浏览仍会尝试，除非同时 --no-ui-browse）",
    )
    parser.add_argument(
        "--browse-wait",
        type=float,
        default=45.0,
        help="输入0AMV后等待缓存刷新的秒数（默认45）",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="即使未更新到期望日期也导出",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        help="导出结束后强制关闭指南针（计划任务推荐）",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="导出结束后保持指南针运行",
    )
    parser.add_argument(
        "--no-prompt-manual",
        action="store_true",
        help="同步失败时不弹窗让用户手工输入今日0AMV",
    )
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="不启动指南针；弹窗录入今日涨跌幅%%并导出（推荐日常用法）",
    )
    parser.add_argument("--status-only", action="store_true", help="只打印状态")
    args = parser.parse_args(argv)

    if args.close and args.keep_open:
        print("错误: --close 与 --keep-open 不能同时使用", file=sys.stderr)
        return 2

    if args.status_only:
        payload = {
            "expected_as_of": str(expected_as_of_date()),
            "compass_running": is_compass_running(),
            "cache": cache_status(args.compass_root),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.manual_only:
        from ai_invest_advisor.compass_bridge import CompassBridgeError
        from ai_invest_advisor.compass_manual_input import prompt_and_export_amv
        import pandas as pd

        as_of = pd.to_datetime(args.as_of).date() if args.as_of else expected_as_of_date()
        try:
            snapshot, daily_path, ind_path, snap_path = prompt_and_export_amv(
                expected_as_of=as_of,
                compass_root=args.compass_root,
                out_dir=args.out_dir,
                start=args.start,
            )
        except CompassBridgeError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "manual_only": True,
                    "as_of": snapshot.get("as_of"),
                    "close": snapshot.get("close"),
                    "ret_1d": snapshot.get("ret_1d"),
                    "ret_pct_input": snapshot.get("ret_pct_input"),
                    "out_daily_csv": str(daily_path),
                    "out_indicator_csv": str(ind_path),
                    "out_snapshot_json": str(snap_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print("风险提示: 研究辅助，非投资建议。")
        return 0

    if args.close:
        close_after = "always"
    elif args.keep_open:
        close_after = "never"
    else:
        close_after = "if_launched"

    try:
        result = sync_and_export(
            compass_root=args.compass_root,
            out_dir=args.out_dir,
            as_of=args.as_of,
            launch=not args.no_launch,
            wait=not args.no_wait,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
            start=args.start,
            allow_stale=args.allow_stale,
            close_after=close_after,
            ui_browse=not args.no_ui_browse,
            ui_download=not args.no_ui_download,
            browse_wait_seconds=args.browse_wait,
            prompt_manual=not args.no_prompt_manual,
        )
    except CompassSyncError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    if result.ok and result.status.get("snapshot"):
        snap = result.status["snapshot"]
        print(
            f"\n摘要: {snap['as_of']} close={snap['close']:.2f} "
            f"ma5={snap['ma5']:.2f} regime={snap['ma5_regime']} "
            f"above_ma5={snap['above_ma5']}"
            f"{' | closed' if result.closed else ''}"
        )
        print("风险提示: 研究辅助，非投资建议。")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
