import json

import pytest

from server.game.game_engine import (
    CellAddress,
    EndReason,
    GameEngine,
    GameState,
    InvalidTransition,
    PlacementResult,
    TableNotEmpty,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("FakeClock cannot move backwards")
        self.value += seconds


def make_engine(
    clock: FakeClock,
    answers=None,
    *,
    time_over_s=60,
    idle_s=10,
    dfesta_presses_required=1,
) -> GameEngine:
    return GameEngine(
        answers or {"1": "C", "2": "D", "3": "R"},
        time_over_s=time_over_s,
        idle_s=idle_s,
        dfesta_presses_required=dfesta_presses_required,
        clock=clock,
    )


def start_running(engine: GameEngine) -> None:
    assert engine.start() is GameState.RUNNING


def test_initial_state_is_ready_and_empty():
    engine = make_engine(FakeClock())

    snapshot = engine.snapshot()

    assert snapshot["state"] == "READY"
    assert snapshot["end_reason"] is None
    assert snapshot["cell_count"] == 0
    assert snapshot["conditions"]["check_start"] is True
    assert snapshot["conditions"]["table_is_clear"] is True
    assert snapshot["timers"]["elapsed_s"] == 0


def test_state_enum_contains_only_the_three_diagram_states():
    assert {state.value for state in GameState} == {
        "READY",
        "RUNNING",
        "RESETTING",
    }


def test_answer_normalization_accepts_string_positions_and_rest():
    engine = make_engine(FakeClock(), {"01": "c", 2: "rest", "3": "D"})

    assert engine.answers == {1: "C", 2: "R", 3: "D"}


@pytest.mark.parametrize(
    "answers",
    [{}, {"1": "R"}, {"0": "C"}, {"x": "C"}, {"1": ""}],
)
def test_invalid_answer_configuration_is_rejected(answers):
    with pytest.raises(ValueError):
        GameEngine(answers, time_over_s=60, idle_s=10)


@pytest.mark.parametrize("value", [0, -1, float("inf"), True, "bad"])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(ValueError):
        GameEngine({"1": "C"}, time_over_s=value, idle_s=10)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_dfesta_press_count_is_rejected(value):
    with pytest.raises(ValueError, match="dfesta_presses_required"):
        GameEngine(
            {"1": "C"},
            time_over_s=60,
            idle_s=10,
            dfesta_presses_required=value,
        )


def test_start_enters_running_and_starts_both_timers():
    clock = FakeClock(100)
    engine = make_engine(clock)

    assert engine.start() is GameState.RUNNING
    clock.advance(3)
    snapshot = engine.snapshot()

    assert snapshot["state"] == "RUNNING"
    assert snapshot["timers"]["elapsed_s"] == 3
    assert snapshot["timers"]["remaining_time_over_s"] == 57
    assert snapshot["timers"]["remaining_idle_s"] == 7
    assert snapshot["conditions"]["check_start"] is False


def test_start_requires_ready_state_and_empty_table():
    clock = FakeClock()
    engine = make_engine(clock)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)

    with pytest.raises(TableNotEmpty):
        engine.start()
    assert engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    assert engine.start() is GameState.RUNNING
    with pytest.raises(InvalidTransition):
        engine.start()


@pytest.mark.parametrize(
    ("slave", "cell", "position", "note"),
    [
        (1, 1, 1, "C"),
        (1, 8, 1, "C"),
        (1, 9, 2, "C"),
        (1, 10, 2, "D"),
        (2, 1, 3, "C"),
        (2, 16, 4, "C"),
    ],
)
def test_cell_address_maps_to_global_position_and_note(
    slave,
    cell,
    position,
    note,
):
    address = CellAddress(slave, cell)

    assert address.position == position
    assert address.note == note


def test_running_placement_is_judged_correct_wrong_rest_or_unknown():
    engine = make_engine(FakeClock())
    start_running(engine)

    assert (
        engine.add_orb(orb_id="correct", slave=1, cell=1)
        is PlacementResult.CORRECT
    )
    assert (
        engine.add_orb(orb_id="wrong", slave=1, cell=11)
        is PlacementResult.WRONG
    )
    assert (
        engine.add_orb(orb_id="rest", slave=2, cell=1)
        is PlacementResult.WRONG
    )
    assert (
        engine.add_orb(orb_id="unknown", slave=1, cell=10, known=False)
        is PlacementResult.UNKNOWN
    )


