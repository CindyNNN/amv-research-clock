from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QXBASE = ROOT / "tdx_indicators" / "QXBASE_市场涨跌家数.txt"
CYBQX = ROOT / "tdx_indicators" / "CYBQX_创业板情绪副图.txt"


def test_qxbase_has_two_ordered_breadth_outputs():
    source = QXBASE.read_text(encoding="utf-8")

    assert "上涨:ADVANCE;" in source
    assert "下跌:DECLINE;" in source
    assert source.index("上涨:ADVANCE;") < source.index("下跌:DECLINE;")


def test_cybqx_uses_historical_cross_index_breadth_not_current_snapshot():
    source = CYBQX.read_text(encoding="utf-8")

    for code in ("SH000001", "SZ399001", "BJ899050"):
        assert source.count(
            f"CALCSTOCKINDEX('{code}','QXBASE'"
        ) == 2
    assert "MAINZSHQ" not in source
    assert "UPN:=SHA+SZA+BJA;" in source
    assert "DNN:=SHD+SZD+BJD;" in source
    assert "SIGNALS_TQ" not in source
    assert "SIGNALS_USER" not in source
    assert "EXTERNVALUE" not in source


def test_cybqx_contains_emotion_kdj_and_paired_signals():
    source = CYBQX.read_text(encoding="utf-8")

    assert "EMO:=IF(UPN+DNN>0,UPN/(UPN+DNN)*100,DRAWNULL);" in source
    assert "RSV:=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100;" in source
    assert "K:=SMA(RSV,3,1);" in source
    assert "D:=SMA(K,3,1);" in source
    assert "J:=3*K-2*D;" in source
    assert "BUYRAW:=EMO<15 AND J<30;" in source
    assert "CROSS(D,K) AND C<MA(C,20)" in source
    assert "TFILTER(BUYRAW,SELLRAW,0)" in source
    assert "DRAWICON" in source
    assert "DRAWTEXT" in source


def test_cybqx_has_no_external_or_trading_calls():
    source = CYBQX.read_text(encoding="utf-8").upper()

    forbidden = (
        "SIGNALS_TQ",
        "SIGNALS_USER",
        "EXTERNVALUE",
        "ORDERBUY",
        "ORDERSELL",
    )
    assert all(token not in source for token in forbidden)


def test_cybqx_does_not_shadow_peak_function():
    source = CYBQX.read_text(encoding="utf-8").upper()

    assert "PEAK:=" not in source
