"""Low-disruption UI helpers for Compass 0AMV refresh.

Design goals (user can keep working on other apps):
- Prefer typing symbol ``0AMV`` first (lighter than download dialog).
- Prefer PostMessage clicks on the toolbar (no cursor teleport).
- At most a couple of attempts; fail fast instead of scanning the screen.
- Save/restore foreground window; avoid Esc/focus spam.
- Never auto-click 「全选」(full history download is huge).

Research support only — not investment advice.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

import win32api
import win32con
import win32gui

MAIN_TITLE_SUBSTR = "指南针全赢"
DOWNLOAD_DIALOG_TITLE = "数据接收"
RECV_LATEST_SUBSTR = "接收最新"
START_SUBSTR = "开始"
CANCEL_CONFIRM_TITLE = "盘后数据下载"
TOOLBAR_TITLES = ("资讯插件工具栏", "菜单栏")
SYMBOL_0AMV = "0AMV"

# 下载 button ≈ 15.2% across the plugin toolbar strip (calibrated 2026-08-03).
DOWNLOAD_X_FRAC = 0.152
DOWNLOAD_Y_FRAC = 0.45
# At most one precise click + one tiny nudge; never a long scan.
CLICK_NUDGES = (0, 10, -10)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", _InputUnion)]


@dataclass(frozen=True)
class UiActionResult:
    ok: bool
    message: str
    steps: tuple[str, ...] = ()


def find_main_hwnd() -> int | None:
    found: list[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if MAIN_TITLE_SUBSTR in title:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def wait_for_main_hwnd(timeout_seconds: float = 60.0, poll: float = 1.0) -> int | None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        hwnd = find_main_hwnd()
        if hwnd:
            return hwnd
        time.sleep(poll)
    return None


def _child_texts(hwnd: int) -> list[tuple[int, str]]:
    kids: list[tuple[int, str]] = []

    def cb(child, _):
        kids.append((child, win32gui.GetWindowText(child)))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return kids


def iter_top_windows() -> list[tuple[int, str, str, list[tuple[int, str]]]]:
    items: list[tuple[int, str, str, list[tuple[int, str]]]] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        items.append((hwnd, cls, title, _child_texts(hwnd)))

    win32gui.EnumWindows(cb, None)
    return items


def find_dialog(
    *,
    title_equals: str | None = None,
    title_contains: str | None = None,
    child_contains: str | None = None,
) -> tuple[int, str, list[tuple[int, str]]] | None:
    for hwnd, _cls, title, kids in iter_top_windows():
        if title_equals is not None and title != title_equals:
            continue
        if title_contains is not None and title_contains not in title:
            continue
        joined = " ".join(t for _, t in kids)
        if child_contains is not None:
            if child_contains not in joined and child_contains not in title:
                continue
        if title_equals or title_contains or child_contains:
            return hwnd, title, kids
    return None


def bm_click(hwnd: int) -> None:
    win32gui.PostMessage(hwnd, win32con.BM_CLICK, 0, 0)


def click_child_button(
    kids: list[tuple[int, str]],
    substr: str,
    *,
    exclude: tuple[str, ...] = (),
) -> str | None:
    for hwnd, text in kids:
        if substr not in text:
            continue
        if any(bad in text for bad in exclude):
            continue
        bm_click(hwnd)
        return text
    return None


def _find_toolbar(main_hwnd: int) -> int | None:
    """Prefer the lower plugin toolbar (首页/补数/下载 row)."""
    plugin: list[int] = []
    menu: list[int] = []

    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title == "资讯插件工具栏":
            plugin.append(hwnd)
        elif title == "菜单栏":
            menu.append(hwnd)
        return True

    try:
        win32gui.EnumChildWindows(main_hwnd, cb, None)
    except Exception:
        return None
    # The nested toolbar child is usually the actual clickable strip.
    if len(plugin) >= 2:
        return plugin[-1]
    if plugin:
        return plugin[0]
    return menu[0] if menu else None


def _post_click_client(hwnd: int, x: int, y: int) -> None:
    """Click inside a window via messages — does not move the system cursor."""
    lparam = win32api.MAKELONG(int(x) & 0xFFFF, int(y) & 0xFFFF)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.05)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def _ensure_visible_noactivate(hwnd: int) -> None:
    """Show/restore without forcing foreground when possible."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    except Exception:
        pass


