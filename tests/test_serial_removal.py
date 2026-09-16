from unittest.mock import patch

from test_main_runtime_integration import _load_main_module


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
