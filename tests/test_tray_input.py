import pytest

from server.game.game_engine import GameEngine
from server.tray_input import TrayDebouncer, hall_sensor_count, tray_state


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


def test_filter_requires_continuous_placement_and_removal():
    now = [0.0]
    sensor = TrayDebouncer({}, clock=lambda: now[0])
    sensor.feed(False, False)
    assert sensor.poll().baseline
    sensor.feed(True, True)
    now[0] = 0.4
    assert sensor.poll() is None
    sensor.feed(False, False)  # A failed placement must restart the wait.
    sensor.poll()
    now[0] = 0.5
    sensor.feed(True, True)
    sensor.poll()
    now[0] = 0.99
    assert sensor.poll() is None
    now[0] = 1.0
    assert sensor.poll().seated is True
    sensor.feed(False, False)
    assert sensor.poll().present is True  # Refresh lease, retain confirmed state.
    now[0] = 2.0
    sensor.feed(False, False)  # Heartbeat must not restart the 1.5 s wait.
    assert sensor.poll().present is True
    now[0] = 2.49
    assert sensor.poll() is None
    now[0] = 2.5
    removed = sensor.poll()
    assert removed.present is False and removed.seated is False


def test_removal_is_cancelled_on_recovery_and_requires_a_new_full_wait():
    now = [0.0]
    sensor = TrayDebouncer({}, clock=lambda: now[0])
    sensor.feed(True, True)
    sensor.poll()
    sensor.feed(False, False)
    now[0] = 1.4
    sensor.feed(True, True)
    assert sensor.poll().seated is True
    now[0] = 1.5
    sensor.feed(False, False)
    assert sensor.poll().present is True
    now[0] = 2.9
    assert sensor.poll() is None
    now[0] = 3.0
    assert sensor.poll().present is False


def test_poll_does_not_renew_lease_or_confirm_stale_pending_state():
    now = [0.0]
    sensor = TrayDebouncer({"tray_removal_delay_s": 5}, clock=lambda: now[0])
    sensor.feed(True, True)
    sensor.poll()
    now[0] = 1
    sensor.feed(False, False)
    sensor.poll()
    now[0] = 3.9
    assert sensor.poll() is None
    now[0] = 4
    expired = sensor.poll()
    assert expired.present is None and expired.seated is None
    now[0] = 8
    assert sensor.poll() is None
    sensor.feed(True, True)
    assert sensor.poll().baseline


def test_report_after_expiry_is_baseline_even_without_an_intermediate_poll():
    now = [0.0]
    sensor = TrayDebouncer({}, clock=lambda: now[0])
    sensor.feed(False, False)
    sensor.poll()
    now[0] = 3
    sensor.feed(True, True)
    assert sensor.poll().baseline
    sensor.reset()
    sensor.feed(True, True)
    assert sensor.poll().baseline


@pytest.mark.parametrize("key", ["tray_placement_delay_s", "tray_removal_delay_s"])
@pytest.mark.parametrize("value", [-1, True, None, "0.5", float("nan"), float("inf")])
def test_invalid_stability_delay_rejected(key, value):
    with pytest.raises(ValueError, match=key):
        TrayDebouncer({key: value})


def test_config_can_disable_delays():
    sensor = TrayDebouncer({"tray_placement_delay_s": 0, "tray_removal_delay_s": 0})
    sensor.feed(False, False)
    sensor.poll()
    sensor.feed(True, True)
    assert sensor.poll().seated
    sensor.feed(False, False)
    assert not sensor.poll().present
