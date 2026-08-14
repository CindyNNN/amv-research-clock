from pathlib import Path


def test_windows_launcher_delegates_to_close_mode_launcher():
    raw = Path("run_cyb_signal_monitor.bat").read_bytes()
    text = raw.decode("ascii")

    assert "run_cyb_signal_monitor_close.bat" in text
    assert "exit /b %ERRORLEVEL%" in text


def test_tdx_launcher_is_ascii_safe_and_invokes_push_script():
    raw = Path("push_tdx_cyb_indicator.bat").read_bytes()
    text = raw.decode("ascii")

    assert "%~dp0" in text
    assert "PYTHONPATH" in text
    assert "scripts\\push_tdx_cyb_indicator.py" in text
    assert len(text.splitlines()) == 1


def test_usage_document_names_required_environment_variables():
    text = Path("docs/cyb_email_monitor_usage.md").read_text(
        encoding="utf-8"
    )

    assert "CYB_QQ_EMAIL" in text
    assert "CYB_QQ_AUTH_CODE" in text
    assert "--dry-run" in text
    assert "smtp.qq.com" in text
    assert "0AMV" in text
    assert "MA60" in text
    assert "cyb_amv_emotion_strategy_state.json" in text
