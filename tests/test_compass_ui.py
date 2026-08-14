from __future__ import annotations

from ai_invest_advisor.compass_ui import parse_progress_text, UiActionResult


def test_parse_progress_text() -> None:
    text = "总包数：146425 已接收：311 比例： 0.2% 字节数：25486404"
    total, received, ratio = parse_progress_text(text)
    assert total == 146425
    assert received == 311
    assert ratio == 0.2


def test_browse_0amv_mocked(monkeypatch) -> None:
    from ai_invest_advisor import compass_ui as ui

    monkeypatch.setattr(ui, "wait_for_main_hwnd", lambda timeout_seconds=8.0: 99)
    monkeypatch.setattr(ui.win32gui, "GetWindowText", lambda hwnd: "指南针全赢决策系统")
    monkeypatch.setattr(ui, "confirm_cancel_if_present", lambda: False)
    monkeypatch.setattr(ui, "_soft_focus", lambda hwnd: 1)
    monkeypatch.setattr(ui, "_restore_focus", lambda prev: None)
    chars: list[str] = []
    keys: list[int] = []
    monkeypatch.setattr(ui, "_send_unicode_char", lambda ch: chars.append(ch))
    monkeypatch.setattr(ui, "_send_vk", lambda vk: keys.append(vk))
    monkeypatch.setattr(ui.time, "sleep", lambda *_: None)

    result = ui.browse_0amv_symbol(settle_seconds=0)
    assert result.ok
    assert "".join(chars) == "0AMV"
    assert "typed:0AMV" in result.steps
    assert ui.win32con.VK_RETURN in keys


def test_trigger_download_mocked(monkeypatch) -> None:
    from ai_invest_advisor import compass_ui as ui

    monkeypatch.setattr(ui, "confirm_cancel_if_present", lambda: False)
    monkeypatch.setattr(ui, "wait_for_main_hwnd", lambda timeout_seconds=5.0: 123)
    monkeypatch.setattr(ui.win32gui, "GetWindowText", lambda hwnd: "指南针全赢决策系统 - [首页]")
    monkeypatch.setattr(ui, "open_download_dialog", lambda main_hwnd, attempts=2: True)

    def fake_start(*, select_all: bool = False):
        return UiActionResult(True, "已触发", ("dialog:数据接收", "click:接收最新(推荐)"))

    monkeypatch.setattr(ui, "start_receive_latest", fake_start)
    result = ui.trigger_after_close_download()
    assert result.ok
    assert "接收最新" in ",".join(result.steps)


