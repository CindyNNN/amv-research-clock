from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_ascii(name: str) -> str:
    return (ROOT / name).read_bytes().decode("ascii")


def test_close_launcher_prompts_amv_then_close_email_only():
    text = _read_ascii("run_cyb_signal_monitor_close.bat")

    assert "prompt_0amv_ret.py" in text
    assert "--mode close" in text
    assert "sync_compass_0amv.py" not in text
    assert "push_tdx_cyb_indicator.py" not in text
    assert text.index("prompt_0amv_ret.py") < text.index("--mode close")


def test_intraday_launcher_delegates_to_close():
    text = _read_ascii("run_cyb_signal_monitor_intraday.bat")

    assert "run_cyb_signal_monitor_close.bat" in text
    assert "sync_compass_0amv.py" not in text


def test_amv_sync_launcher_is_manual_prompt_only():
    text = _read_ascii("sync_compass_0amv.bat")

    assert "prompt_0amv_ret.py" in text
    assert "run_cyb_signal_monitor.py" not in text


def test_compatibility_launcher_calls_close_launcher():
    text = _read_ascii("run_cyb_signal_monitor.bat")

    assert "run_cyb_signal_monitor_close.bat" in text


def test_task_installer_registers_single_close_task():
    text = (
        ROOT / "scripts" / "install_cyb_monitor_tasks.ps1"
    ).read_text(encoding="utf-8")

    assert "CYB Signal Monitor Close 15-30" in text
    assert "'15:30'" in text
    assert "run_cyb_signal_monitor_close.bat" in text
    assert "CYB Signal Monitor Intraday 14-40" in text
    assert "CYB Sync Compass 0AMV 15-35" in text
    assert "CYB Signal Monitor Close 15-45" in text
    assert "Register-CybMonitorTask" not in text
    assert "Monday,Tuesday,Wednesday,Thursday,Friday" in text