def _is_receive_dialog(title: str, kids: list[tuple[int, str]]) -> bool:
    if title == DOWNLOAD_DIALOG_TITLE:
        return True
    joined = " ".join(t for _, t in kids)
    return RECV_LATEST_SUBSTR in joined and ("全选" in joined or "下载内容" in joined)


def _receive_dialog_open() -> bool:
    for _hwnd, _cls, title, kids in iter_top_windows():
        if _is_receive_dialog(title, kids):
            return True
    return False


def open_download_dialog(main_hwnd: int, *, attempts: int = 2) -> bool:
    """Open 数据接收 with at most a few quiet toolbar clicks."""
    if _receive_dialog_open():
        return True

    toolbar = _find_toolbar(main_hwnd)
    if not toolbar:
        return False

    def _try_clicks() -> bool:
        _ensure_visible_noactivate(main_hwnd)
        left, top, right, bottom = win32gui.GetClientRect(toolbar)
        width = max(1, right - left)
        height = max(1, bottom - top)
        base_x = int(width * DOWNLOAD_X_FRAC)
        y = int(height * DOWNLOAD_Y_FRAC)
        for attempt in range(max(1, attempts)):
            for dx in CLICK_NUDGES:
                _post_click_client(toolbar, base_x + dx, y)
                time.sleep(0.9)
                if _receive_dialog_open():
                    return True
            time.sleep(0.4 + 0.2 * attempt)
        return False

    # Soft focus once so custom toolbar often accepts messages, then restore.
    def _run() -> None:
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            pass
        time.sleep(0.2)

    prev = win32gui.GetForegroundWindow()
    try:
        _run()
        if _try_clicks():
            return True
    finally:
        if prev and win32gui.IsWindow(prev):
            try:
                win32gui.SetForegroundWindow(prev)
            except Exception:
                pass
    return _receive_dialog_open()


def confirm_cancel_if_present() -> bool:
    hit = find_dialog(title_equals=CANCEL_CONFIRM_TITLE, child_contains="是否取消")
    if not hit:
        return False
    _hwnd, _title, kids = hit
    return click_child_button(kids, "是") is not None


def _send_unicode_char(ch: str) -> None:
    """Send one Unicode character via SendInput (works better across IME)."""
    if len(ch) != 1:
        raise ValueError("expected single character")
    extra = ctypes.c_ulong(0)
    for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
        union = _InputUnion()
        union.ki = _KeyBdInput(0, ord(ch), flags, 0, ctypes.pointer(extra))
        inp = _Input(INPUT_KEYBOARD, union)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_vk(vk: int) -> None:
    extra = ctypes.c_ulong(0)
    for flags in (0, KEYEVENTF_KEYUP):
        union = _InputUnion()
        union.ki = _KeyBdInput(vk, 0, flags, 0, ctypes.pointer(extra))
        inp = _Input(INPUT_KEYBOARD, union)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _soft_focus(hwnd: int) -> int | None:
    """Briefly foreground a window; return previous foreground hwnd if any."""
    prev = win32gui.GetForegroundWindow()
    _ensure_visible_noactivate(hwnd)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.25)
    return prev if prev and prev != hwnd else None


def _restore_focus(prev: int | None) -> None:
    if not prev or not win32gui.IsWindow(prev):
        return
    try:
        win32gui.SetForegroundWindow(prev)
    except Exception:
        pass


