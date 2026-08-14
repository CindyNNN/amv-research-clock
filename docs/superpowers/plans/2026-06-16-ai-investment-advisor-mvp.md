# A/H Share AI Investment Advisor MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MVP that can normalize K-line data, rank A-share sectors, analyze a stock, generate a Markdown report, and persist local Agent memory.

**Architecture:** The MVP is a Python package with a small CLI, source adapters, pure analysis functions, Markdown reporting, and fixture-backed unit tests. Network-dependent AKShare calls are isolated behind adapters so core scoring and reporting can be tested offline.

**Tech Stack:** Python 3.11 from Anaconda, pandas, pytest, typer, rich, tomli/tomli-w, optional AKShare, optional mootdx, optional yfinance.

---

## File Structure

- Create: `pyproject.toml` - package metadata, CLI dependencies, test configuration.
- Create: `README.md` - setup, commands, data source notes, and risk disclaimer.
- Create: `CLAUDE.md` - project operating rules loaded by future agent sessions.
- Create: `config/settings.toml` - default local settings.
- Create: `memory/Memory.md` - durable preferences and watchlists.
- Create: `memory/Learning.md` - data quirks and lessons learned.
- Create: `memory/Wiki.md` - shared definitions and symbol conventions.
- Create: `src/ai_invest_advisor/__init__.py` - package version.
- Create: `src/ai_invest_advisor/config.py` - settings loader and directory resolver.
- Create: `src/ai_invest_advisor/models.py` - typed data structures.
- Create: `src/ai_invest_advisor/data/normalization.py` - column mapping and validation.
- Create: `src/ai_invest_advisor/data/akshare_adapter.py` - AKShare source adapter.
- Create: `src/ai_invest_advisor/data/tdx_adapter.py` - mootdx/TongDaXin source adapter.
- Create: `src/ai_invest_advisor/data/cache.py` - CSV cache helpers.
- Create: `src/ai_invest_advisor/analysis/factors.py` - factor calculations.
- Create: `src/ai_invest_advisor/analysis/ranking.py` - sector and stock ranking.
- Create: `src/ai_invest_advisor/reports/markdown.py` - report rendering.
- Create: `src/ai_invest_advisor/memory/store.py` - Markdown memory append/read helpers.
- Create: `src/ai_invest_advisor/cli.py` - command line interface.
- Create: `tests/fixtures.py` - deterministic test data.
- Create: `tests/test_config.py` - config tests.
- Create: `tests/test_normalization.py` - K-line normalization tests.
- Create: `tests/test_factors.py` - factor and scoring tests.
- Create: `tests/test_reports.py` - Markdown report tests.
- Create: `tests/test_memory.py` - memory append tests.
- Create: `tests/test_cli.py` - CLI smoke tests.

## Task 1: Project Skeleton and Local Memory

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `CLAUDE.md`
- Create: `config/settings.toml`
- Create: `memory/Memory.md`
- Create: `memory/Learning.md`
- Create: `memory/Wiki.md`
- Create: `src/ai_invest_advisor/__init__.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Create `tests/test_config.py`:

```python
from pathlib import Path

from ai_invest_advisor.config import load_settings


def test_load_settings_uses_project_defaults():
    settings = load_settings(Path("config/settings.toml"))

    assert settings.data_source == "akshare"
    assert settings.market == "both"
    assert settings.cache_dir.as_posix().endswith("data/cache")
    assert settings.report_dir.as_posix().endswith("reports")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
