from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.tdx_indicator_push import (  # noqa: E402
    TdxPayloadError,
    load_tdx_payload,
    send_tdx_payload,
)


DEFAULT_DATA_PATH = (
    ROOT / "data" / "monitor" / "ths_cyb_emotion_subchart.csv"
)
DEFAULT_TDX_HOME = Path(r"C:\Softwares\new_tdx")


def run_push(*, data_path: Path, tq) -> int:
    initialized = False
    try:
        payload = load_tdx_payload(data_path)
        tq.initialize(__file__)
        initialized = True
        result = send_tdx_payload(tq, payload)
        print(
            "通达信副图推送成功: "
            f"{len(payload.time_list)}行, "
            f"{payload.time_list[0]}-{payload.time_list[-1]}, "
            f"ErrorId={result['ErrorId']}"
        )
        return 0
    except (TdxPayloadError, OSError, RuntimeError, ValueError) as exc:
        print(f"通达信副图推送失败: {exc}", file=sys.stderr)
        return 2
    finally:
        if initialized:
            tq.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="向通达信TdxQuant推送创业板情绪副图历史"
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="创业板情绪副图CSV",
    )
    parser.add_argument(
        "--tdx-home",
        type=Path,
        default=DEFAULT_TDX_HOME,
        help="通达信安装目录",
    )
    args = parser.parse_args(argv)

    tqcenter_dir = args.tdx_home / "PYPlugins" / "user"
    tqcenter_file = tqcenter_dir / "tqcenter.py"
    if not tqcenter_file.is_file():
        print(
            f"找不到TdxQuant模块 tqcenter.py: {tqcenter_file}",
            file=sys.stderr,
        )
        return 4
    if str(tqcenter_dir) not in sys.path:
        sys.path.insert(0, str(tqcenter_dir))
    try:
        tqcenter = importlib.import_module("tqcenter")
    except (ImportError, OSError) as exc:
        print(f"无法加载TdxQuant模块: {exc}", file=sys.stderr)
        return 4
    return run_push(data_path=args.data, tq=tqcenter.tq)


if __name__ == "__main__":
    raise SystemExit(main())
