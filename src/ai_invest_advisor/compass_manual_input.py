"""Manual 0AMV input: ask for today's close, derive return from prior bar.

Strategy gates use amv_ret_1d / amv_ret_2d_sum from consecutive closes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ai_invest_advisor.compass_bridge import (
    SYMBOL_FILE,
    SYMBOL_UI,
    CompassBridgeError,
    read_0amv_daily,
)

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "compass"


@dataclass(frozen=True)
class ManualAmvInput:
    as_of: date
    close: float
    open: float
    high: float
    low: float
    ret_pct: float | None = None


def _parse_close(text: str) -> float | None:
    raw = (text or "").strip().replace(",", "").replace("%", "")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value > 1e8:
        return None
    return value


def _parse_ret_pct(text: str) -> float | None:
    """Parse percent points, e.g. 1.5 / +1.5% / -2 → +1.5 / -2.0."""
    raw = (text or "").strip().replace(",", "").replace("%", "").replace("＋", "+")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < -50.0 or value > 50.0:
        return None
    return value


def close_from_ret_pct(previous_close: float, ret_pct: float) -> float:
    if previous_close <= 0:
        raise ValueError("previous_close must be positive")
    return float(previous_close) * (1.0 + float(ret_pct) / 100.0)


def prompt_manual_amv_via_env() -> ManualAmvInput | None:
    """Non-GUI path: prefer CYB_AMV_MANUAL_CLOSE, else RET_PCT+PREV_CLOSE."""
    as_of_raw = os.environ.get("CYB_AMV_MANUAL_DATE", "").strip()
    as_of = (
        pd.to_datetime(as_of_raw).date()
        if as_of_raw
        else datetime.now().date()
    )
    close_raw = os.environ.get("CYB_AMV_MANUAL_CLOSE", "").strip()
    if close_raw:
        close = _parse_close(close_raw)
        if close is None:
            return None
        open_ = _parse_close(os.environ.get("CYB_AMV_MANUAL_OPEN", "")) or close
        high = _parse_close(os.environ.get("CYB_AMV_MANUAL_HIGH", "")) or max(open_, close)
        low = _parse_close(os.environ.get("CYB_AMV_MANUAL_LOW", "")) or min(open_, close)
        ret_pct = None
        prev_raw = os.environ.get("CYB_AMV_MANUAL_PREV_CLOSE", "").strip()
        if prev_raw:
            prev = _parse_close(prev_raw)
            if prev and prev > 0:
                ret_pct = (close / prev - 1.0) * 100.0
        return ManualAmvInput(
            as_of=as_of, close=close, open=open_, high=high, low=low, ret_pct=ret_pct
        )

    ret_raw = os.environ.get("CYB_AMV_MANUAL_RET_PCT", "").strip()
    prev_raw = os.environ.get("CYB_AMV_MANUAL_PREV_CLOSE", "").strip()
    if ret_raw:
        ret_pct = _parse_ret_pct(ret_raw)
        prev = _parse_close(prev_raw)
        if ret_pct is None or prev is None:
            return None
        close = close_from_ret_pct(prev, ret_pct)
        return ManualAmvInput(
            as_of=as_of, close=close, open=close, high=close, low=close, ret_pct=ret_pct
        )
    return None


def prompt_manual_amv_dialog(
    *,
    expected_as_of: date,
    previous_close: float | None = None,
    previous_as_of: date | None = None,
) -> ManualAmvInput | None:
    """Show a small Tk dialog asking for today's 0AMV close."""
    close_raw = os.environ.get("CYB_AMV_MANUAL_CLOSE", "").strip()
    ret_raw = os.environ.get("CYB_AMV_MANUAL_RET_PCT", "").strip()
    if close_raw:
        env_hit = prompt_manual_amv_via_env()
        if env_hit is not None:
            as_of = expected_as_of
            if os.environ.get("CYB_AMV_MANUAL_DATE", "").strip():
                as_of = env_hit.as_of
            ret_pct = env_hit.ret_pct
            if ret_pct is None and previous_close and previous_close > 0:
                ret_pct = (env_hit.close / previous_close - 1.0) * 100.0
            return ManualAmvInput(
                as_of=as_of,
                close=env_hit.close,
                open=env_hit.open,
                high=env_hit.high,
                low=env_hit.low,
                ret_pct=ret_pct,
            )
    if ret_raw and previous_close is not None and previous_close > 0:
        ret_pct = _parse_ret_pct(ret_raw)
        if ret_pct is not None:
            close = close_from_ret_pct(previous_close, ret_pct)
            as_of = expected_as_of
            if os.environ.get("CYB_AMV_MANUAL_DATE", "").strip():
                as_of = pd.to_datetime(os.environ["CYB_AMV_MANUAL_DATE"].strip()).date()
            return ManualAmvInput(
                as_of=as_of, close=close, open=close, high=close, low=close, ret_pct=ret_pct
            )

    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception:
        return None

    result: dict[str, Any] = {"value": None}

    root = tk.Tk()
    root.title("录入今日活跃市值收盘")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=14)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(
        frame,
        text="不启动指南针。请填写今日活跃市值(0AMV)收盘数值，程序会自动算涨跌幅。",
        wraplength=440,
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    prev_txt = "无"
    if previous_close is not None and previous_close > 0:
        prev_day = str(previous_as_of) if previous_as_of else "?"
        prev_txt = f"{previous_close:,.2f}（{prev_day}）"
    ttk.Label(frame, text=f"上一交易日收盘：{prev_txt}").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )

    ttk.Label(frame, text="日期 YYYY-MM-DD").grid(row=2, column=0, sticky="w")
    date_var = tk.StringVar(value=str(expected_as_of))
    ttk.Entry(frame, textvariable=date_var, width=18).grid(
        row=2, column=1, sticky="w", pady=2
    )

    ttk.Label(frame, text="今日收盘（必填）").grid(row=3, column=0, sticky="w")
    close_var = tk.StringVar()
    close_entry = ttk.Entry(frame, textvariable=close_var, width=18)
    close_entry.grid(row=3, column=1, sticky="w", pady=2)

    chg_var = tk.StringVar(value="涨跌：—")
    ttk.Label(frame, textvariable=chg_var).grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(8, 4)
    )

    def _refresh_chg(*_args: object) -> None:
        close = _parse_close(close_var.get())
        if close is None or previous_close is None or previous_close <= 0:
            chg_var.set("涨跌：—")
            return
        pct = (close / previous_close - 1.0) * 100.0
        chg_var.set(f"涨跌：{pct:+.2f}%（相对 {previous_close:,.2f}）")

    close_var.trace_add("write", _refresh_chg)

    def _cancel() -> None:
        result["value"] = None
        root.destroy()

    def _ok() -> None:
        close = _parse_close(close_var.get())
        if close is None:
            messagebox.showerror("输入无效", "请填写有效的今日收盘价。", parent=root)
            return
        try:
            as_of = pd.to_datetime(date_var.get().strip()).date()
        except Exception:
            messagebox.showerror("输入无效", "日期格式应为 YYYY-MM-DD。", parent=root)
            return
        ret_pct = None
        if previous_close is not None and previous_close > 0:
            ret_pct = (close / previous_close - 1.0) * 100.0
        result["value"] = ManualAmvInput(
            as_of=as_of,
            close=close,
            open=close,
            high=close,
            low=close,
            ret_pct=ret_pct,
        )
        root.destroy()

    btns = ttk.Frame(frame)
    btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btns, text="取消", command=_cancel).grid(row=0, column=0, padx=6)
    ttk.Button(btns, text="确认并继续", command=_ok).grid(row=0, column=1)

    root.protocol("WM_DELETE_WINDOW", _cancel)
    close_entry.focus_set()
    root.bind("<Return>", lambda _e: _ok())
    root.bind("<Escape>", lambda _e: _cancel())

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 3)
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["value"]