pytest tests/test_config.py -q
```

Expected: FAIL because `ai_invest_advisor.config` does not exist.

- [ ] **Step 3: Create package metadata**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ai-invest-advisor"
version = "0.1.0"
description = "Local A/H share AI investment research assistant"
requires-python = ">=3.10"
dependencies = [
  "pandas>=2.0",
  "typer>=0.12",
  "rich>=13.0",
  "tomli>=2.0; python_version < '3.11'",
  "tomli-w>=1.0",
]

[project.optional-dependencies]
data = [
  "akshare>=1.16",
  "mootdx>=0.11",
  "yfinance>=0.2",
]
test = [
  "pytest>=8.0",
]

[project.scripts]
ai-invest-advisor = "ai_invest_advisor.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Create default settings**

Create `config/settings.toml`:

```toml
data_source = "akshare"
market = "both"
cache_dir = "data/cache"
report_dir = "reports"
tdx_path = ""
```

- [ ] **Step 5: Create memory files**

Create `CLAUDE.md`:

```markdown
# AI Investment Advisor Rules

This project builds a local research assistant for A shares and Hong Kong shares.

Rules:

- Treat all output as research support, not financial advice.
- Never place trades or request brokerage credentials.
- Always show data source, timestamp, and risk notes.
- Prefer explainable factors over opaque predictions.
- Record durable user preferences in `memory/Memory.md`.
- Record data quirks and mistakes in `memory/Learning.md`.
- Record shared definitions in `memory/Wiki.md`.
```

Create `memory/Memory.md`:

```markdown
# Memory

## User Preferences

- Markets: A shares and Hong Kong shares.
- First data preference: public data, with TongDaXin local data as an optional adapter.
- Output preference: explainable research views with risk notes.

## Watchlists

- No persistent watchlist has been added yet.
```

Create `memory/Learning.md`:

```markdown
# Learning

## Data Notes

- On 2026-06-16, common TongDaXin paths were checked and were not found.
- AKShare is the MVP default source because it covers K-line and board data.
```

Create `memory/Wiki.md`:

```markdown
# Wiki

## Recommendation Labels

- `watch`: worth tracking, but not a buy instruction.
- `avoid_for_now`: risk or trend is unfavorable.
- `strong_sector_watchlist`: sector is strong and deserves deeper review.
- `pullback_wait`: trend is extended and a better entry may require patience.
- `risk_control_required`: volatility, drawdown, or data risk is elevated.

## Disclaimer

This is research support, not financial advice.
```

- [ ] **Step 6: Implement settings loader**

Create `src/ai_invest_advisor/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/ai_invest_advisor/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


@dataclass(frozen=True)
class Settings:
    data_source: str
    market: str
    cache_dir: Path
    report_dir: Path
    tdx_path: Path | None


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
    tdx_raw = str(data.get("tdx_path", "")).strip()

    return Settings(
        data_source=str(data.get("data_source", "akshare")),
        market=str(data.get("market", "both")),
        cache_dir=_resolve_path(str(data.get("cache_dir", "data/cache")), base_dir),
        report_dir=_resolve_path(str(data.get("report_dir", "reports")), base_dir),
        tdx_path=_resolve_path(tdx_raw, base_dir) if tdx_raw else None,
    )
```

- [ ] **Step 7: Run the test to verify it passes**

Run:

```powershell
pytest tests/test_config.py -q
```

Expected: PASS.

## Task 2: Data Models and K-Line Normalization

**Files:**
- Create: `src/ai_invest_advisor/models.py`
- Create: `src/ai_invest_advisor/data/__init__.py`
- Create: `src/ai_invest_advisor/data/normalization.py`
- Create: `tests/fixtures.py`
- Create: `tests/test_normalization.py`

- [ ] **Step 1: Write failing normalization tests**

Create `tests/fixtures.py`:

```python
import pandas as pd


def akshare_kline_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"日期": "2026-06-10", "开盘": 10.0, "收盘": 10.5, "最高": 10.8, "最低": 9.9, "成交量": 10000, "成交额": 105000, "换手率": 1.2, "涨跌幅": 2.0},
            {"日期": "2026-06-11", "开盘": 10.5, "收盘": 10.2, "最高": 10.7, "最低": 10.1, "成交量": 12000, "成交额": 124000, "换手率": 1.4, "涨跌幅": -2.86},
            {"日期": "2026-06-12", "开盘": 10.2, "收盘": 10.9, "最高": 11.0, "最低": 10.2, "成交量": 18000, "成交额": 190000, "换手率": 2.1, "涨跌幅": 6.86},
        ]
    )
