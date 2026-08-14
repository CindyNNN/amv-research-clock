from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def safe_name(value: str) -> str:
    allowed = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_"):
            allowed.append(char)
        elif char in (" ", "/", "\\", ":", "|"):
            allowed.append("_")
        else:
            allowed.append(char)
    return "".join(allowed).strip("_") or "unnamed"


def write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def timestamped_dir(base_dir: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base_dir / f"{prefix}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path
