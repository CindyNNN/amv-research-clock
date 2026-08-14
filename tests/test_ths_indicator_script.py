from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ths_indicators" / "cyb_emotion_subchart.py"


class DrawRecorder:
    def __init__(self):
        self.curves = []

    def curve(self, *args):
        self.curves.append(args)


def test_indicator_uses_expected_remote_voyage_api():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'get("收盘价", i)' in source
    assert 'draw.curve("情绪"' in source
    assert 'draw.curve("冰点15"' in source
    assert "text(" in source
    assert "requests" not in source


def test_indicator_aligns_by_close_and_draws_signals(tmp_path):
    csv_path = tmp_path / "subchart.csv"
    csv_path.write_text(
        "\n".join(
            [
                "date,close,emotion,j,signal,holding",
                "2026-07-13,98,40,50,FLAT,0",
                "2026-07-14,99,20,40,FLAT,0",
                "2026-07-15,100,10,10,BUY,1",
                "2026-07-16,110,60,80,HOLD,1",
                "2026-07-17,101.2,30,30,SELL,0",
            ]
        ),
        encoding="utf-8",
    )
    source = SCRIPT.read_text(encoding="utf-8")
    source = source.replace(
        'DATA_FILE = r"C:\\Users\\Cindy\\Desktop\\Finance\\AI金融\\data\\monitor\\ths_cyb_emotion_subchart.csv"',
        f'DATA_FILE = r"{csv_path}"',
    )
    closes = [98.0, 99.0, 100.0, 110.0, 101.2]
    saved = {}
    labels = []
    draw = DrawRecorder()

    exec(
        compile(source, str(SCRIPT), "exec"),
        {
            "total": len(closes),
            "get": lambda name, i: closes[i],
            "save": lambda name, value, i: saved.__setitem__(
                (name, i), value
            ),
            "text": lambda value, i, label, color: labels.append(
                (value, i, label, color)
            ),
            "draw": draw,
        },
    )

    assert saved[("情绪", 2)] == 10.0
    assert saved[("持仓状态", 3)] == 100.0
    assert saved[("持仓状态", 4)] == 0.0
    assert any(label == "↑买" and i == 2 for _, i, label, _ in labels)
    assert any(label == "↓卖" and i == 4 for _, i, label, _ in labels)
    assert ("情绪", 5, 2) in draw.curves
    assert ("冰点15", 15, 1) in draw.curves
