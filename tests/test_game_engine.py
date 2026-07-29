import json

import pytest

from server.game_engine import (
    CellAddress,
    FinishReason,
    GameEngine,
    GamePhase,
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
    total_timeout_s=60,
    idle_timeout_s=10,
) -> GameEngine:
    return GameEngine(
        answers or {"1": "C", "2": "D", "3": "R"},
        total_timeout_s=total_timeout_s,
        idle_timeout_s=idle_timeout_s,
        clock=clock,
    )


def start_running(engine: GameEngine) -> None:
    assert engine.start() is GamePhase.STARTING
    assert engine.complete_start() is GamePhase.RUNNING


def test_initial_state_is_ready_and_empty():
    clock = FakeClock()
    engine = make_engine(clock)

    snapshot = engine.snapshot()

    assert snapshot["phase"] == "READY"
    assert snapshot["finish_reason"] is None
    assert snapshot["cell_count"] == 0
    assert snapshot["timers"]["elapsed_s"] == 0


def test_answer_normalization_accepts_string_positions_and_rest():
    clock = FakeClock()
    engine = make_engine(clock, {"01": "c", 2: "rest", "3": "D"})

    assert engine.answers == {1: "C", 2: "R", 3: "D"}


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"1": "R"},
        {"0": "C"},
        {"x": "C"},
        {"1": ""},
    ],
)
def test_invalid_answer_configuration_is_rejected(answers):
    with pytest.raises(ValueError):
        GameEngine(
            answers,
            total_timeout_s=60,
            idle_timeout_s=10,
        )


@pytest.mark.parametrize("value", [0, -1, float("inf"), True, "bad"])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(ValueError):
        GameEngine(
            {"1": "C"},
            total_timeout_s=value,
            idle_timeout_s=10,
        )


def test_start_has_a_distinct_starting_phase_and_does_not_start_timers():
    clock = FakeClock(100)
    engine = make_engine(clock)

    engine.start()
    clock.advance(25)
    snapshot = engine.snapshot()

    assert snapshot["phase"] == "STARTING"
    assert snapshot["timers"]["elapsed_s"] == 0


def test_start_complete_enters_running_and_starts_timers():
    clock = FakeClock(100)
    engine = make_engine(clock)
    engine.start()
    clock.advance(25)

    engine.complete_start()
    clock.advance(3)
    snapshot = engine.snapshot()

    assert snapshot["phase"] == "RUNNING"
    assert snapshot["timers"]["elapsed_s"] == 3
    assert snapshot["timers"]["remaining_total_s"] == 57
    assert snapshot["timers"]["remaining_idle_s"] == 7


def test_invalid_lifecycle_events_raise():
    clock = FakeClock()
    engine = make_engine(clock)

    with pytest.raises(InvalidTransition):
        engine.complete_start()
    engine.start()
    with pytest.raises(InvalidTransition):
        engine.start()


def test_start_requires_an_empty_table():
    clock = FakeClock()
    engine = make_engine(clock)

    result = engine.add_orb(orb_id="orb-a", slave=1, cell=1)

    assert result is PlacementResult.INACTIVE
    with pytest.raises(TableNotEmpty):
        engine.start()
    assert engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    assert engine.start() is GamePhase.STARTING


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
    clock = FakeClock()
    engine = make_engine(clock)
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


def test_all_required_positions_complete_the_game():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    result = engine.add_orb(orb_id="orb-d", slave=1, cell=10)

    assert result is PlacementResult.CORRECT
    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.COMPLETE


def test_extra_wrong_orb_blocks_completion_until_removed():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    engine.add_orb(orb_id="wrong", slave=1, cell=2)
    engine.add_orb(orb_id="orb-c", slave=1, cell=1)
    engine.add_orb(orb_id="orb-d", slave=1, cell=10)

    assert engine.phase is GamePhase.RUNNING
    assert engine.remove_orb(orb_id="wrong", slave=1, cell=2)
    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.COMPLETE


def test_unknown_orb_on_correct_cell_blocks_completion():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "C"})
    start_running(engine)

    engine.add_orb(orb_id="unknown", slave=1, cell=1, known=False)

    assert engine.phase is GamePhase.RUNNING
    assert engine.result_for_cell(slave=1, cell=1) is PlacementResult.UNKNOWN


def test_same_orb_moves_instead_of_occupying_two_cells():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2)
    snapshot = engine.snapshot()

    assert snapshot["cell_count"] == 1
    assert "1:1" not in snapshot["cells"]
    assert snapshot["cells"]["1:2"]["orb_id"] == "orb-a"