```

Create `tests/test_normalization.py`:

```python
import pytest

from ai_invest_advisor.data.normalization import normalize_kline
from tests.fixtures import akshare_kline_fixture


def test_normalize_akshare_kline_columns():
    normalized = normalize_kline(akshare_kline_fixture(), source="akshare")

    assert list(normalized.columns) == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover",
        "pct_change",
        "source",
    ]
    assert normalized.iloc[0]["close"] == 10.5
    assert normalized.iloc[0]["source"] == "akshare"


def test_normalize_kline_requires_close_column():
    with pytest.raises(ValueError, match="missing required columns"):
        normalize_kline(akshare_kline_fixture().drop(columns=["收盘"]), source="akshare")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_normalization.py -q
```

Expected: FAIL because normalization module does not exist.

- [ ] **Step 3: Implement models and normalization**

Create `src/ai_invest_advisor/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    label: str
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectorView:
    name: str
    code: str
    pct_change: float
    turnover: float | None
    rising_count: int | None
    falling_count: int | None
    leader: str | None
    leader_pct_change: float | None
    score: float
    reasons: list[str]
    risk_flags: list[str]


@dataclass(frozen=True)
class StockView:
    symbol: str
    market: str
    close: float
    score: float
    label: str
    reasons: list[str]
    risk_flags: list[str]
```

Create `src/ai_invest_advisor/data/__init__.py`:

```python
"""Data adapters and normalization helpers."""
```

Create `src/ai_invest_advisor/data/normalization.py`:

```python
from __future__ import annotations

import pandas as pd


AKSHARE_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover",
    "涨跌幅": "pct_change",
}

TDX_COLUMN_MAP = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
}

REQUIRED_KLINE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
NORMALIZED_KLINE_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover",
    "pct_change",
    "source",
]


