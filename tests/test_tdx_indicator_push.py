import math

import pandas as pd
import pytest

from ai_invest_advisor.tdx_indicator_push import (
    TdxPayloadError,
    build_tdx_payload,
    send_tdx_payload,
)


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "close": 3692.46,
                "emotion": 45.238957,
                "j": 14.649880,
                "signal": "SELL",
                "holding": 0,
            },
            {
                "date": "2026-07-17",
                "close": 3428.63,
                "emotion": 8.728721,
                "j": 6.310737,
                "signal": "BUY",
                "holding": 1,
            },
        ]
    )


def test_build_payload_uses_six_fixed_fields():
    payload = build_tdx_payload(sample_frame())

    assert payload.time_list == ["20260716", "20260717"]
    assert payload.data_list[0] == [
        "45.238957",
        "0",
        "1",
        "0",
        "14.649880",
        "3692.460000",
    ]
    assert payload.data_list[-1] == [
        "8.728721",
        "1",
        "0",
        "1",
        "6.310737",
        "3428.630000",
    ]


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_date", "bad_signal", "nan"],
)
def test_build_payload_rejects_invalid_history(mutation):
    frame = sample_frame()
    if mutation == "duplicate_date":
        frame.loc[1, "date"] = frame.loc[0, "date"]
    elif mutation == "bad_signal":
        frame.loc[1, "signal"] = "WAIT"
    elif mutation == "nan":
        frame.loc[1, "emotion"] = math.nan

    with pytest.raises(TdxPayloadError):
        build_tdx_payload(frame)


class FakeTq:
    def __init__(self, response):
        self.response = response
        self.call = None

    def send_bt_data(self, **kwargs):
        self.call = kwargs
        return self.response


def test_sender_uses_full_count_and_fixed_symbol():
    payload = build_tdx_payload(sample_frame())
    fake = FakeTq({"ErrorId": "0", "Error": "发送回测结果成功."})

    result = send_tdx_payload(fake, payload)

    assert result["ErrorId"] == "0"
    assert fake.call["stock_code"] == "399006.SZ"
    assert fake.call["count"] == len(payload.time_list)
    assert fake.call["time_list"] == payload.time_list
    assert fake.call["data_list"] == payload.data_list
