"""Auto-refresh Compass local cache and export 0AMV with indicators.

Compass has no public quote API. This module:
1) starts WavMain.exe if needed (requires one-time auto-login in the client),
2) if cache is still stale, quietly types ``0AMV`` to open the chart,
3) if still stale, UI-clicks 下载 → 接收最新 → 开始,
4) waits until day.vdat last bar is fresh enough,
5) exports OHLCV + explainable indicators for research use.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.compass_bridge import (
    DEFAULT_COMPASS_ROOT,
    CompassBridgeError,
    cache_status,
    read_0amv_daily,
)
from ai_invest_advisor.compass_ui import (
    browse_0amv_symbol,
    read_download_progress,
    trigger_after_close_download,
    wait_for_main_hwnd,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "data" / "compass"
DEFAULT_EXE_NAME = "WavMain.exe"
SYNC_LOCK_PATH = DEFAULT_OUT_DIR / ".sync.lock"
SYNC_LOCK_STALE_SECONDS = 45 * 60


class CompassSyncError(RuntimeError):
    pass


class _SyncLock:
    """Prevent overlapping sync jobs from repeatedly launching Compass."""

    def __init__(self, path: Path = SYNC_LOCK_PATH) -> None:
        self.path = path
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if self.path.exists():
            try:
                age = now - self.path.stat().st_mtime
            except OSError:
                age = 0.0
            if age < SYNC_LOCK_STALE_SECONDS:
                return False
        try:
            self.path.write_text(f"pid={os.getpid()}\nstarted={now}\n", encoding="utf-8")
            self.acquired = True
            return True
        except OSError:
            return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        self.acquired = False

    def __enter__(self) -> "_SyncLock":
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    launched: bool
    waited_seconds: float
    last_bar_date: str | None
    expected_as_of: str
    out_daily_csv: str | None
    out_indicator_csv: str | None
    out_snapshot_json: str | None
    message: str
    status: dict[str, Any]
    closed: bool = False


def _parse_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.to_datetime(value).date()


def last_weekday_on_or_before(day: date) -> date:
    cur = day
    while cur.weekday() >= 5:  # Sat/Sun
        cur -= timedelta(days=1)
    return cur


def expected_as_of_date(now: datetime | None = None) -> date:
    """Approximate last completed A-share session date (weekday heuristic)."""
    now = now or datetime.now()
    day = now.date()
    # Before 15:30, previous weekday is the expected completed bar.
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        day -= timedelta(days=1)
    return last_weekday_on_or_before(day)


def is_compass_running(exe_name: str = DEFAULT_EXE_NAME) -> bool:
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return False
    out = (completed.stdout or "") + (completed.stderr or "")
    return exe_name.lower() in out.lower()


def launch_compass(compass_root: Path | str = DEFAULT_COMPASS_ROOT) -> bool:
    """Start WavMain.exe if not already running. Returns True if a new process was started."""
    root = Path(compass_root)
    exe = root / DEFAULT_EXE_NAME
    if not exe.exists():
        raise CompassSyncError(f"找不到指南针主程序: {exe}")
    if is_compass_running():
        return False
    subprocess.Popen(
        [str(exe)],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def close_compass(
    exe_name: str = DEFAULT_EXE_NAME,
    *,
    force: bool = True,
    wait_seconds: float = 3.0,
) -> bool:
    """Close WavMain.exe after sync. Returns True if it is no longer running.

    Uses ``taskkill``. Prefer calling this only for processes we started, or
    when the caller explicitly asks to shut the client down after unattended sync.
    """
    if not is_compass_running(exe_name):
        return True
    args = ["taskkill", "/IM", exe_name]
    if force:
        args.append("/F")
    try:
        subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
        )
    except OSError as exc:
        raise CompassSyncError(f"无法关闭指南针进程 {exe_name}: {exc}") from exc
    deadline = time.time() + max(0.5, wait_seconds)
    while time.time() < deadline:
        if not is_compass_running(exe_name):
            return True
        time.sleep(0.25)
    return not is_compass_running(exe_name)


def read_last_bar_date(compass_root: Path | str = DEFAULT_COMPASS_ROOT) -> date | None:
    try:
        frame = read_0amv_daily(compass_root=compass_root)
    except CompassBridgeError:
        return None
    except OSError:
        # day.vdat is often locked while Compass is writing a download.
        return None
    if frame.empty:
        return None
    value = frame["date"].iloc[-1]
    return value if isinstance(value, date) else pd.to_datetime(value).date()


def wait_for_fresh_daily(
    *,
    compass_root: Path | str = DEFAULT_COMPASS_ROOT,
    as_of: date | str | None = None,
    timeout_seconds: int = 180,
    poll_seconds: float = 5.0,
) -> tuple[bool, date | None, float]:
    """Poll local day.vdat until last bar date >= as_of or timeout."""
    target = _parse_date(as_of) or expected_as_of_date()
    started = time.time()
    last: date | None = None
    while True:
        last = read_last_bar_date(compass_root)
        if last is not None and last >= target:
            return True, last, time.time() - started
        if time.time() - started >= timeout_seconds:
            return False, last, time.time() - started
        progress = read_download_progress()
        if progress:
            # Keep waiting while a download dialog reports progress.
            pass
        time.sleep(poll_seconds)


def compute_0amv_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    """Explainable indicators on 0AMV close (research support, not advice)."""
    frame = daily.copy().sort_values("date").reset_index(drop=True)
    close = frame["close"].astype(float)
    frame["ma5"] = close.rolling(5, min_periods=1).mean()
    frame["ma10"] = close.rolling(10, min_periods=1).mean()
    frame["ma20"] = close.rolling(20, min_periods=1).mean()
    frame["ma5_slope"] = frame["ma5"].diff()
    frame["ma5_slope_3d"] = frame["ma5"].diff(3)
    frame["above_ma5"] = close > frame["ma5"]
    frame["above_ma20"] = close > frame["ma20"]
    frame["ma5_above_ma20"] = frame["ma5"] > frame["ma20"]
    # Simple regime label used by Compass docs: rising / falling / flattening MA5
    slope = frame["ma5_slope"]
    frame["ma5_regime"] = "flat"
    frame.loc[slope > 0, "ma5_regime"] = "up"
    frame.loc[slope < 0, "ma5_regime"] = "down"
    # Soften near-zero slope
    eps = close.rolling(20, min_periods=5).std().fillna(0) * 0.02
    near_zero = slope.abs() <= eps
    frame.loc[near_zero, "ma5_regime"] = "flat"
    frame["ret_1d"] = close.pct_change()
    frame["ret_5d"] = close.pct_change(5)
    return frame


def latest_indicator_snapshot(indicator_frame: pd.DataFrame) -> dict[str, Any]:
    if indicator_frame.empty:
        raise CompassSyncError("指标表为空")
    row = indicator_frame.iloc[-1]
    return {
        "symbol": "0AMV",
        "name": "活跃市值",
        "as_of": str(row["date"]),
        "close": float(row["close"]),
        "ma5": float(row["ma5"]),
        "ma10": float(row["ma10"]),
        "ma20": float(row["ma20"]),
        "ma5_slope": float(row["ma5_slope"]) if pd.notna(row["ma5_slope"]) else None,
        "ma5_slope_3d": float(row["ma5_slope_3d"]) if pd.notna(row["ma5_slope_3d"]) else None,
        "above_ma5": bool(row["above_ma5"]),
        "above_ma20": bool(row["above_ma20"]),
        "ma5_above_ma20": bool(row["ma5_above_ma20"]),
        "ma5_regime": str(row["ma5_regime"]),
        "ret_1d": float(row["ret_1d"]) if pd.notna(row["ret_1d"]) else None,
        "ret_5d": float(row["ret_5d"]) if pd.notna(row["ret_5d"]) else None,
        "source": "compass_local_cache",
        "disclaimer": "研究辅助，非投资建议；0AMV 为指南针专有指数，以本地缓存为准。",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def export_0amv_bundle(
    *,
    compass_root: Path | str = DEFAULT_COMPASS_ROOT,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    start: date | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    daily = read_0amv_daily(compass_root=compass_root, start=start)
    indicators = compute_0amv_indicators(daily)
    snapshot = latest_indicator_snapshot(indicators)

    daily_path = out / "0amv_daily.csv"
    indicator_path = out / "0amv_indicators.csv"
    snapshot_path = out / "0amv_latest.json"

    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    indicators.to_csv(indicator_path, index=False, encoding="utf-8-sig")
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return daily, indicators, snapshot, daily_path, indicator_path, snapshot_path


def sync_and_export(
    *,
    compass_root: Path | str = DEFAULT_COMPASS_ROOT,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    as_of: date | str | None = None,
    launch: bool = True,
    wait: bool = True,
    timeout_seconds: int = 300,
    poll_seconds: float = 5.0,
    start: date | str | None = "2018-01-01",
    allow_stale: bool = False,
    close_after: str = "if_launched",
    ui_browse: bool = True,
    ui_download: bool = True,
    passive_wait_seconds: float = 15.0,
    browse_wait_seconds: float = 45.0,
    prompt_manual: bool = True,
) -> SyncResult:
    """Launch (optional), type 0AMV, optionally download once, wait, export.

    Refresh order when cache is stale:
    1. short passive wait after launch
    2. type ``0AMV`` (light) then wait ``browse_wait_seconds``
    3. one quiet 下载→接收最新 if still stale
    4. manual popup if still stale and ``prompt_manual``

    ``close_after``: ``never`` | ``if_launched`` | ``always``

    Overlapping syncs are rejected via a lock file so scheduled tasks do not
    keep relaunching Compass and stealing the desktop.
    """
    if close_after not in {"never", "if_launched", "always"}:
        raise CompassSyncError(f"unsupported close_after: {close_after}")

    lock = _SyncLock()
    # allow_stale / no-wait exports should not block behind an active sync lock
    needs_lock = bool(wait or ui_browse or ui_download or launch)
    if needs_lock and not allow_stale:
        if not lock.acquire():
            last = read_last_bar_date(compass_root)
            return SyncResult(
                ok=False,
                launched=False,
                waited_seconds=0.0,
                last_bar_date=str(last) if last else None,
                expected_as_of=str(_parse_date(as_of) or expected_as_of_date()),
                out_daily_csv=None,
                out_indicator_csv=None,
                out_snapshot_json=None,
                message=(
                    "另一同步任务正在进行（已跳过，避免反复开启指南针抢占桌面）。"
                    "请等待当前任务结束，或删除 data/compass/.sync.lock 后重试。"
                ),
                status={"lock": str(SYNC_LOCK_PATH), "cache": cache_status(compass_root)},
                closed=False,
            )

    try:
        return _sync_and_export_unlocked(
            compass_root=compass_root,
            out_dir=out_dir,
            as_of=as_of,
            launch=launch,
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            start=start,
            allow_stale=allow_stale,
            close_after=close_after,
            ui_browse=ui_browse,
            ui_download=ui_download,
            passive_wait_seconds=passive_wait_seconds,
            browse_wait_seconds=browse_wait_seconds,
            prompt_manual=prompt_manual,
        )
    finally:
        lock.release()


def _sync_and_export_unlocked(
    *,
    compass_root: Path | str,
    out_dir: Path | str,
    as_of: date | str | None,
    launch: bool,
    wait: bool,
    timeout_seconds: int,
    poll_seconds: float,
    start: date | str | None,
    allow_stale: bool,
    close_after: str,
    ui_browse: bool,
    ui_download: bool,
    passive_wait_seconds: float,
    browse_wait_seconds: float,
    prompt_manual: bool,
) -> SyncResult:
    root = Path(compass_root)
    target = _parse_date(as_of) or expected_as_of_date()
    status_before = cache_status(root)
    launched = False
    waited = 0.0
    closed = False
    ui_steps: list[str] = []
    ui_message = ""
    last = read_last_bar_date(root)

    # Only launch when we actually intend to refresh (wait path), not for stale export.
    if launch and wait and (last is None or last < target):
        launched = launch_compass(root)
        time.sleep(8 if launched else 1)
        wait_for_main_hwnd(timeout_seconds=45.0)

    fresh = last is not None and last >= target
    if wait and not fresh:
        passive = min(float(timeout_seconds), max(0.0, float(passive_wait_seconds)))
        if passive > 0:
            fresh, last, waited_passive = wait_for_fresh_daily(
                compass_root=root,
                as_of=target,
                timeout_seconds=int(passive),
                poll_seconds=poll_seconds,
            )
            waited += waited_passive

    # Light path: type 0AMV to open chart / trigger auto day-fill when enabled.
    if wait and not fresh and ui_browse:
        browse = browse_0amv_symbol()
        ui_steps.extend(browse.steps)
        ui_message = browse.message
        browse_budget = min(
            max(10.0, float(browse_wait_seconds)),
            max(10.0, float(timeout_seconds) - waited),
        )
        fresh, last, waited_browse = wait_for_fresh_daily(
            compass_root=root,
            as_of=target,
            timeout_seconds=int(browse_budget),
            poll_seconds=poll_seconds,
        )
        waited += waited_browse
        if fresh:
            ui_message = f"{browse.message}; browse_ok"

    if wait and not fresh and ui_download:
        # One quiet UI attempt only — no retry storms that steal the mouse.
        ui = trigger_after_close_download(select_all=False, open_attempts=2)
        ui_steps.extend(ui.steps)
        ui_message = (ui_message + "; " if ui_message else "") + ui.message
        remaining = max(30, int(timeout_seconds - waited))
        fresh, last, waited_ui = wait_for_fresh_daily(
            compass_root=root,
            as_of=target,
            timeout_seconds=remaining,
            poll_seconds=poll_seconds,
        )
        waited += waited_ui

    if (not fresh) and prompt_manual:
        from ai_invest_advisor.compass_manual_input import (
            export_manual_amv_bundle,
            load_base_daily,
            prompt_manual_amv_dialog,
        )

        prev_close = None
        prev_as_of = None
        try:
            base = load_base_daily(compass_root=root, out_dir=out_dir, start=start)
            if not base.empty:
                prev_as_of = base["date"].iloc[-1]
                prev_close = float(base["close"].iloc[-1])
        except Exception:
            pass
        manual = prompt_manual_amv_dialog(
            expected_as_of=target,
            previous_close=prev_close,
            previous_as_of=prev_as_of if isinstance(prev_as_of, date) else None,
        )
        if manual is not None:
            try:
                _d, _i, snapshot, daily_path, ind_path, snap_path = export_manual_amv_bundle(
                    manual,
                    compass_root=root,
                    out_dir=out_dir,
                    start=start,
                )
                fresh = True
                last = manual.as_of
                ui_message = (ui_message + "; " if ui_message else "") + "manual_input"
                ui_steps = [*ui_steps, f"manual:{manual.as_of}:{manual.close}"]

                def _maybe_close_manual() -> bool:
                    should_close = close_after == "always" or (
                        close_after == "if_launched" and launched
                    )
                    if not should_close:
                        return False
                    return close_compass()

                closed = _maybe_close_manual()
                msg = (
                    f"已用人工补录导出 0AMV，最后一根={snapshot['as_of']}，"
                    f"regime={snapshot['ma5_regime']}"
                    + ("；已关闭指南针" if closed else "")
                )
                return SyncResult(
                    ok=True,
                    launched=launched,
                    waited_seconds=waited,
                    last_bar_date=str(snapshot["as_of"]),
                    expected_as_of=str(target),
                    out_daily_csv=str(daily_path),
                    out_indicator_csv=str(ind_path),
                    out_snapshot_json=str(snap_path),
                    message=msg,
                    status={
                        "before": status_before,
                        "after": cache_status(root),
                        "snapshot": snapshot,
                        "closed": closed,
                        "ui_steps": ui_steps,
                        "ui_message": ui_message,
                        "manual_input": True,
                    },
                    closed=closed,
                )
            except Exception as exc:
                ui_message = (ui_message + "; " if ui_message else "") + f"manual_failed:{exc}"

    def _maybe_close() -> bool:
        should_close = close_after == "always" or (
            close_after == "if_launched" and launched
        )
        if not should_close:
            return False
        return close_compass()

    if not fresh and not allow_stale:
        closed = _maybe_close()
        progress = read_download_progress()
        extra = f" UI={ui_message}" if ui_message else ""
        if progress:
            extra += f" progress={progress[:120]}"
        return SyncResult(
            ok=False,
            launched=launched,
            waited_seconds=waited,
            last_bar_date=str(last) if last else None,
            expected_as_of=str(target),
            out_daily_csv=None,
            out_indicator_csv=None,
            out_snapshot_json=None,
            message=(
                f"缓存未更新到 {target}（当前最后一根={last}）。"
                "已尝试：输入0AMV打开行情 → 安静「下载→接收最新」（各一次）。"
                "可加大 --timeout，或稍后再跑；也可用 --allow-stale 先导出旧缓存。"
                f"{extra}"
            ),
            status={
                **cache_status(root),
                "ui_steps": ui_steps,
                "ui_message": ui_message,
                "progress": progress,
            },
            closed=closed,
        )

    try:
        _daily, _ind, snapshot, daily_path, ind_path, snap_path = export_0amv_bundle(
            compass_root=root,
            out_dir=out_dir,
            start=start,
        )
    except CompassBridgeError as exc:
        closed = _maybe_close()
        raise CompassSyncError(str(exc)) from exc

    closed = _maybe_close()
    msg = (
        f"已导出 0AMV，最后一根={snapshot['as_of']}，regime={snapshot['ma5_regime']}"
        + ("" if fresh else "（允许使用过期缓存）")
        + (f"；UI={ui_message}" if ui_message else "")
        + ("；已关闭指南针" if closed else "")
    )
    return SyncResult(
        ok=True,
        launched=launched,
        waited_seconds=waited,
        last_bar_date=str(snapshot["as_of"]),
        expected_as_of=str(target),
        out_daily_csv=str(daily_path),
        out_indicator_csv=str(ind_path),
        out_snapshot_json=str(snap_path),
        message=msg,
        status={
            "before": status_before,
            "after": cache_status(root),
            "snapshot": snapshot,
            "closed": closed,
            "ui_steps": ui_steps,
            "ui_message": ui_message,
        },
        closed=closed,
    )


def result_to_dict(result: SyncResult) -> dict[str, Any]:
    return asdict(result)
