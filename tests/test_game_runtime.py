import threading
import time

import pytest

from server.game.game_engine import (
    EndReason,
    GameEngine,
    GameState,
    InvalidTransition,
    PlacementResult,
)
from server.game.game_runtime import GameRuntime


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


def make_engine(
    clock=None,
    *,
    answers=None,
    time_over_s=60,
    idle_s=10,
) -> GameEngine:
    return GameEngine(
        answers or {"1": "C", "2": "D"},
        time_over_s=time_over_s,
        idle_s=idle_s,
        clock=clock or time.monotonic,
    )


def wait_until(predicate, timeout=1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def test_runtime_requires_start_before_commands():
    runtime = GameRuntime(make_engine())

    with pytest.raises(RuntimeError, match="not started"):
        runtime.start_game()

    runtime.close()


def test_lifecycle_and_placement_use_cached_engine_snapshot():
    runtime = GameRuntime(make_engine(), tick_interval_s=0.01)
    runtime.start()
    try:
        started = runtime.start_game()
        placed = runtime.add_orb(
            orb_id="orb-c",
            slave=1,
            cell=1,
            voice="aespa_1",
        )

        assert started.value is GameState.RUNNING
        assert placed.value is PlacementResult.CORRECT
        snapshot = runtime.snapshot()
        assert snapshot["state"] == "RUNNING"
        assert snapshot["cells"]["1:1"]["orb_id"] == "orb-c"
        assert snapshot["cells"]["1:1"]["voice"] == "aespa_1"
    finally:
        runtime.close()


def test_command_state_changes_are_published_in_caller_order():
    transitions = []
    runtime = GameRuntime(
        make_engine(answers={"1": "C"}),
        on_state_change=lambda old, new, snapshot: transitions.append(
            (old, new, snapshot["end_reason"])
        ),
    )
    runtime.start()
    try:
        started = runtime.start_game()
        assert transitions == []
        runtime.publish_state_change(started)
        transitions.clear()

        final_orb = runtime.add_orb(orb_id="orb-c", slave=1, cell=1)
        assert final_orb.state == "RUNNING"
        assert final_orb.snapshot["is_correct"] is True
        assert transitions == []

        transitions.append(("output", "orb_added", None))
        runtime.publish_state_change(final_orb)
        dfesta = runtime.press_dfesta()
        assert dfesta.state == "RESETTING"
        assert transitions == [("output", "orb_added", None)]
        runtime.publish_state_change(dfesta)

        assert transitions == [
            ("output", "orb_added", None),
            ("RUNNING", "RESETTING", "complete"),
        ]
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("time_over_s", "idle_s", "advance_s", "reason"),
    [(5, None, 5, "time_over"), (None, 5, 5, "idle")],
)
def test_background_timeout_enters_resetting_directly(
    time_over_s,
    idle_s,
    advance_s,
    reason,
):
    clock = FakeClock()
    transitions = []
    runtime = GameRuntime(
        make_engine(clock, time_over_s=time_over_s, idle_s=idle_s),
        tick_interval_s=0.005,
        on_state_change=lambda old, new, snapshot: transitions.append(
            (old, new, snapshot["end_reason"])
        ),
    )
    runtime.start()
    try:
        runtime.publish_state_change(runtime.start_game())
        runtime.add_orb(orb_id="orb-c", slave=1, cell=1)
        transitions.clear()

        clock.advance(advance_s)
        wait_until(lambda: runtime.snapshot()["state"] == "RESETTING")

        assert transitions == [("RUNNING", "RESETTING", reason)]
    finally:
        runtime.close()


def test_empty_timeout_exposes_resetting_before_ready():
    clock = FakeClock()
    transitions = []
    runtime = GameRuntime(
        make_engine(clock, time_over_s=None, idle_s=5),
        tick_interval_s=0.005,
        on_state_change=lambda old, new, snapshot: transitions.append(
            (old, new, snapshot["end_reason"])
        ),
    )
    runtime.start()
    try:
        runtime.publish_state_change(runtime.start_game())
        transitions.clear()
        clock.advance(5)
        wait_until(
            lambda: any(new == "READY" for _old, new, _reason in transitions)
        )

        assert transitions == [
            ("RUNNING", "RESETTING", "idle"),
            ("RESETTING", "READY", None),
        ]
    finally:
        runtime.close()


