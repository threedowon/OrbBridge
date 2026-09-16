from unittest.mock import patch
import sys
import pytest

from test_main_runtime_integration import _load_main_module


@pytest.fixture(autouse=True)
def restore_test_imports():
    names = ("serial", "server.main", "server.inout.serial_io")
    previous = {name: sys.modules.get(name) for name in names}
    paths = sys.path[:]
    yield
    sys.path[:] = paths
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_led_timestamps_and_ack_do_not_modify_game(capsys):
    main = _load_main_module()
    writes = []
    with patch.object(main._serial_writer, "send_line", side_effect=lambda cmd: writes.append(cmd) or True), \
            patch.object(main.hardware_io, "_snapshot", return_value={"mode": "master"}):
        main.send_led_effect(1)
    main.handle_serial_message(dict(type="led_ack", slave=1, effect=1,
                                   received_ms=0xfffffffe, applied_ms=3, ack_wait_ms=2500))
    output = capsys.readouterr().out
    assert writes == ["C1"]
    assert "PC 전송 요청 시각" in output
    assert "[led] [master] [송신]" in output
    assert "[led] [slave 1] [수신]" in output
    assert "적용 확인 이펙트=1" in output
    assert "처리=5ms" in output
    assert "응답송신대기=2500ms" in output
    assert "PC 확인 응답 수신 시각" in output
    assert not main._slaves
    assert main._game_runtime is None


def test_quick_removal_clears_slot_and_allows_same_orb_to_return():
    main = _load_main_module()
    try:
        added = {"type": "added", "slave": 1, "cell": 1, "id": "TEST-ORB"}
        with patch.object(main.time, "time", return_value=1000):
            main.handle_serial_message(added)
            assert "1:1" in main.get_cells()
        with patch.object(main.time, "time", return_value=1000.4):
            main.handle_serial_message({**added, "type": "removed"})
            assert "1:1" not in main.get_cells()
            assert "/orb/removed" in [a for a, _ in main._websocket_service.messages]
        with patch.object(main.time, "time", return_value=1000.5):
            main.handle_serial_message(added)
            main.handle_serial_message(added)  # Repeated frame is still suppressed.
            assert "1:1" in main.get_cells()
            assert sum(a == "/orb/added" for a, _ in main._websocket_service.messages) == 2
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()


def test_missing_removal_recovers_even_when_another_slot_changes():
    main = _load_main_module()
    try:
        main.handle_serial_message({"type": "added", "slave": 1, "cell": 1, "id": "TEST-ORB"})
        main.handle_serial_message({"type": "heartbeat", "slave": 1, "state": 0})
        assert "1:1" in main.get_cells()  # A single mask is not enough.
        main.handle_serial_message({"type": "heartbeat", "slave": 1, "state": 2})
        assert "1:1" not in main.get_cells()  # Slot 1 stayed empty; slot 2 changed.
        assert "1:2" not in main.get_cells()
        main.handle_serial_message({"type": "heartbeat", "slave": 1, "state": 2})
        assert "1:2" in main.get_cells()
    finally:
        if main._game_runtime is not None:
            main._game_runtime.close()