def test_correct_answer_waits_for_dfesta_before_resetting():
    engine = make_engine(FakeClock())
    start_running(engine)

    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    engine.add_orb(orb_id="orb-d", slave=1, cell=10)
    snapshot = engine.snapshot()

    assert snapshot["state"] == "RUNNING"
    assert snapshot["is_correct"] is True
    assert snapshot["conditions"]["is_correct"] is True
    assert snapshot["completion"] == {
        "waiting_for_dfesta": True,
        "dfesta_presses": 0,
        "dfesta_presses_required": 1,
        "dfesta_presses_remaining": 1,
    }

    assert engine.press_dfesta() is True
    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.COMPLETE


def test_extra_wrong_orb_blocks_dfesta_completion_until_removed():
    engine = make_engine(FakeClock())
    start_running(engine)
    engine.add_orb(orb_id="wrong", slave=1, cell=2)
    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    engine.add_orb(orb_id="orb-d", slave=1, cell=10)

    assert engine.press_dfesta() is False
    assert engine.remove_orb(orb_id="wrong", slave=1, cell=2)
    assert engine.press_dfesta() is True
    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.COMPLETE


def test_configured_dfesta_count_requires_every_press():
    engine = make_engine(
        FakeClock(),
        {"1": "C"},
        dfesta_presses_required=2,
    )
    start_running(engine)
    engine.add_orb(orb_id="orb-c", slave=1, cell=1)

    assert engine.press_dfesta() is True
    first = engine.snapshot()
    assert first["state"] == "RUNNING"
    assert first["completion"]["dfesta_presses"] == 1
    assert first["completion"]["dfesta_presses_remaining"] == 1

    assert engine.press_dfesta() is True
    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.COMPLETE


def test_dfesta_before_solution_does_not_count_but_refreshes_idle():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "C"})
    start_running(engine)
    clock.advance(9)

    assert engine.press_dfesta() is False
    clock.advance(9)
    engine.tick()

    assert engine.snapshot()["completion"]["dfesta_presses"] == 0
    assert engine.state is GameState.RUNNING
    assert engine.snapshot()["timers"]["remaining_idle_s"] == 1


def test_breaking_solution_resets_partial_dfesta_progress():
    engine = make_engine(
        FakeClock(),
        {"1": "C"},
        dfesta_presses_required=2,
    )
    start_running(engine)
    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    engine.press_dfesta()
    engine.add_orb(orb_id="wrong", slave=1, cell=2)

    snapshot = engine.snapshot()
    assert snapshot["is_correct"] is False
    assert snapshot["completion"]["dfesta_presses"] == 0


def test_same_orb_moves_and_new_orb_replaces_previous_cell_occupant():
    engine = make_engine(FakeClock())
    start_running(engine)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2)
    engine.add_orb(orb_id="orb-b", slave=1, cell=2)
    engine.add_orb(orb_id="orb-a", slave=1, cell=3)
    snapshot = engine.snapshot()

    assert snapshot["cell_count"] == 2
    assert snapshot["cells"]["1:2"]["orb_id"] == "orb-b"
    assert snapshot["cells"]["1:3"]["orb_id"] == "orb-a"


def test_stale_remove_does_not_remove_a_different_orb():
    engine = make_engine(FakeClock())
    start_running(engine)
    engine.add_orb(orb_id="orb-b", slave=1, cell=1)

    assert not engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    assert engine.snapshot()["cells"]["1:1"]["orb_id"] == "orb-b"


@pytest.mark.parametrize("orb_id", [None, "", "   "])
def test_empty_orb_id_is_rejected(orb_id):
    engine = make_engine(FakeClock())

    with pytest.raises(ValueError):
        engine.add_orb(orb_id=orb_id, slave=1, cell=1)


def test_duplicate_add_does_not_refresh_idle_timer():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "D"})
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(1)
    engine.tick()

    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.IDLE
    assert engine.snapshot()["conditions"]["is_idle"] is True


def test_time_over_enters_resetting_directly():
    clock = FakeClock()
    engine = make_engine(clock, idle_s=None)
    start_running(engine)
    clock.advance(60)

    assert engine.tick() is GameState.RESETTING
    assert engine.end_reason is EndReason.TIME_OVER
    assert engine.snapshot()["conditions"]["is_time_over"] is True


def test_idle_enters_resetting_directly():
    clock = FakeClock()
    engine = make_engine(clock, time_over_s=None)
    start_running(engine)
    clock.advance(10)

    assert engine.tick() is GameState.RESETTING
    assert engine.end_reason is EndReason.IDLE