def browse_0amv_symbol(*, symbol: str = SYMBOL_0AMV, settle_seconds: float = 2.0) -> UiActionResult:
    """Type symbol code into Compass so it opens the 0AMV chart (no download dialog).

    This is the light path: launch/login already happened; typing often triggers
    auto day-fill when enabled. After-hours daily bars may still need download.
    """
    steps: list[str] = []
    main = wait_for_main_hwnd(timeout_seconds=8.0)
    if not main:
        return UiActionResult(False, "未找到指南针主窗口，无法输入代码", tuple(steps))
    title = win32gui.GetWindowText(main)
    steps.append(f"main:{title}")

    # Close stray download confirm so keystrokes are not swallowed.
    if confirm_cancel_if_present():
        steps.append("dismiss:cancel_confirm")
        time.sleep(0.3)

    prev = _soft_focus(main)
    try:
        # Escape any open menu/dialog focus without Esc spam (single Esc).
        _send_vk(win32con.VK_ESCAPE)
        time.sleep(0.15)
        for ch in symbol:
            _send_unicode_char(ch)
            time.sleep(0.05)
        steps.append(f"typed:{symbol}")
        time.sleep(0.2)
        _send_vk(win32con.VK_RETURN)
        steps.append("key:enter")
        time.sleep(max(0.0, settle_seconds))
    finally:
        _restore_focus(prev)

    return UiActionResult(True, f"已输入 {symbol} 打开行情", tuple(steps))


def start_receive_latest(*, select_all: bool = False) -> UiActionResult:
    """Drive 数据接收 -> 接收最新 -> 开始 (buttons via BM_CLICK, no cursor)."""
    steps: list[str] = []
    dlg = None
    for _ in range(12):
        for hwnd, _cls, title, kids in iter_top_windows():
            if _is_receive_dialog(title, kids):
                dlg = (hwnd, title, kids)
                break
        if dlg:
            break
        time.sleep(0.25)
    if not dlg:
        return UiActionResult(False, "未找到数据接收对话框", tuple(steps))

    _hwnd, title, kids = dlg
    steps.append(f"dialog:{title}")
    if select_all:
        clicked = click_child_button(kids, "全选")
        if clicked:
            steps.append(f"click:{clicked}")
            time.sleep(0.4)
            kids = _child_texts(_hwnd)

    clicked = click_child_button(kids, RECV_LATEST_SUBSTR)
    if not clicked:
        return UiActionResult(False, "未找到接收最新按钮", tuple(steps))
    steps.append(f"click:{clicked}")
    time.sleep(1.0)

    for _ in range(12):
        started = False
        for _hwnd2, _cls, title2, kids2 in iter_top_windows():
            joined = " ".join(t for _, t in kids2)
            if START_SUBSTR in joined and (
                "历史数据" in title2 or "接收状态" in joined or "总包数" in joined
            ):
                label = click_child_button(kids2, "开始", exclude=("开始菜单",))
                if label:
                    steps.append(f"click:{label}@{title2}")
                    started = True
                    break
        if started:
            break
        time.sleep(0.4)

    return UiActionResult(True, "已触发盘后/历史数据接收", tuple(steps))


def parse_progress_text(text: str) -> tuple[int | None, int | None, float | None]:
    total = None
    received = None
    ratio = None
    m = re.search(r"总包数[:：]\s*(\d+)", text)
    if m:
        total = int(m.group(1))
    m = re.search(r"已接收[:：]\s*(\d+)", text)
    if m:
        received = int(m.group(1))
    m = re.search(r"比例[:：]\s*([0-9.]+)\s*%", text)
    if m:
        ratio = float(m.group(1))
    return total, received, ratio


def read_download_progress() -> str | None:
    for _hwnd, _cls, title, kids in iter_top_windows():
        joined = " ".join(t for _, t in kids)
        if "总包数" in joined or "已接收" in joined or "比例" in joined:
            return f"{title} | {joined}"
    return None


def trigger_after_close_download(
    *,
    select_all: bool = False,
    open_attempts: int = 2,
) -> UiActionResult:
    """Quiet UI sequence: open download once, start receive, return quickly."""
    steps: list[str] = []
    confirm_cancel_if_present()
    main = wait_for_main_hwnd(timeout_seconds=8.0)
    if not main:
        return UiActionResult(False, "未找到指南针主窗口", tuple(steps))
    steps.append(f"main:{win32gui.GetWindowText(main)}")
    if not open_download_dialog(main, attempts=open_attempts):
        return UiActionResult(
            False,
            "无法打开数据接收对话框（已停止重试，避免抢占鼠标）",
            tuple(steps),
        )
    steps.append("opened:数据接收")
    result = start_receive_latest(select_all=select_all)
    steps.extend(result.steps)
    return UiActionResult(result.ok, result.message, tuple(steps))


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    poll_seconds: float = 5.0,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll_seconds)
    return False
