import pytest

from server.game.game_engine import GameEngine
from server.tray_input import hall_sensor_count, tray_state


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("a,b", [(False, False), (True, False), (False, True), (True, True)])
def test_either_sensor_or_both(count, a, b):
    report = {"present": a or b, "seated": a and b}
    assert tray_state(report, hardware=True, config={"hall_sensor_count": count}) == (
        a or b, (a or b) if count == 1 else (a and b))


def test_default_and_simulation_unchanged():
    assert hall_sensor_count({}) == 2
    report = {"present": True, "seated": False}
    assert tray_state(report, hardware=True, config={}) == (True, False)
    assert tray_state(report, hardware=False, config={"hall_sensor_count": 1}) == (True, False)


@pytest.mark.parametrize("count", [0, 3, True, "1", None])
def test_invalid_config_rejected(count):
    with pytest.raises(ValueError, match="hall_sensor_count"):
        hall_sensor_count({"hall_sensor_count": count})


@pytest.mark.parametrize("report", [{"present": True}, {"present": False, "seated": True},
                                    {"present": None, "seated": None}])
def test_malformed_hardware_report_not_treated_as_placement(report):
    with pytest.raises(ValueError):
        tray_state(report, hardware=True, config={"hall_sensor_count": 1})


@pytest.mark.parametrize("count", [1, 2])
def test_placement_edge_repeat_removal_and_expiry(count):
    now = [0.0]
    game = GameEngine({"1": "C"}, time_over_s=120, idle_s=60, clock=lambda: now[0])
    def report(present, seated):
        state = tray_state({"present": present, "seated": seated}, hardware=True,
                           config={"hall_sensor_count": count})
        return game.set_tray(*state, ttl_s=3)
    assert not report(False, False)
    assert report(True, False) == (count == 1)
    assert not report(True, False)
    if count == 2:
        assert report(True, True)
    game.start()
    game.end()
    report(False, False)
    assert game.snapshot()["state"] == "READY"
    now[0] = 4
    assert game.snapshot()["tray"] == {"present": None, "seated": None}
    # Reconnecting an already placed tray must not manufacture a new start edge.
    assert not report(True, True)
