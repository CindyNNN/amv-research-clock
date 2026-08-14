"""Ask for today's 0AMV close and update local CSV — no Compass launch.

Research support only; not investment advice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.compass_autoupdate import expected_as_of_date  # noqa: E402
from ai_invest_advisor.compass_bridge import CompassBridgeError  # noqa: E402
from ai_invest_advisor.compass_manual_input import prompt_and_export_amv  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="弹窗录入今日活跃市值收盘，更新本地0AMV（不启动指南针）"
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "compass")
    parser.add_argument("--compass-root", default=r"C:\Softwares\compass")
    parser.add_argument("--as-of", default=None, help="日期 YYYY-MM-DD，默认按交易日启发式")
    parser.add_argument("--start", default="2018-01-01")
    args = parser.parse_args(argv)

    if args.as_of:
        import pandas as pd

        as_of = pd.to_datetime(args.as_of).date()
    else:
        as_of = expected_as_of_date()

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
                "as_of": snapshot.get("as_of"),
                "close": snapshot.get("close"),
                "ret_1d": snapshot.get("ret_1d"),
                "ret_pct_input": snapshot.get("ret_pct_input"),
                "ma5_regime": snapshot.get("ma5_regime"),
                "out_daily_csv": str(daily_path),
                "out_indicator_csv": str(ind_path),
                "out_snapshot_json": str(snap_path),
                "source": snapshot.get("source"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    ret = snapshot.get("ret_1d")
    ret_txt = f"{ret:.2%}" if isinstance(ret, (int, float)) else "?"
    print(
        f"\n摘要: {snapshot.get('as_of')} close={snapshot.get('close'):.2f} "
        f"涨跌={ret_txt} regime={snapshot.get('ma5_regime')}"
    )
    print("风险提示: 研究辅助，非投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