def test_sync_browse_then_download(monkeypatch, tmp_path) -> None:
    from datetime import date

    from ai_invest_advisor import compass_autoupdate as mod

    monkeypatch.setattr(mod, "read_last_bar_date", lambda root=None: date(2026, 7, 31))
    monkeypatch.setattr(mod, "expected_as_of_date", lambda now=None: date(2026, 8, 3))
    monkeypatch.setattr(mod, "cache_status", lambda root: {"ok": True})
    monkeypatch.setattr(mod, "launch_compass", lambda root: True)
    monkeypatch.setattr(mod, "wait_for_main_hwnd", lambda timeout_seconds=45.0: 1)
    monkeypatch.setattr(mod, "close_compass", lambda: True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    calls = {"browse": 0, "ui": 0, "wait": 0}

    def fake_wait(**kwargs):
        calls["wait"] += 1
        # 1 passive stale, 2 browse still stale, 3 after download fresh
        if calls["wait"] <= 2:
            return False, date(2026, 7, 31), 1.0
        return True, date(2026, 8, 3), 2.0

    def fake_browse(**kwargs):
        calls["browse"] += 1
        return type("R", (), {"ok": True, "message": "typed", "steps": ("typed:0AMV",)})()

    def fake_ui(**kwargs):
        calls["ui"] += 1
        return type("R", (), {"ok": True, "message": "triggered", "steps": ("ui",)})()

    monkeypatch.setattr(mod, "wait_for_fresh_daily", fake_wait)
    monkeypatch.setattr(mod, "browse_0amv_symbol", fake_browse)
    monkeypatch.setattr(mod, "trigger_after_close_download", fake_ui)

    def fake_export(**kwargs):
        snap = {
            "as_of": "2026-08-03",
            "ma5_regime": "up",
            "close": 1.0,
            "ma5": 1.0,
        }
        p = tmp_path / "x.csv"
        p.write_text("a", encoding="utf-8")
        j = tmp_path / "x.json"
        j.write_text("{}", encoding="utf-8")
        return None, None, snap, p, p, j

    monkeypatch.setattr(mod, "export_0amv_bundle", fake_export)
    result = mod.sync_and_export(
        compass_root=tmp_path,
        out_dir=tmp_path,
        timeout_seconds=120,
        passive_wait_seconds=5,
        browse_wait_seconds=10,
        ui_browse=True,
        ui_download=True,
        close_after="never",
        prompt_manual=False,
    )
    assert calls["browse"] == 1
    assert calls["ui"] == 1
    assert result.ok
    assert result.last_bar_date == "2026-08-03"


def test_sync_browse_alone_can_succeed(monkeypatch, tmp_path) -> None:
    from datetime import date

    from ai_invest_advisor import compass_autoupdate as mod

    monkeypatch.setattr(mod, "read_last_bar_date", lambda root=None: date(2026, 7, 31))
    monkeypatch.setattr(mod, "expected_as_of_date", lambda now=None: date(2026, 8, 3))
    monkeypatch.setattr(mod, "cache_status", lambda root: {"ok": True})
    monkeypatch.setattr(mod, "launch_compass", lambda root: False)
    monkeypatch.setattr(mod, "wait_for_main_hwnd", lambda timeout_seconds=45.0: 1)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    calls = {"browse": 0, "ui": 0, "wait": 0}

    def fake_wait(**kwargs):
        calls["wait"] += 1
        if calls["wait"] == 1:
            return False, date(2026, 7, 31), 1.0
        return True, date(2026, 8, 3), 2.0

    def fake_browse(**kwargs):
        calls["browse"] += 1
        return type("R", (), {"ok": True, "message": "typed", "steps": ("typed:0AMV",)})()

    def fake_ui(**kwargs):
        calls["ui"] += 1
        return type("R", (), {"ok": True, "message": "triggered", "steps": ("ui",)})()

    monkeypatch.setattr(mod, "wait_for_fresh_daily", fake_wait)
    monkeypatch.setattr(mod, "browse_0amv_symbol", fake_browse)
    monkeypatch.setattr(mod, "trigger_after_close_download", fake_ui)

    def fake_export(**kwargs):
        snap = {"as_of": "2026-08-03", "ma5_regime": "up", "close": 1.0, "ma5": 1.0}
        p = tmp_path / "x.csv"
        p.write_text("a", encoding="utf-8")
        j = tmp_path / "x.json"
        j.write_text("{}", encoding="utf-8")
        return None, None, snap, p, p, j

    monkeypatch.setattr(mod, "export_0amv_bundle", fake_export)
    result = mod.sync_and_export(
        compass_root=tmp_path,
        out_dir=tmp_path,
        timeout_seconds=60,
        passive_wait_seconds=5,
        browse_wait_seconds=10,
        ui_browse=True,
        ui_download=True,
        close_after="never",
        prompt_manual=False,
    )
    assert calls["browse"] == 1
    assert calls["ui"] == 0
    assert result.ok
    assert "browse_ok" in (result.status.get("ui_message") or "")


def test_sync_calls_ui_when_stale(monkeypatch, tmp_path) -> None:
    from datetime import date

    from ai_invest_advisor import compass_autoupdate as mod

    monkeypatch.setattr(mod, "read_last_bar_date", lambda root=None: date(2026, 7, 31))
    monkeypatch.setattr(mod, "expected_as_of_date", lambda now=None: date(2026, 8, 3))
    monkeypatch.setattr(mod, "cache_status", lambda root: {"ok": True})
    monkeypatch.setattr(mod, "launch_compass", lambda root: True)
    monkeypatch.setattr(mod, "wait_for_main_hwnd", lambda timeout_seconds=45.0: 1)
    monkeypatch.setattr(mod, "close_compass", lambda: True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    calls = {"ui": 0, "wait": 0, "browse": 0}

    def fake_wait(**kwargs):
        calls["wait"] += 1
        # First passive wait stale; browse wait stale; after UI wait become fresh.
        if calls["wait"] <= 2:
            return False, date(2026, 7, 31), 1.0
        return True, date(2026, 8, 3), 2.0

    def fake_browse(**kwargs):
        calls["browse"] += 1
        return type("R", (), {"ok": True, "message": "typed", "steps": ("typed:0AMV",)})()

    def fake_ui(**kwargs):
        calls["ui"] += 1
        return type("R", (), {"ok": True, "message": "triggered", "steps": ("ui",)})()

    monkeypatch.setattr(mod, "wait_for_fresh_daily", fake_wait)
    monkeypatch.setattr(mod, "browse_0amv_symbol", fake_browse)
    monkeypatch.setattr(mod, "trigger_after_close_download", fake_ui)

    def fake_export(**kwargs):
        snap = {
            "as_of": "2026-08-03",
            "ma5_regime": "up",
            "close": 1.0,
            "ma5": 1.0,
        }
        p = tmp_path / "x.csv"
        p.write_text("a", encoding="utf-8")
        j = tmp_path / "x.json"
        j.write_text("{}", encoding="utf-8")
        return None, None, snap, p, p, j

    monkeypatch.setattr(mod, "export_0amv_bundle", fake_export)
    result = mod.sync_and_export(
        compass_root=tmp_path,
        out_dir=tmp_path,
        timeout_seconds=60,
        passive_wait_seconds=5,
        browse_wait_seconds=5,
        ui_browse=True,
        ui_download=True,
        close_after="never",
        prompt_manual=False,
    )
    assert calls["browse"] == 1
    assert calls["ui"] == 1
    assert result.ok
    assert result.last_bar_date == "2026-08-03"


def test_sync_skips_when_lock_held(tmp_path, monkeypatch) -> None:
    from datetime import date

    from ai_invest_advisor import compass_autoupdate as mod

    class AlwaysBlocked:
        def __init__(self, path=None):
            self.acquired = False

        def acquire(self):
            return False

        def release(self):
            return None

    monkeypatch.setattr(mod, "_SyncLock", AlwaysBlocked)
    monkeypatch.setattr(mod, "read_last_bar_date", lambda root=None: date(2026, 8, 5))
    monkeypatch.setattr(mod, "expected_as_of_date", lambda now=None: date(2026, 8, 6))
    monkeypatch.setattr(mod, "cache_status", lambda root: {})
    result = mod.sync_and_export(
        compass_root=tmp_path,
        out_dir=tmp_path,
        ui_download=True,
        wait=True,
        close_after="never",
    )
    assert result.ok is False
    assert "另一同步任务" in result.message
