from datetime import date

import pandas as pd
import pytest

from ai_invest_advisor.cyb_signal_monitor import (
    MarketSnapshot,
    ModelState,
    StateFileError,
    evaluate_snapshot,
    load_state,
    replay_history,
    save_state_atomic,
)


def snapshot(
    day: str,
    *,
    close: float = 100,
    emotion: float = 40,
    j: float = 50,
    k: float = 50,
    d: float = 50,
    dead: bool = False,
    ma20: float = 100,
    ma60: float = 100,
    amv_ret_1d: float = 0.0,
    amv_ret_2d_sum: float = 0.0,
) -> MarketSnapshot:
    return MarketSnapshot(
        date=date.fromisoformat(day),
        close=float(close),
        pct_change=0.0,
        emotion=float(emotion),
        advancers=100,
        unchanged=20,
        decliners=100,
        k=float(k),
        d=float(d),
        j=float(j),
        ma20=float(ma20),
        ma60=float(ma60),
        kdj_dead_cross=dead,
        amv_ret_1d=float(amv_ret_1d),
        amv_ret_2d_sum=float(amv_ret_2d_sum),
        source_timestamp=f"{day} 15:10:00",
    )


def holding_state() -> ModelState:
    return ModelState(
        holding=True,
        entry_signal_date="2026-07-01",
        entry_signal_close=100.0,
        peak_close=110.0,
        peak_close_date="2026-07-10",
    )


def test_flat_enters_when_amv_two_day_sum_above_threshold():
    state, decision = evaluate_snapshot(
        ModelState.flat(),
        snapshot("2026-07-17", amv_ret_2d_sum=0.031),
    )

    assert decision.signal == "BUY"
    assert state.holding is True
    assert "AMV_TWO_DAY_SUM_GT_3PCT" in decision.reasons
    assert state.entry_signal_date == "2026-07-17"


def test_entry_threshold_is_strict():
    _, decision = evaluate_snapshot(
        ModelState.flat(),
        snapshot("2026-07-17", amv_ret_2d_sum=0.03),
    )
    assert decision.signal == "FLAT"


def test_holding_without_exit_stays_hold():
    state, decision = evaluate_snapshot(
        holding_state(),
        snapshot(
            "2026-07-17",
            close=105,
            ma60=100,
            emotion=40,
            amv_ret_1d=-0.01,
        ),
    )
    assert decision.signal == "HOLD"
    assert state.holding is True


def test_amv_crash_sells_when_below_ma60():
    state, decision = evaluate_snapshot(
        holding_state(),
        snapshot(
            "2026-07-17",
            close=90,
            ma60=100,
            amv_ret_1d=-0.036,
            emotion=20,
        ),
    )
    assert decision.signal == "SELL"
    assert "AMV_DAILY_RET_LE_MINUS_3_5PCT" in decision.reasons
    assert state.holding is False


def test_emotion_hot_sells_when_below_ma60():
    state, decision = evaluate_snapshot(
        holding_state(),
        snapshot(
            "2026-07-17",
            close=90,
            ma60=100,
            emotion=60,
            amv_ret_1d=0.0,
        ),
    )
    assert decision.signal == "SELL"
    assert "EMOTION_GE_60" in decision.reasons


def test_ma60_protect_suppresses_exit():
    state, decision = evaluate_snapshot(
        holding_state(),
        snapshot(
            "2026-07-17",
            close=120,
            ma60=100,
            emotion=70,
            amv_ret_1d=-0.05,
        ),
    )
    assert decision.signal == "HOLD"
    assert state.holding is True
    assert "EMOTION_GE_60" in decision.suppressed_exits
    assert "AMV_DAILY_RET_LE_MINUS_3_5PCT" in decision.suppressed_exits


def test_new_high_updates_peak():
    state, decision = evaluate_snapshot(
        holding_state(),
        snapshot("2026-07-17", close=112, ma60=100, emotion=30),
    )
    assert decision.signal == "HOLD"
    assert state.peak_close == 112
    assert state.peak_close_date == "2026-07-17"


def test_same_date_repeats_result_without_state_transition():
    first_state, first = evaluate_snapshot(
        ModelState.flat(),
        snapshot("2026-07-17", amv_ret_2d_sum=0.05),
    )
    second_state, second = evaluate_snapshot(
        first_state,
        snapshot("2026-07-17", amv_ret_2d_sum=0.05),
    )
    assert second.repeated_check is True
    assert second.signal == first.signal
    assert second_state == first_state


def test_replay_history_matches_incremental_evaluation():
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-15",
                "close": 100,
                "pct_chg": 0,
                "emotion": 40,
                "advancers": 100,
                "unchanged": 20,
                "decliners": 900,
                "k": 20,
                "d": 25,
                "j": 10,
                "ma20": 105,
                "ma60": 110,
                "kdj_dead_cross": False,
                "amv_ret_1d": 0.02,
                "amv_ret_2d_sum": 0.05,
                "source_timestamp": "2026-07-15 15:10:00",
            },
            {
                "date": "2026-07-16",
                "close": 110,
                "pct_chg": 10,
                "emotion": 40,
                "advancers": 600,
                "unchanged": 20,
                "decliners": 400,
                "k": 60,
                "d": 50,
                "j": 80,
                "ma20": 104,
                "ma60": 108,
                "kdj_dead_cross": False,
                "amv_ret_1d": 0.01,
                "amv_ret_2d_sum": 0.02,
                "source_timestamp": "2026-07-16 15:10:00",
            },
            {
                "date": "2026-07-17",
                "close": 100,
                "pct_chg": -8,
                "emotion": 65,
                "advancers": 300,
                "unchanged": 20,
                "decliners": 700,
                "k": 40,
                "d": 45,
                "j": 30,
                "ma20": 103,
                "ma60": 105,
                "kdj_dead_cross": False,
                "amv_ret_1d": -0.01,
                "amv_ret_2d_sum": 0.0,
                "source_timestamp": "2026-07-17 15:10:00",
            },
        ]
    )

    state, decision = replay_history(frame)

    assert state.holding is False
    assert decision.signal == "SELL"
    assert "EMOTION_GE_60" in decision.reasons


def test_atomic_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    expected = ModelState.flat()
    save_state_atomic(path, expected)
    assert load_state(path) == expected


def test_invalid_state_file_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"holding": "yes"}', encoding="utf-8")
    with pytest.raises(StateFileError):
        load_state(path)


def test_old_emotion_state_without_strategy_code_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"holding": false, "last_signal": "FLAT", "last_reasons": []}',
        encoding="utf-8",
    )
    with pytest.raises(StateFileError):
        load_state(path)
