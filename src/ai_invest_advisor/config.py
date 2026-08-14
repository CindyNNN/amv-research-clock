from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


@dataclass(frozen=True)
class TechBoardSettings:
    board_source: str
    output_dir: Path
    history_start: str
    history_period: str
    history_adjust: str


@dataclass(frozen=True)
class Settings:
    data_source: str
    market: str
    cache_dir: Path
    report_dir: Path
    tdx_path: Path | None
    tech_boards: TechBoardSettings


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _resolve_path(raw: str, base_dir: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base_dir / path


def load_settings(path: Path | str = Path("config/settings.toml")) -> Settings:
    settings_path = Path(path)
    base_dir = settings_path.parent.parent if settings_path.parent.name == "config" else Path.cwd()
    data = _read_toml(settings_path)
    tech = data.get("tech_boards", {})
    tdx_raw = str(data.get("tdx_path", "")).strip()

    return Settings(
        data_source=str(data.get("data_source", "akshare")),
        market=str(data.get("market", "a_share")),
        cache_dir=_resolve_path(str(data.get("cache_dir", "data/cache")), base_dir),
        report_dir=_resolve_path(str(data.get("report_dir", "reports")), base_dir),
        tdx_path=_resolve_path(tdx_raw, base_dir) if tdx_raw else None,
        tech_boards=TechBoardSettings(
            board_source=str(tech.get("board_source", "ths")),
            output_dir=_resolve_path(str(tech.get("output_dir", "data/tech_boards")), base_dir),
            history_start=str(tech.get("history_start", "20240101")),
            history_period=str(tech.get("history_period", "daily")),
            history_adjust=str(tech.get("history_adjust", "")),
        ),
    )