def test_remove_can_complete_resetting_and_return_ready():
    runtime = GameRuntime(make_engine(), tick_interval_s=0.01)
    runtime.start()
    try:
        runtime.start_game()
        runtime.add_orb(orb_id="wrong", slave=1, cell=2)
        resetting = runtime.reset_game()
        removed = runtime.remove_orb(orb_id="wrong", slave=1, cell=2)

        assert resetting.state == "RESETTING"
        assert removed.value is True
        assert removed.state == "READY"
        assert runtime.snapshot()["cell_count"] == 0
    finally:
        runtime.close()


def test_snapshot_returns_a_detached_copy():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        first = runtime.snapshot()
        first["state"] = "BROKEN"
        first["cells"]["fake"] = {}

        second = runtime.snapshot()

        assert second["state"] == "READY"
        assert "fake" not in second["cells"]
    finally:
        runtime.close()


def test_replace_engine_updates_answers_only_while_ready_and_empty():
    runtime = GameRuntime(make_engine(answers={"1": "C"}))
    runtime.start()
    try:
        replacement = make_engine(answers={"1": "D", "2": "R"})
        result = runtime.replace_engine(replacement)
        assert result.snapshot["answers"] == {"1": "D", "2": "R"}

        runtime.start_game()
        with pytest.raises(RuntimeError, match="READY and empty"):
            runtime.replace_engine(make_engine(answers={"1": "E"}))
    finally:
        runtime.close()


def test_engine_error_is_returned_and_worker_survives():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        with pytest.raises(InvalidTransition):
            runtime.end_game()

        result = runtime.start_game()

        assert result.state == "RUNNING"
        assert runtime.running
    finally:
        runtime.close()


class RecordingEngine(GameEngine):
    def __init__(self) -> None:
        super().__init__({"1": "D"}, time_over_s=None, idle_s=None)
        self.mutation_threads = []

    def add_orb(self, **kwargs):
        self.mutation_threads.append(threading.get_ident())
        return super().add_orb(**kwargs)


def test_concurrent_producers_mutate_engine_on_one_worker_thread():
    engine = RecordingEngine()
    runtime = GameRuntime(engine, tick_interval_s=0.01)
    runtime.start()
    errors = []

    def producer(index):
        try:
            runtime.add_orb(
                orb_id=f"orb-{index}",
                slave=1,
                cell=(index % 8) + 1,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=producer, args=(i,)) for i in range(24)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(engine.mutation_threads) == 24
        assert len(set(engine.mutation_threads)) == 1
        assert engine.mutation_threads[0] not in {
            thread.ident for thread in threads
        }
    finally:
        runtime.close()


def test_barrier_waits_for_earlier_commands():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        runtime.add_orb(orb_id="orb-a", slave=1, cell=1)
        result = runtime.barrier()

        assert result.snapshot["cell_count"] == 1
        assert result.snapshot["cells"]["1:1"]["orb_id"] == "orb-a"
    finally:
        runtime.close()


def test_close_is_idempotent_and_rejects_new_commands():
    runtime = GameRuntime(make_engine())
    runtime.start()

    runtime.close()
    runtime.close()

    assert not runtime.running
    with pytest.raises(RuntimeError, match="closed"):
        runtime.start()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.start_game()


def test_runtime_preserves_end_reason_in_snapshot():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        runtime.start_game()
        result = runtime.end_game(EndReason.MANUAL)

        assert result.value is GameState.RESETTING
        assert result.snapshot["end_reason"] == "manual"
    finally:
        runtime.close()




def test_dfesta_debug_requirement_keeps_game_running_until_999_presses():
    runtime = GameRuntime(make_engine(answers={"1": "C"}))
    runtime.start()
    try:
        runtime.start_game()
        runtime.add_orb(orb_id="orb-c", slave=1, cell=1, voice="voice", known=True)
        runtime.set_dfesta_requirement(999)
        for _ in range(998):
            result = runtime.press_dfesta()
            assert result.snapshot["state"] == "RUNNING"
        assert result.snapshot["completion"]["dfesta_presses_remaining"] == 1
        assert runtime.press_dfesta().snapshot["state"] == "RESETTING"
    finally:
        runtime.close()