def normalize_kline(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    column_map = AKSHARE_COLUMN_MAP if source == "akshare" else TDX_COLUMN_MAP
    normalized = frame.rename(columns=column_map).copy()

    missing = [column for column in REQUIRED_KLINE_COLUMNS if column not in normalized.columns]
    if missing:
        raise ValueError(f"K-line data is missing required columns: {missing}")

    for optional_column in ["amount", "turnover", "pct_change"]:
        if optional_column not in normalized.columns:
            normalized[optional_column] = pd.NA

    normalized["date"] = pd.to_datetime(normalized["date"])
    normalized["source"] = source

    numeric_columns = ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_change"]
    for column in numeric_columns:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized = normalized[NORMALIZED_KLINE_COLUMNS].sort_values("date").reset_index(drop=True)
    return normalized
```

- [ ] **Step 4: Run normalization tests**

Run:

```powershell
pytest tests/test_normalization.py -q
```

Expected: PASS.

## Task 3: Analysis Factors and Ranking

**Files:**
- Create: `src/ai_invest_advisor/analysis/__init__.py`
- Create: `src/ai_invest_advisor/analysis/factors.py`
- Create: `src/ai_invest_advisor/analysis/ranking.py`
- Create: `tests/test_factors.py`

- [ ] **Step 1: Write failing factor tests**

Create `tests/test_factors.py`:

```python
import pandas as pd

from ai_invest_advisor.analysis.factors import analyze_stock_kline, calculate_factor_table
from ai_invest_advisor.analysis.ranking import rank_sectors
from ai_invest_advisor.data.normalization import normalize_kline
from tests.fixtures import akshare_kline_fixture


def test_calculate_factor_table_adds_momentum_and_volume_ratio():
    frame = normalize_kline(akshare_kline_fixture(), source="akshare")

    factors = calculate_factor_table(frame)

    assert "ma_5" in factors.columns
    assert "ret_5" in factors.columns
    assert "volume_ratio_20" in factors.columns
    assert factors.iloc[-1]["close"] == 10.9


def test_analyze_stock_kline_returns_explainable_score():
    frame = normalize_kline(akshare_kline_fixture(), source="akshare")

    view = analyze_stock_kline(frame, symbol="600036", market="a_share")

    assert view.symbol == "600036"
    assert view.market == "a_share"
    assert view.score > 0
    assert view.reasons


def test_rank_sectors_sorts_by_score_descending():
    sectors = pd.DataFrame(
        [
            {"板块名称": "低分板块", "板块代码": "BK0001", "涨跌幅": 0.5, "换手率": 1.0, "上涨家数": 3, "下跌家数": 5, "领涨股票": "A", "领涨股票-涨跌幅": 1.0},
            {"板块名称": "高分板块", "板块代码": "BK0002", "涨跌幅": 3.0, "换手率": 2.0, "上涨家数": 8, "下跌家数": 2, "领涨股票": "B", "领涨股票-涨跌幅": 7.0},
        ]
    )

    ranked = rank_sectors(sectors, top=2)

    assert ranked[0].name == "高分板块"
    assert ranked[0].score > ranked[1].score
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_factors.py -q
```

Expected: FAIL because analysis modules do not exist.

- [ ] **Step 3: Implement factor calculations**

Create `src/ai_invest_advisor/analysis/__init__.py`:

```python
"""Explainable market analysis functions."""
```

Create `src/ai_invest_advisor/analysis/factors.py`:

```python
from __future__ import annotations

import pandas as pd

from ai_invest_advisor.models import StockView


def calculate_factor_table(kline: pd.DataFrame) -> pd.DataFrame:
    frame = kline.copy()
    frame["ma_5"] = frame["close"].rolling(5, min_periods=1).mean()
    frame["ma_20"] = frame["close"].rolling(20, min_periods=1).mean()
    frame["ma_60"] = frame["close"].rolling(60, min_periods=1).mean()
    frame["ret_5"] = frame["close"].pct_change(5).fillna(frame["close"].pct_change().fillna(0))
    frame["ret_20"] = frame["close"].pct_change(20).fillna(frame["ret_5"])
    frame["ret_60"] = frame["close"].pct_change(60).fillna(frame["ret_20"])
    frame["volume_ma_20"] = frame["volume"].rolling(20, min_periods=1).mean()
    frame["volume_ratio_20"] = frame["volume"] / frame["volume_ma_20"].replace(0, pd.NA)
    frame["drawdown_20"] = frame["close"] / frame["close"].rolling(20, min_periods=1).max() - 1
    frame["volatility_20"] = frame["close"].pct_change().rolling(20, min_periods=2).std().fillna(0)
    return frame


def analyze_stock_kline(kline: pd.DataFrame, symbol: str, market: str) -> StockView:
    factors = calculate_factor_table(kline)
    latest = factors.iloc[-1]
    score = 0.0
    reasons: list[str] = []
    risk_flags: list[str] = []

    if latest["close"] >= latest["ma_20"]:
        score += 25
        reasons.append("Close is above the 20-day moving average.")
    else:
        risk_flags.append("Close is below the 20-day moving average.")

    if latest["ret_5"] > 0:
        score += 20
        reasons.append("Recent 5-day momentum is positive.")

    if latest["volume_ratio_20"] >= 1.2:
        score += 15
        reasons.append("Volume is above its 20-day average.")

    if latest["drawdown_20"] > -0.08:
        score += 15
        reasons.append("Recent drawdown is controlled.")
    else:
        risk_flags.append("20-day drawdown is deeper than 8%.")

    if latest["volatility_20"] <= 0.04:
        score += 10
        reasons.append("Recent volatility is moderate.")
    else:
        risk_flags.append("Recent volatility is elevated.")

    if len(kline) < 20:
        risk_flags.append("Data window is shorter than 20 trading days.")

    label = "watch" if score >= 45 else "avoid_for_now"
    if risk_flags and score >= 45:
        label = "risk_control_required"

    return StockView(
        symbol=symbol,
        market=market,
        close=float(latest["close"]),
        score=round(float(score), 2),
        label=label,
        reasons=reasons,
        risk_flags=risk_flags,
    )
```

- [ ] **Step 4: Implement sector ranking**

Create `src/ai_invest_advisor/analysis/ranking.py`:

```python
from __future__ import annotations

import pandas as pd

from ai_invest_advisor.models import SectorView


def _value(row: pd.Series, column: str, default: float = 0.0) -> float:
    value = row.get(column, default)
    if pd.isna(value):
        return default
    return float(value)


def rank_sectors(frame: pd.DataFrame, top: int = 10) -> list[SectorView]:
    views: list[SectorView] = []
    for _, row in frame.iterrows():
        pct_change = _value(row, "涨跌幅")
        turnover = _value(row, "换手率")
        rising = int(_value(row, "上涨家数"))
        falling = int(_value(row, "下跌家数"))
        breadth = rising / max(rising + falling, 1)
        leader_pct = _value(row, "领涨股票-涨跌幅")
        score = pct_change * 12 + turnover * 2 + breadth * 30 + leader_pct * 3

        reasons = [
            f"Board change is {pct_change:.2f}%.",
            f"Breadth is {breadth:.0%}.",
        ]
        risk_flags: list[str] = []
        if pct_change >= 6:
            risk_flags.append("Board may be extended after a sharp move.")
        if rising + falling == 0:
            risk_flags.append("Breadth data is unavailable.")

        views.append(
            SectorView(
                name=str(row.get("板块名称", "")),
                code=str(row.get("板块代码", "")),
                pct_change=pct_change,
                turnover=turnover,
                rising_count=rising,
                falling_count=falling,
                leader=str(row.get("领涨股票", "")) or None,
                leader_pct_change=leader_pct,
                score=round(float(score), 2),
                reasons=reasons,
                risk_flags=risk_flags,
            )
        )

    return sorted(views, key=lambda item: item.score, reverse=True)[:top]
```

- [ ] **Step 5: Run factor tests**

Run:

```powershell
pytest tests/test_factors.py -q
```

Expected: PASS.

## Task 4: Data Adapters and Cache

**Files:**
- Create: `src/ai_invest_advisor/data/cache.py`
- Create: `src/ai_invest_advisor/data/akshare_adapter.py`
- Create: `src/ai_invest_advisor/data/tdx_adapter.py`
- Create: `tests/test_data_adapters.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_data_adapters.py`:

```python
from pathlib import Path

import pandas as pd
import pytest

from ai_invest_advisor.data.cache import cache_frame, load_cached_frame
from ai_invest_advisor.data.tdx_adapter import validate_tdx_path


def test_cache_round_trip(tmp_path: Path):
    frame = pd.DataFrame([{"date": "2026-06-16", "close": 10.0}])
    path = cache_frame(frame, tmp_path, "sample")

    loaded = load_cached_frame(path)

    assert path.exists()
    assert loaded.iloc[0]["close"] == 10.0


def test_validate_tdx_path_rejects_missing_path(tmp_path: Path):
    missing = tmp_path / "missing_tdx"

    with pytest.raises(FileNotFoundError, match="TongDaXin path does not exist"):
        validate_tdx_path(missing)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_data_adapters.py -q
```

Expected: FAIL because adapter modules do not exist.

- [ ] **Step 3: Implement cache helpers**

Create `src/ai_invest_advisor/data/cache.py`:

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd


def cache_frame(frame: pd.DataFrame, cache_dir: Path, name: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_cached_frame(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
```

- [ ] **Step 4: Implement AKShare adapter**

Create `src/ai_invest_advisor/data/akshare_adapter.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd

from ai_invest_advisor.data.normalization import normalize_kline


def _akshare_module():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare is not installed. Install optional data dependencies first.") from exc
    return ak


def fetch_a_share_kline(symbol: str, start: str = "20200101", end: str | None = None, adjust: str = "qfq") -> pd.DataFrame:
    ak = _akshare_module()
    end_date = end or date.today().strftime("%Y%m%d")
    raw = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end_date, adjust=adjust)
    return normalize_kline(raw, source="akshare")


def fetch_concept_boards() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_concept_name_em()


def fetch_industry_boards() -> pd.DataFrame:
    ak = _akshare_module()
    return ak.stock_board_industry_name_em()
```

- [ ] **Step 5: Implement TDX adapter**

Create `src/ai_invest_advisor/data/tdx_adapter.py`:

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ai_invest_advisor.data.normalization import normalize_kline


def validate_tdx_path(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"TongDaXin path does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"TongDaXin path is not a directory: {path}")
    return path


def fetch_tdx_daily(symbol: str, tdx_path: Path) -> pd.DataFrame:
    validate_tdx_path(tdx_path)
    try:
        from mootdx.reader import Reader
    except ImportError as exc:
        raise RuntimeError("mootdx is not installed. Install optional data dependencies first.") from exc

    reader = Reader.factory(market="std", tdxdir=str(tdx_path))
    raw = reader.daily(symbol=symbol)
    return normalize_kline(raw.reset_index(), source="tdx")
```

- [ ] **Step 6: Run adapter tests**

Run:

```powershell
pytest tests/test_data_adapters.py -q
```

Expected: PASS.

## Task 5: Markdown Reports and Memory Store

**Files:**
- Create: `src/ai_invest_advisor/reports/__init__.py`
- Create: `src/ai_invest_advisor/reports/markdown.py`
- Create: `src/ai_invest_advisor/memory/__init__.py`
- Create: `src/ai_invest_advisor/memory/store.py`
- Create: `tests/test_reports.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write failing report and memory tests**

Create `tests/test_reports.py`:

```python
from ai_invest_advisor.models import SectorView, StockView
from ai_invest_advisor.reports.markdown import render_market_report, render_stock_report


def test_render_stock_report_includes_disclaimer():
    view = StockView(symbol="600036", market="a_share", close=10.9, score=65, label="watch", reasons=["Trend is positive."], risk_flags=["Data window is short."])

    report = render_stock_report(view)

    assert "# Stock Analysis: 600036" in report
    assert "Trend is positive." in report
    assert "This is research support, not financial advice." in report


def test_render_market_report_lists_sectors():
    sector = SectorView(name="AI概念", code="BK0001", pct_change=2.0, turnover=1.5, rising_count=10, falling_count=2, leader="测试股票", leader_pct_change=6.0, score=80, reasons=["Strong breadth."], risk_flags=[])

    report = render_market_report([sector], [], data_source="fixture")

    assert "# Market Report" in report
    assert "AI概念" in report
    assert "fixture" in report
```

Create `tests/test_memory.py`:

```python
from pathlib import Path

from ai_invest_advisor.memory.store import append_memory_note, read_memory_file


def test_append_memory_note_adds_heading(tmp_path: Path):
    path = tmp_path / "Memory.md"
    path.write_text("# Memory\n", encoding="utf-8")

    append_memory_note(path, "Session", "Reviewed AI sector strength.")

    content = read_memory_file(path)
    assert "## Session" in content
    assert "Reviewed AI sector strength." in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_reports.py tests/test_memory.py -q
```

Expected: FAIL because report and memory modules do not exist.

- [ ] **Step 3: Implement Markdown report rendering**

Create `src/ai_invest_advisor/reports/__init__.py`:

```python
"""Markdown report renderers."""
```

Create `src/ai_invest_advisor/reports/markdown.py`:

```python
from __future__ import annotations

from datetime import datetime

from ai_invest_advisor.models import SectorView, StockView

DISCLAIMER = "This is research support, not financial advice."


def _bullet_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None"


def render_stock_report(view: StockView) -> str:
    return f"""# Stock Analysis: {view.symbol}

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Market: {view.market}
Latest close: {view.close:.2f}
Score: {view.score:.2f}
Label: {view.label}

## Reasons

{_bullet_lines(view.reasons)}

## Risk Flags

{_bullet_lines(view.risk_flags)}

## Disclaimer

{DISCLAIMER}
"""


def _sector_table(sectors: list[SectorView]) -> str:
    if not sectors:
        return "No sector data available."
    lines = ["| Rank | Name | Code | Change | Score | Leader |", "| --- | --- | --- | ---: | ---: | --- |"]
    for index, sector in enumerate(sectors, start=1):
        leader = sector.leader or ""
        lines.append(f"| {index} | {sector.name} | {sector.code} | {sector.pct_change:.2f}% | {sector.score:.2f} | {leader} |")
    return "\n".join(lines)


def render_market_report(concept_sectors: list[SectorView], industry_sectors: list[SectorView], data_source: str) -> str:
    return f"""# Market Report

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Data source: {data_source}

## Top Concept Boards

{_sector_table(concept_sectors)}

## Top Industry Boards

{_sector_table(industry_sectors)}

## Disclaimer

{DISCLAIMER}
"""
```

- [ ] **Step 4: Implement memory helpers**

Create `src/ai_invest_advisor/memory/__init__.py`:

```python
"""Local Markdown memory helpers."""
```

Create `src/ai_invest_advisor/memory/store.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def read_memory_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def append_memory_note(path: Path, heading: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem}\n"
    entry = f"\n## {heading}\n\nTime: {timestamp}\n\n{body.strip()}\n"
    path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
```

- [ ] **Step 5: Run report and memory tests**

Run:

```powershell
pytest tests/test_reports.py tests/test_memory.py -q
```

Expected: PASS.

## Task 6: CLI Commands

**Files:**
- Create: `src/ai_invest_advisor/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from ai_invest_advisor.cli import app


def test_cli_version():
    runner = CliRunner()

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_memory_appends_note(tmp_path):
    runner = CliRunner()
    path = tmp_path / "Memory.md"

    result = runner.invoke(app, ["memory", "--path", str(path), "--heading", "Session", "--note", "Test note"])

    assert result.exit_code == 0
    assert "Memory updated" in result.output
    assert "Test note" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_cli.py -q
```

Expected: FAIL because CLI module does not exist.

- [ ] **Step 3: Implement CLI**

Create `src/ai_invest_advisor/cli.py`:

```python
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ai_invest_advisor import __version__
from ai_invest_advisor.analysis.factors import analyze_stock_kline
from ai_invest_advisor.analysis.ranking import rank_sectors
from ai_invest_advisor.config import load_settings
from ai_invest_advisor.data.akshare_adapter import fetch_a_share_kline, fetch_concept_boards, fetch_industry_boards
from ai_invest_advisor.memory.store import append_memory_note
from ai_invest_advisor.reports.markdown import render_market_report, render_stock_report

app = typer.Typer(help="Local A/H share AI investment research assistant.")
console = Console()


@app.command()
def version() -> None:
    console.print(f"ai-invest-advisor {__version__}")


@app.command()
def memory(
    path: Path = typer.Option(Path("memory/Memory.md"), help="Memory file path."),
    heading: str = typer.Option("Session", help="Entry heading."),
    note: str = typer.Option(..., help="Memory note body."),
) -> None:
    append_memory_note(path, heading, note)
    console.print(f"Memory updated: {path}")


@app.command()
def sector(
    top: int = typer.Option(10, min=1, max=50, help="Number of boards to display."),
    settings_path: Path = typer.Option(Path("config/settings.toml"), help="Settings file path."),
) -> None:
    settings = load_settings(settings_path)
    concept = rank_sectors(fetch_concept_boards(), top=top)
    industry = rank_sectors(fetch_industry_boards(), top=top)
    report = render_market_report(concept, industry, data_source=settings.data_source)
    console.print(report)


@app.command()
def stock(
    symbol: str = typer.Argument(..., help="Stock symbol, such as 600036."),
    market: str = typer.Option("a_share", help="Market name."),
    start: str = typer.Option("20200101", help="Start date in YYYYMMDD."),
) -> None:
    if market != "a_share":
        raise typer.BadParameter("The MVP CLI currently supports direct stock command for a_share symbols.")
    frame = fetch_a_share_kline(symbol=symbol, start=start)
    view = analyze_stock_kline(frame, symbol=symbol, market=market)
    console.print(render_stock_report(view))


@app.command()
def report(
    output: Path = typer.Option(Path("reports/market_report.md"), help="Output Markdown path."),
    top: int = typer.Option(10, min=1, max=50, help="Number of boards to include."),
    settings_path: Path = typer.Option(Path("config/settings.toml"), help="Settings file path."),
) -> None:
    settings = load_settings(settings_path)
    concept = rank_sectors(fetch_concept_boards(), top=top)
    industry = rank_sectors(fetch_industry_boards(), top=top)
    rendered = render_market_report(concept, industry, data_source=settings.data_source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    console.print(f"Report written: {output}")
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
pytest tests/test_cli.py -q
```

Expected: PASS.

## Task 7: README and Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Create README**

Create `README.md`:

```markdown
# A/H Share AI Investment Advisor

Local research assistant for A shares and Hong Kong shares. It reads market data, ranks boards, analyzes K-line trends, generates Markdown reports, and keeps local Markdown memory.

This is research support, not financial advice.

## Setup

```powershell
uv venv
uv pip install -e ".[test]"
```

Optional live data:

```powershell
uv pip install -e ".[data,test]"
```

## Commands

```powershell
python -m ai_invest_advisor.cli version
python -m ai_invest_advisor.cli memory --heading Session --note "Reviewed today's AI board strength."
python -m ai_invest_advisor.cli sector --top 10
python -m ai_invest_advisor.cli stock 600036 --market a_share
python -m ai_invest_advisor.cli report --output reports/market_report.md
```

## Data Sources

- AKShare is the default public data source.
- mootdx can read TongDaXin local data after `tdx_path` is configured in `config/settings.toml`.
- Hong Kong share support starts with public data adapters and can be expanded after the A-share flow is stable.

## Memory Files

- `CLAUDE.md`: project rules.
- `memory/Memory.md`: durable user preferences.
- `memory/Learning.md`: data quirks and lessons learned.
- `memory/Wiki.md`: shared definitions.
```

- [ ] **Step 2: Run full offline tests**

Run:

```powershell
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run local CLI smoke commands**

Run:

```powershell
python -m ai_invest_advisor.cli version
python -m ai_invest_advisor.cli memory --heading Session --note "MVP smoke test."
```

Expected:

- Version command prints `ai-invest-advisor 0.1.0`.
- Memory command prints `Memory updated: memory\Memory.md`.

- [ ] **Step 4: Run optional network command after installing data extras**

Run:

```powershell
python -m ai_invest_advisor.cli sector --top 5
```

Expected: A Markdown market report prints concept and industry board tables. If AKShare is not installed or network access is blocked, the command prints a clear installation or fetch error.

## Self-Review

Spec coverage:

- Data source defaults are covered by Tasks 1, 2, and 4.
- Explainable stock and sector analysis is covered by Task 3.
- Markdown reports are covered by Task 5.
- Memory files and memory append behavior are covered by Tasks 1 and 5.
- CLI acceptance paths are covered by Task 6.
- Offline tests and smoke verification are covered by Task 7.

Placeholder scan:

- The plan contains no `TBD` markers.
- Every code-changing step includes concrete file content.

Type consistency:

- `StockView` and `SectorView` fields are defined in Task 2 and reused consistently in Tasks 3, 5, and 6.
- Normalized K-line columns are defined once in Task 2 and used by factor code in Task 3.

