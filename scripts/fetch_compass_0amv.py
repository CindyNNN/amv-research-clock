from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.compass_bridge import (  # noqa: E402
    CompassBridgeError,
    cache_status,
    fetch_0amv,
)


DEFAULT_OUT = ROOT / "data" / "compass" / "0amv_daily.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从指南针本地缓存读取活跃市值(0AMV) K线")
    parser.add_argument("--freq", choices=["day", "min15"], default="day")
    parser.add_argument("--compass-root", default=r"C:\Softwares\compass")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--out", type=Path, default=None, help="导出 CSV 路径")
    parser.add_argument("--status", action="store_true", help="仅打印缓存状态")
    parser.add_argument("--tail", type=int, default=10, help="打印最近 N 根")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(cache_status(args.compass_root), ensure_ascii=False, indent=2))
        return 0

    try:
        frame = fetch_0amv(
            args.freq,
            compass_root=args.compass_root,
            start=args.start,
            end=args.end,
        )
    except CompassBridgeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    out = args.out
    if out is None:
        suffix = "daily" if args.freq == "day" else "min15"
        out = ROOT / "data" / "compass" / f"0amv_{suffix}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"symbol=0AMV freq={args.freq} rows={len(frame)}")
    if args.freq == "day":
        print(f"range={frame['date'].iloc[0]} -> {frame['date'].iloc[-1]}")
    else:
        print(f"range={frame['datetime'].iloc[0]} -> {frame['datetime'].iloc[-1]}")
    print(f"source={frame['source_path'].iloc[0]}")
    print(f"file_mtime={frame['file_mtime'].iloc[0]}")
    print(f"saved={out}")
    print(frame.tail(args.tail).to_string(index=False))
    print("风险提示: 研究辅助数据，非投资建议；需打开指南针同步后才是最新缓存。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
