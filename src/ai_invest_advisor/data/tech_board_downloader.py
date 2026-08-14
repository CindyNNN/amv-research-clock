from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Callable

import pandas as pd

from ai_invest_advisor.config import TechBoardSettings
from ai_invest_advisor.data.akshare_adapter import (
    fetch_concept_boards,
    fetch_concept_boards_ths,
    fetch_concept_constituents,
    fetch_concept_history,
    fetch_concept_history_ths,
    fetch_concept_info_ths,
    fetch_industry_boards,
    fetch_industry_boards_ths,
    fetch_industry_constituents,
    fetch_industry_history,
    fetch_industry_history_ths,
    fetch_industry_info_ths,
)
from ai_invest_advisor.data.cache import safe_name, timestamped_dir, write_csv
from ai_invest_advisor.data.tech_universe import filter_tech_boards, theme_keywords_frame


@dataclass(frozen=True)
class DownloadResult:
    output_dir: Path
    concept_count: int
    industry_count: int
    files: tuple[Path, ...]
    failures: tuple[str, ...]


def _with_retries(label: str, action: Callable[[], pd.DataFrame], attempts: int = 3, delay_seconds: float = 1.5) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # pragma: no cover - integration safety
            last_error = exc
            if attempt < attempts:
                sleep(delay_seconds)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error


def _download_board_details(
    boards: pd.DataFrame,
    board_type: str,
    output_dir: Path,
    settings: TechBoardSettings,
    include_history: bool,
    include_constituents: bool,
) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    failures: list[str] = []
    name_column = "板块名称"
    detail_dir = output_dir / board_type

    for _, row in boards.iterrows():
        board_name = str(row[name_column])
        file_stem = safe_name(f"{board_name}_{row.get('板块代码', '')}")

        if include_constituents:
            try:
                if board_type == "concept":
                    constituents = _with_retries(
                        f"{board_type}:{board_name}: constituents",
                        lambda: fetch_concept_constituents(board_name),
                    )
                else:
                    constituents = _with_retries(
                        f"{board_type}:{board_name}: constituents",
                        lambda: fetch_industry_constituents(board_name),
                    )
                constituents.insert(0, "板块名称", board_name)
                files.append(write_csv(constituents, detail_dir / "constituents" / f"{file_stem}.csv"))
            except Exception as exc:  # pragma: no cover - integration safety
                failures.append(f"{board_type}:{board_name}: constituents failed: {exc}")

        if include_history:
            try:
                if board_type == "concept":
                    history = _with_retries(
                        f"{board_type}:{board_name}: history",
                        lambda: fetch_concept_history(
                            board_name,
                            start_date=settings.history_start,
                            period=settings.history_period,
                            adjust=settings.history_adjust,
                        ),
                    )
                else:
                    history = _with_retries(
                        f"{board_type}:{board_name}: history",
                        lambda: fetch_industry_history(
                            board_name,
                            start_date=settings.history_start,
                            period=settings.history_period,
                            adjust=settings.history_adjust,
                        ),
                    )
                history.insert(0, "板块名称", board_name)
                files.append(write_csv(history, detail_dir / "history" / f"{file_stem}.csv"))
            except Exception as exc:  # pragma: no cover - integration safety
                failures.append(f"{board_type}:{board_name}: history failed: {exc}")

    return files, failures


