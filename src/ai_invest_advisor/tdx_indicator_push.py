from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "date",
    "close",
    "emotion",
    "j",
    "signal",
    "holding",
}
VALID_SIGNALS = {"BUY", "SELL", "HOLD", "FLAT"}


class TdxPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class TdxPayload:
    time_list: list[str]
    data_list: list[list[str]]


def build_tdx_payload(frame: pd.DataFrame) -> TdxPayload:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise TdxPayloadError(f"通达信数据缺少字段: {sorted(missing)}")
    if frame.empty:
        raise TdxPayloadError("通达信数据为空")

    data = frame.copy()
    try:
        data["date"] = pd.to_datetime(data["date"], errors="raise")
        for column in ("close", "emotion", "j", "holding"):
            data[column] = pd.to_numeric(data[column], errors="raise")
    except (TypeError, ValueError) as exc:
        raise TdxPayloadError("通达信数据包含无法解析的字段") from exc

    if data["date"].duplicated().any():
        raise TdxPayloadError("通达信数据存在重复日期")
    if not data["date"].is_monotonic_increasing:
        raise TdxPayloadError("通达信数据日期不是严格递增")
    if not data["signal"].isin(VALID_SIGNALS).all():
        raise TdxPayloadError("通达信数据包含非法信号")
    if not data["holding"].isin([0, 1]).all():
        raise TdxPayloadError("通达信持仓状态必须为0或1")
    if not data["emotion"].between(0.0, 100.0).all():
        raise TdxPayloadError("市场情绪必须在0到100之间")
    for column in ("close", "emotion", "j"):
        if not all(math.isfinite(float(value)) for value in data[column]):
            raise TdxPayloadError(f"{column} 包含非有限数值")

    time_list: list[str] = []
    data_list: list[list[str]] = []
    for row in data.itertuples(index=False):
        signal = str(row.signal)
        time_list.append(pd.Timestamp(row.date).strftime("%Y%m%d"))
        data_list.append(
            [
                f"{float(row.emotion):.6f}",
                "1" if signal == "BUY" else "0",
                "1" if signal == "SELL" else "0",
                str(int(row.holding)),
                f"{float(row.j):.6f}",
                f"{float(row.close):.6f}",
            ]
        )
    return TdxPayload(time_list=time_list, data_list=data_list)


def load_tdx_payload(path: Path) -> TdxPayload:
    try:
        frame = pd.read_csv(path, encoding="utf-8")
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise TdxPayloadError(f"无法读取通达信副图数据: {path}") from exc
    return build_tdx_payload(frame)


def send_tdx_payload(
    tq,
    payload: TdxPayload,
    *,
    stock_code: str = "399006.SZ",
) -> dict:
    if not payload.time_list:
        raise TdxPayloadError("通达信推送载荷为空")
    if len(payload.time_list) != len(payload.data_list):
        raise TdxPayloadError("通达信推送日期和数据行数不一致")
    result = tq.send_bt_data(
        stock_code=stock_code,
        time_list=payload.time_list,
        data_list=payload.data_list,
        count=len(payload.time_list),
    )
    if not isinstance(result, dict):
        raise TdxPayloadError("通达信返回结果不是对象")
    if result.get("ErrorId") != "0":
        detail = result.get("Error") or result.get("Msg") or result
        raise TdxPayloadError(f"通达信推送失败: {detail}")
    return result