def test_new_orb_replaces_the_previous_occupant_of_a_cell():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    engine.add_orb(orb_id="orb-b", slave=1, cell=1)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2)
    snapshot = engine.snapshot()

    assert snapshot["cells"]["1:1"]["orb_id"] == "orb-b"
    assert snapshot["cells"]["1:2"]["orb_id"] == "orb-a"


def test_stale_remove_does_not_remove_a_different_orb():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    engine.add_orb(orb_id="orb-b", slave=1, cell=1)

    assert not engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    assert engine.snapshot()["cells"]["1:1"]["orb_id"] == "orb-b"


@pytest.mark.parametrize("orb_id", [None, "", "   "])
def test_empty_orb_id_is_rejected(orb_id):
    clock = FakeClock()
    engine = make_engine(clock)

    with pytest.raises(ValueError):
        engine.add_orb(orb_id=orb_id, slave=1, cell=1)


def test_duplicate_add_is_idempotent_and_does_not_refresh_idle_timer():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "D"})
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(1)
    engine.tick()

    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.IDLE_TIMEOUT


def test_total_timeout_finishes_the_game():
    clock = FakeClock()
    engine = make_engine(clock, idle_timeout_s=None)
    start_running(engine)

    clock.advance(60)
    engine.tick()

    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.TOTAL_TIMEOUT


def test_idle_timeout_finishes_the_game():
    clock = FakeClock()
    engine = make_engine(clock, total_timeout_s=None)
    start_running(engine)

    clock.advance(10)
    engine.tick()

    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.IDLE_TIMEOUT


def test_real_add_and_remove_changes_refresh_idle_timer():
    clock = FakeClock()
    engine = make_engine(clock, {"1": "C", "2": "D", "3": "E"})
    start_running(engine)
    clock.advance(9)
    engine.add_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)
    assert engine.remove_orb(orb_id="orb-a", slave=1, cell=1)
    clock.advance(9)

    engine.tick()

    assert engine.phase is GamePhase.RUNNING


def test_earliest_expired_deadline_determines_reason():
    clock = FakeClock()
    engine = make_engine(clock, total_timeout_s=60, idle_timeout_s=10)
    start_running(engine)

    clock.advance(100)
    engine.tick()

    assert engine.finish_reason is FinishReason.IDLE_TIMEOUT


def test_equal_deadlines_prefer_total_timeout():
    clock = FakeClock()
    engine = make_engine(clock, total_timeout_s=10, idle_timeout_s=10)
    start_running(engine)

    clock.advance(10)
    engine.tick()

    assert engine.finish_reason is FinishReason.TOTAL_TIMEOUT


def test_input_at_exact_deadline_is_too_late_to_complete():
    clock = FakeClock()
    engine = make_engine(
        clock,
        {"1": "C"},
        total_timeout_s=10,
        idle_timeout_s=None,
    )
    start_running(engine)
    clock.advance(10)

    engine.add_orb(orb_id="orb-a", slave=1, cell=1)

    assert engine.phase is GamePhase.FINISHED
    assert engine.finish_reason is FinishReason.TOTAL_TIMEOUT
    assert engine.cell_count == 1


def test_manual_finish_freezes_elapsed_time():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)
    clock.advance(4)

    engine.finish()
    clock.advance(20)

    assert engine.snapshot()["timers"]["elapsed_s"] == 4
    assert engine.finish_reason is FinishReason.MANUAL


def test_reset_with_orbs_waits_in_resetting_until_last_orb_is_removed():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)
    engine.add_orb(orb_id="wrong", slave=1, cell=2)

    assert engine.reset() is GamePhase.RESETTING
    assert engine.remove_orb(orb_id="wrong", slave=1, cell=2)
    assert engine.phase is GamePhase.READY
    assert engine.finish_reason is None


def test_reset_with_empty_table_returns_ready_immediately():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)

    assert engine.reset() is GamePhase.READY


def test_add_during_resetting_keeps_engine_in_resetting():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2)
    engine.reset()

    engine.add_orb(orb_id="orb-b", slave=1, cell=3)
    engine.remove_orb(orb_id="orb-a", slave=1, cell=2)

    assert engine.phase is GamePhase.RESETTING
    assert engine.cell_count == 1


def test_snapshot_is_json_serializable():
    clock = FakeClock()
    engine = make_engine(clock)
    start_running(engine)
    engine.add_orb(orb_id="orb-a", slave=1, cell=2, voice="aespa_1")

    encoded = json.dumps(engine.snapshot())

    assert '"phase": "RUNNING"' in encoded
    assert '"voice": "aespa_1"' in encoded


def test_backwards_clock_is_rejected():
    clock = FakeClock(10)
    engine = make_engine(clock)
    engine.start()
    clock.value = 9

    with pytest.raises(RuntimeError, match="backwards"):
        engine.complete_start()
