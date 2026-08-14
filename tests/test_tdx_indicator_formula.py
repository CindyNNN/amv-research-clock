from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "tdx_indicators" / "CYBQX_创业板情绪副图.txt"


def test_formula_is_native_and_has_no_trading_calls():
    source = FORMULA.read_text(encoding="utf-8")

    assert "CALCSTOCKINDEX" in source
    assert "QXBASE" in source
    assert "MAINZSHQ" not in source
    assert "TFILTER" in source
    assert "DRAWICON" in source
    assert "DRAWTEXT" in source
    assert "15" in source
    upper = source.upper()
    for forbidden in (
        "SIGNALS_TQ",
        "SIGNALS_USER",
        "EXTERNVALUE",
        "ORDER_STOCK",
        "ORDERBUY",
        "ORDERSELL",
    ):
        assert forbidden not in upper