def test_timers_continue_while_waiting_for_dfesta():
    clock = FakeClock()
    engine = make_engine(
        clock,
        {"1": "C"},
        time_over_s=120,
        idle_s=30,
    )
    start_running(engine)
    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    clock.advance(5)

    snapshot = engine.snapshot()

    assert snapshot["state"] == "RUNNING"
    assert snapshot["is_correct"] is True
    assert snapshot["timers"] == {
        "elapsed_s": 5,
        "time_over_limit_s": 120.0,
        "idle_limit_s": 30.0,
        "remaining_time_over_s": 115,
        "remaining_idle_s": 25,
    }


def test_real_add_remove_listen_and_play_activity_refresh_idle_timer():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "C", "2": "D", "3": "E"})
    start_running(engine)
    clock.advance(9)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)
    engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)
    assert engine.record_activity() is True
    clock.advance(9)
    assert engine.record_activity() is True
    clock.advance(9)
    engine.tick()

    assert engine.state is GameState.RUNNING
    assert engine.snapshot()["timers"]["remaining_idle_s"] == 1


def test_activity_at_exact_idle_deadline_is_too_late():
    clock = FakeClock()
    engine = make_engine(clock, time_over_s=None, idle_s=10)
    start_running(engine)
    clock.advance(10)

    assert engine.record_activity() is False
    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.IDLE


def test_reset_at_exact_time_over_deadline_keeps_timer_reason():
    clock = FakeClock()
    engine = make_engine(clock, time_over_s=10, idle_s=None)
    start_running(engine)
    clock.advance(10)

    assert engine.reset() is GameState.RESETTING
    assert engine.end_reason is EndReason.TIME_OVER


def test_earliest_deadline_determines_reason_and_tie_prefers_time_over():
    first_clock = FakeClock()
    first = make_engine(first_clock, time_over_s=60, idle_s=10)
    start_running(first)
    first_clock.advance(100)
    first.tick()
    assert first.end_reason is EndReason.IDLE

    tie_clock = FakeClock()
    tie = make_engine(tie_clock, time_over_s=10, idle_s=10)
    start_running(tie)
    tie_clock.advance(10)
    tie.tick()
    assert tie.end_reason is EndReason.TIME_OVER


def test_input_at_exact_deadline_is_tracked_but_cannot_complete():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "C"}, time_over_s=10, idle_s=None)
    start_running(engine)
    clock.advance(10)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)

    assert engine.state is GameState.RESETTING
    assert engine.end_reason is EndReason.TIME_OVER
    assert engine.cell_count == 1


def test_manual_end_enters_resetting_and_freezes_elapsed_time():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)
    clock.advance(4)

    assert engine.end() is GameState.RESETTING
    clock.advance(20)

    assert engine.snapshot()["timers"]["elapsed_s"] == 4
    assert engine.end_reason is EndReason.MANUAL


def test_reset_with_orbs_waits_until_last_orb_is_removed():
    engine = make_engine(FakeClock())
    start_running(engine)
    engine.add_orb(orb_id="wrong", slave=1, cell=2)

    assert engine.reset() is GameState.RESETTING
    assert engine.remove_orb(orb_id="wrong", slave=1, cell=2)
    assert engine.state is GameState.READY
    assert engine.end_reason is None


def test_reset_with_empty_table_exposes_resetting_for_one_tick():
    engine = make_engine(FakeClock())
    start_running(engine)

    assert engine.reset() is GameState.RESETTING
    assert engine.tick() is GameState.READY


def test_add_during_resetting_keeps_resetting_until_table_is_clear():
    engine = make_engine(FakeClock())
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2)
    engine.reset()
    engine.add_orb(orb_id="orb-b", slave=1, cell=3)
    engine.remove_orb(orb_id="orb-a", slave=1, cell=2)

    assert engine.state is GameState.RESETTING
    assert engine.cell_count == 1


def test_snapshot_is_json_serializable():
    engine = make_engine(FakeClock())
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2, voice="aespa_1")

    encoded = json.dumps(engine.snapshot())

    assert '"state": "RUNNING"' in encoded
    assert '"voice": "aespa_1"' in encoded


def test_backwards_clock_is_rejected():
    clock = FakeClock(10)
    engine = make_engine(clock)
    engine.start()
    clock.value = 9

    with pytest.raises(RuntimeError, match="backwards"):
        engine.snapshot()