def load_base_daily(
    *,
    compass_root: Path | str | None = None,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    start: date | str | None = "2018-01-01",
) -> pd.DataFrame:
    """Prefer exported CSV; fall back to day.vdat."""
    out = Path(out_dir)
    csv_path = out / "0amv_daily.csv"
    if csv_path.exists():
        frame = pd.read_csv(csv_path)
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        return frame.sort_values("date").reset_index(drop=True)
    if compass_root is not None:
        try:
            return read_0amv_daily(compass_root=compass_root, start=start)
        except (CompassBridgeError, OSError):
            pass
    raise CompassBridgeError("没有可用的 0AMV 历史（CSV / day.vdat），无法手工补当日")


def apply_manual_bar(
    daily: pd.DataFrame,
    manual: ManualAmvInput,
) -> pd.DataFrame:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "date": manual.as_of,
        "open": float(manual.open),
        "high": float(manual.high),
        "low": float(manual.low),
        "close": float(manual.close),
        "volume": 0.0,
        "amount": 0.0,
        "symbol": SYMBOL_UI,
        "symbol_file": SYMBOL_FILE,
        "source": "manual_user_input",
        "source_path": "popup_close",
        "file_mtime": now,
    }
    if manual.ret_pct is not None:
        row["ret_pct_input"] = float(manual.ret_pct)
    frame = frame[frame["date"] != manual.as_of]
    frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    frame = frame[frame["date"] <= manual.as_of]
    return frame.sort_values("date").reset_index(drop=True)


