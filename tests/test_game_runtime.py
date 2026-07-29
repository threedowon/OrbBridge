import threading
import time

import pytest

from server.game_engine import (
    FinishReason,
    GameEngine,
    GamePhase,
    InvalidTransition,
    PlacementResult,
)
from server.game_runtime import GameRuntime


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
    total_timeout_s=60,
    idle_timeout_s=10,
) -> GameEngine:
    return GameEngine(
        answers or {"1": "C", "2": "D"},
        total_timeout_s=total_timeout_s,
        idle_timeout_s=idle_timeout_s,
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
        running = runtime.complete_start()
        placed = runtime.add_orb(
            orb_id="orb-c",
            slave=1,
            cell=1,
            voice="aespa_1",
        )

        assert started.value is GamePhase.STARTING
        assert running.value is GamePhase.RUNNING
        assert placed.value is PlacementResult.CORRECT
        snapshot = runtime.snapshot()
        assert snapshot["phase"] == "RUNNING"
        assert snapshot["cells"]["1:1"]["orb_id"] == "orb-c"
        assert snapshot["cells"]["1:1"]["voice"] == "aespa_1"
    finally:
        runtime.close()


def test_command_phase_changes_are_published_explicitly_in_caller_order():
    transitions = []
    runtime = GameRuntime(
        make_engine(answers={"1": "C"}),
        on_phase_change=lambda old, new, snapshot: transitions.append(
            (old, new, snapshot["finish_reason"])
        ),
    )
    runtime.start()
    try:
        start = runtime.start_game()
        complete_start = runtime.complete_start()
        runtime.publish_phase_change(start)
        runtime.publish_phase_change(complete_start)
        transitions.clear()

        final_orb = runtime.add_orb(orb_id="orb-c", slave=1, cell=1)

        assert final_orb.phase == "FINISHED"
        assert transitions == []

        transitions.append(("output", "orb_added", None))
        runtime.publish_phase_change(final_orb)

        assert transitions == [
            ("output", "orb_added", None),
            ("RUNNING", "FINISHED", "complete"),
        ]
    finally:
        runtime.close()


def test_background_tick_publishes_timeout_transition_automatically():
    clock = FakeClock()
    transitions = []
    runtime = GameRuntime(
        make_engine(clock, total_timeout_s=5, idle_timeout_s=None),
        tick_interval_s=0.005,
        on_phase_change=lambda old, new, snapshot: transitions.append(
            (old, new, snapshot["finish_reason"])
        ),
    )
    runtime.start()
    try:
        runtime.publish_phase_change(runtime.start_game())
        runtime.publish_phase_change(runtime.complete_start())
        transitions.clear()

        clock.advance(5)
        wait_until(lambda: runtime.snapshot()["phase"] == "FINISHED")

        assert transitions == [("RUNNING", "FINISHED", "total_timeout")]
    finally:
        runtime.close()


def test_remove_can_finish_resetting_and_returns_ready():
    runtime = GameRuntime(make_engine(), tick_interval_s=0.01)
    runtime.start()
    try:
        runtime.start_game()
        runtime.complete_start()
        runtime.add_orb(orb_id="wrong", slave=1, cell=2)
        resetting = runtime.reset_game()
        removed = runtime.remove_orb(orb_id="wrong", slave=1, cell=2)

        assert resetting.phase == "RESETTING"
        assert removed.value is True
        assert removed.phase == "READY"
        assert runtime.snapshot()["cell_count"] == 0
    finally:
        runtime.close()


def test_snapshot_returns_a_detached_copy():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        first = runtime.snapshot()
        first["phase"] = "BROKEN"
        first["cells"]["fake"] = {}

        second = runtime.snapshot()

        assert second["phase"] == "READY"
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


def test_engine_error_is_returned_to_producer_and_worker_survives():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        with pytest.raises(InvalidTransition):
            runtime.complete_start()

        result = runtime.start_game()

        assert result.phase == "STARTING"
        assert runtime.running
    finally:
        runtime.close()


class RecordingEngine(GameEngine):
    def __init__(self) -> None:
        super().__init__(
            {"1": "D"},
            total_timeout_s=None,
            idle_timeout_s=None,
        )
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
        assert engine.mutation_threads[0] not in {thread.ident for thread in threads}
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


def test_runtime_preserves_finish_reason_in_snapshot():
    runtime = GameRuntime(make_engine())
    runtime.start()
    try:
        runtime.start_game()
        runtime.complete_start()
        result = runtime.finish_game(FinishReason.MANUAL)

        assert result.value is GamePhase.FINISHED
        assert result.snapshot["finish_reason"] == "manual"
    finally:
        runtime.close()
