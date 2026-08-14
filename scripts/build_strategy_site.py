"""Generate the public strategy research site into site/.

Research support only; not investment advice.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.amv_cloud import beijing_today  # noqa: E402
from ai_invest_advisor.strategy_site import build_site  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成创业板 0AMV 研究站点")
    parser.add_argument("--amv-close", type=float, default=None, help="今日 0AMV 收盘价（可选）")
    parser.add_argument("--amv-date", default=None, help="0AMV 日期 YYYY-MM-DD，默认北京今天")
    parser.add_argument("--offline", action="store_true", help="不强制在线刷新，优先用缓存")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "site")
    args = parser.parse_args(argv)

    amv_date: date | None = None
    if args.amv_date:
        amv_date = date.fromisoformat(args.amv_date)

    result = build_site(
        amv_close=args.amv_close,
        amv_date=amv_date,
        force_download=not args.offline,
        out_dir=args.out_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n本地预览: python -m http.server 8080 --directory {args.out_dir}")
    print(f"数据截至策略有效日 {result['strategy_end']}（北京日历参考 {beijing_today()}）")
    print("研究辅助，不是投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