def export_manual_amv_bundle(
    manual: ManualAmvInput,
    *,
    compass_root: Path | str | None = None,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    start: date | str | None = "2018-01-01",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], Path, Path, Path]:
    """Append manual bar onto history and rewrite CSV/JSON exports."""
    from ai_invest_advisor.compass_autoupdate import (
        compute_0amv_indicators,
        latest_indicator_snapshot,
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = load_base_daily(compass_root=compass_root, out_dir=out, start=start)
    daily = apply_manual_bar(base, manual)
    if start is not None:
        start_d = pd.to_datetime(start).date()
        daily = daily[daily["date"] >= start_d].reset_index(drop=True)
    indicators = compute_0amv_indicators(daily)
    as_of_mask = pd.to_datetime(indicators["date"]).dt.date == manual.as_of
    if not as_of_mask.any():
        raise CompassBridgeError(f"手工补录后找不到日期 {manual.as_of}")
    snapshot = latest_indicator_snapshot(indicators.loc[as_of_mask].reset_index(drop=True))
    snapshot["source"] = "manual_user_input"
    if manual.ret_pct is not None:
        snapshot["ret_pct_input"] = float(manual.ret_pct)
    snapshot["disclaimer"] = (
        "研究辅助，非投资建议；本条 0AMV 收盘为人工补录，非指南针自动落盘。"
    )

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


def prompt_and_export_amv(
    *,
    expected_as_of: date | None = None,
    compass_root: Path | str | None = None,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    start: date | str | None = "2018-01-01",
) -> tuple[dict[str, Any], Path, Path, Path]:
    """Prompt for today's close and export indicators (no Compass)."""
    from ai_invest_advisor.compass_autoupdate import expected_as_of_date

    target = expected_as_of or expected_as_of_date()
    base = load_base_daily(compass_root=compass_root, out_dir=out_dir, start=start)
    if base.empty:
        raise CompassBridgeError("0AMV 历史为空，无法手工补录")
    prev_as_of = base["date"].iloc[-1]
    prev_close = float(base["close"].iloc[-1])
    if prev_as_of == target and len(base) >= 2:
        prev_as_of = base["date"].iloc[-2]
        prev_close = float(base["close"].iloc[-2])

    manual = prompt_manual_amv_dialog(
        expected_as_of=target,
        previous_close=prev_close,
        previous_as_of=prev_as_of if isinstance(prev_as_of, date) else None,
    )
    if manual is None:
        raise CompassBridgeError("已取消：未录入今日活跃市值收盘")

    _d, _i, snapshot, daily_path, ind_path, snap_path = export_manual_amv_bundle(
        manual,
        compass_root=compass_root,
        out_dir=out_dir,
        start=start,
    )
    return snapshot, daily_path, ind_path, snap_path
