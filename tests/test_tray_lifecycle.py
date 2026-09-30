import pytest

from server.game.game_engine import GameEngine
from server.game.game_runtime import GameRuntime


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


@pytest.fixture
def engine():
    clock = Clock()
    game = GameEngine({"1": "C"}, time_over_s=60, idle_s=30, clock=clock)
    return game, clock


@pytest.mark.parametrize("tray_first", [False, True])
def test_cleanup_requires_both_orbs_and_tray_removed(engine, tray_first):
    game, clock = engine
    assert game.set_tray(True, True)
    game.start()
    game.add_orb(orb_id="a", slave=1, cell=1, voice="ive_5")
    game.end()
    if tray_first:
        game.set_tray(False, False)
    else:
        game.remove_orb(slave=1, cell=1)
    game.tick()
    assert game.snapshot()["state"] == "RESETTING"
    clock.value = 1
    if tray_first:
        game.remove_orb(slave=1, cell=1)
    else:
        game.set_tray(False, False)
    assert game.snapshot()["state"] == "READY"
    assert game.snapshot()["timers"]["since_table_cleared_s"] == 0
    clock.value = 2
    for _ in range(5):
        game.set_tray(False, False)
        game.tick()
    assert game.snapshot()["timers"]["since_table_cleared_s"] == 1


def test_partial_tray_does_not_complete_cleanup(engine):
    game, _ = engine
    game.set_tray(True, True)
    game.start()
    game.end()
    game.set_tray(True, False)
    game.tick()
    assert game.snapshot()["state"] == "RESETTING"
    assert game.snapshot()["conditions"]["table_is_clear"]
    assert not game.snapshot()["conditions"]["cleanup_is_clear"]
    game.set_tray(False, False)
    assert game.snapshot()["state"] == "READY"


def test_unknown_or_expired_sensor_cannot_clear_table_or_create_start_edge(engine):
    game, clock = engine
    game.set_tray(None, None)
    assert not game.set_tray(True, True, ttl_s=3)  # boot already occupied
    assert not game.set_tray(True, True, ttl_s=3)  # identical heartbeat
    game.start()
    game.add_orb(orb_id="a", slave=1, cell=1)
    game.end()
    game.set_tray(False, False, ttl_s=3)
    clock.value = 4
    game.remove_orb(slave=1, cell=1)
    assert game.snapshot()["tray"] == {"present": None, "seated": None}
    assert game.snapshot()["state"] == "RESETTING"
    assert not game.set_tray(True, True, ttl_s=3)
    game.set_tray(False, False, ttl_s=3)
    assert game.snapshot()["state"] == "READY"
    assert game.set_tray(True, True, ttl_s=3)
    assert not game.set_tray(True, True, ttl_s=3)


def test_tray_reports_do_not_refresh_idle_timer(engine):
    game, clock = engine
    game.set_tray(True, True)
    game.start()
    for second in range(1, 31):
        clock.value = second
        game.set_tray(True, True, ttl_s=3)
    assert game.snapshot()["state"] == "RESETTING"
    assert game.snapshot()["end_reason"] == "idle"


@pytest.mark.parametrize("present,seated", [
    (False, True), (1, True), ("false", False), (None, False), (True, None),
])
def test_invalid_tray_state_is_rejected_without_mutation(engine, present, seated):
    game, _ = engine
    before = game.snapshot()
    with pytest.raises(ValueError):
        game.set_tray(present, seated)
    assert game.snapshot() == before


def test_runtime_engine_reload_preserves_tray_and_original_expiry(engine):
    game, clock = engine
    runtime = GameRuntime(game)
    runtime.start()
    try:
        runtime.set_tray(True, True, ttl_s=3)
        clock.value = 2
        replacement = GameEngine({"1": "D"}, time_over_s=60, idle_s=30, clock=clock)
        runtime.replace_engine(replacement)
        assert runtime.snapshot()["tray"]["seated"]
        clock.value = 3
        runtime.barrier()
        assert runtime.snapshot()["tray"]["present"] is None
    finally:
        runtime.close()