def _normalize_board_list(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.rename(columns={"name": "板块名称", "code": "板块代码"}).copy()
    return normalized


def _download_ths_board_details(
    boards: pd.DataFrame,
    board_type: str,
    output_dir: Path,
    settings: TechBoardSettings,
    include_history: bool,
    include_constituents: bool,
) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    failures: list[str] = []
    name_column = "板块名称"
    detail_dir = output_dir / f"ths_{board_type}"

    for _, row in boards.iterrows():
        board_name = str(row[name_column])
        file_stem = safe_name(f"{board_name}_{row.get('板块代码', '')}")

        if include_constituents:
            try:
                if board_type == "concept":
                    info = _with_retries(
                        f"ths:{board_type}:{board_name}: info",
                        lambda: fetch_concept_info_ths(board_name),
                    )
                else:
                    info = _with_retries(
                        f"ths:{board_type}:{board_name}: info",
                        lambda: fetch_industry_info_ths(board_name),
                    )
                info.insert(0, "板块名称", board_name)
                files.append(write_csv(info, detail_dir / "info" / f"{file_stem}.csv"))
            except Exception as exc:  # pragma: no cover - integration safety
                failures.append(f"ths:{board_type}:{board_name}: info failed: {exc}")

        if include_history:
            try:
                if board_type == "concept":
                    history = _with_retries(
                        f"ths:{board_type}:{board_name}: history",
                        lambda: fetch_concept_history_ths(board_name, start_date=settings.history_start),
                    )
                else:
                    history = _with_retries(
                        f"ths:{board_type}:{board_name}: history",
                        lambda: fetch_industry_history_ths(board_name, start_date=settings.history_start),
                    )
                history.insert(0, "板块名称", board_name)
                files.append(write_csv(history, detail_dir / "history" / f"{file_stem}.csv"))
            except Exception as exc:  # pragma: no cover - integration safety
                failures.append(f"ths:{board_type}:{board_name}: history failed: {exc}")

    return files, failures


def _fetch_board_lists(settings: TechBoardSettings) -> tuple[pd.DataFrame, pd.DataFrame, list[str], str]:
    failures: list[str] = []
    source = settings.board_source.lower()

    if source == "ths":
        concept = _normalize_board_list(_with_retries("ths concept board list", fetch_concept_boards_ths))
        industry = _normalize_board_list(_with_retries("ths industry board list", fetch_industry_boards_ths))
        return concept, industry, failures, "ths"

    if source == "em":
        concept = _with_retries("em concept board list", fetch_concept_boards)
        industry = _with_retries("em industry board list", fetch_industry_boards)
        return concept, industry, failures, "em"

    try:
        concept = _with_retries("em concept board list", fetch_concept_boards)
        industry = _with_retries("em industry board list", fetch_industry_boards)
        return concept, industry, failures, "em"
    except Exception as exc:  # pragma: no cover - integration safety
        failures.append(f"em board list failed, falling back to ths: {exc}")
        concept = _normalize_board_list(_with_retries("ths concept board list", fetch_concept_boards_ths))
        industry = _normalize_board_list(_with_retries("ths industry board list", fetch_industry_boards_ths))
        return concept, industry, failures, "ths"


def download_tech_board_data(
    settings: TechBoardSettings,
    include_history: bool = True,
    include_constituents: bool = True,
) -> DownloadResult:
    output_dir = timestamped_dir(settings.output_dir, "tech_boards")
    files: list[Path] = []
    failures: list[str] = []

    try:
        concept_all, industry_all, list_failures, actual_source = _fetch_board_lists(settings)
        failures.extend(list_failures)
    except Exception as exc:  # pragma: no cover - integration safety
        failures.append(f"board list failed: {exc}")
        actual_source = settings.board_source
        concept_all = pd.DataFrame()
        industry_all = pd.DataFrame()

    concept_tech = filter_tech_boards(concept_all) if not concept_all.empty else pd.DataFrame()
    industry_tech = filter_tech_boards(industry_all) if not industry_all.empty else pd.DataFrame()

    files.append(write_csv(theme_keywords_frame(), output_dir / "tech_theme_keywords.csv"))
    files.append(write_csv(pd.DataFrame([{"board_source": actual_source}]), output_dir / "board_source.csv"))
    if not concept_all.empty:
        files.append(write_csv(concept_all, output_dir / "all_concept_boards.csv"))
    if not industry_all.empty:
        files.append(write_csv(industry_all, output_dir / "all_industry_boards.csv"))
    if not concept_tech.empty:
        files.append(write_csv(concept_tech, output_dir / "tech_concept_boards.csv"))
    if not industry_tech.empty:
        files.append(write_csv(industry_tech, output_dir / "tech_industry_boards.csv"))

    if actual_source == "ths":
        if not concept_tech.empty:
            detail_files, detail_failures = _download_ths_board_details(
                concept_tech,
                "concept",
                output_dir,
                settings,
                include_history=include_history,
                include_constituents=include_constituents,
            )
            files.extend(detail_files)
            failures.extend(detail_failures)

        if not industry_tech.empty:
            detail_files, detail_failures = _download_ths_board_details(
                industry_tech,
                "industry",
                output_dir,
                settings,
                include_history=include_history,
                include_constituents=include_constituents,
            )
            files.extend(detail_files)
            failures.extend(detail_failures)
    elif not concept_tech.empty:
        detail_files, detail_failures = _download_board_details(
            concept_tech,
            "concept",
            output_dir,
            settings,
            include_history=include_history,
            include_constituents=include_constituents,
        )
        files.extend(detail_files)
        failures.extend(detail_failures)

    if actual_source != "ths" and not industry_tech.empty:
        detail_files, detail_failures = _download_board_details(
            industry_tech,
            "industry",
            output_dir,
            settings,
            include_history=include_history,
            include_constituents=include_constituents,
        )
        files.extend(detail_files)
        failures.extend(detail_failures)

    if failures:
        failure_frame = pd.DataFrame({"failure": failures})
        files.append(write_csv(failure_frame, output_dir / "download_failures.csv"))

    return DownloadResult(
        output_dir=output_dir,
        concept_count=len(concept_tech),
        industry_count=len(industry_tech),
        files=tuple(files),
        failures=tuple(failures),
    )
