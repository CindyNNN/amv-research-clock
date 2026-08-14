from pathlib import Path

import pandas as pd

from scripts.push_tdx_cyb_indicator import main, run_push


def write_data(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "date": "2026-07-17",
                "close": 3428.63,
                "emotion": 8.728721,
                "j": 6.310737,
                "signal": "BUY",
                "holding": 1,
            }
        ]
    ).to_csv(path, index=False, encoding="utf-8")


class FakeTq:
    def __init__(self):
        self.initialized = False
        self.closed = False
        self.call = None

    def initialize(self, path):
        self.initialized = True

    def send_bt_data(self, **kwargs):
        self.call = kwargs
        return {"ErrorId": "0", "Error": "发送回测结果成功."}

    def close(self):
        self.closed = True


def test_run_push_initializes_sends_and_closes(tmp_path, capsys):
    data_path = tmp_path / "history.csv"
    write_data(data_path)
    fake = FakeTq()

    result = run_push(data_path=data_path, tq=fake)

    assert result == 0
    assert fake.initialized is True
    assert fake.closed is True
    assert fake.call["stock_code"] == "399006.SZ"
    assert fake.call["count"] == 1
    assert "20260717" in capsys.readouterr().out


def test_main_reports_missing_tqcenter_without_importing_trading_code(
    tmp_path,
    capsys,
):
    data_path = tmp_path / "history.csv"
    write_data(data_path)

    result = main(
        [
            "--data",
            str(data_path),
            "--tdx-home",
            str(tmp_path / "missing-tdx"),
        ]
    )

    assert result == 4
    assert "tqcenter.py" in capsys.readouterr().err
