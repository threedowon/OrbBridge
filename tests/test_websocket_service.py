import asyncio
import json
from pathlib import Path
import sys

import websockets


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from websocket_service import WebSocketService, event_message, snapshot_message


def test_wire_messages_validate_and_preserve_the_browser_event_shape():
    """Browser event names and argument order remain stable."""

    assert event_message("/game/state", ("RUNNING", 4)) == {
        "type": "event",
        "address": "/game/state",
        "args": ["RUNNING", 4],
    }
    assert snapshot_message({"schema_version": 1}) == {
        "type": "snapshot",
        "snapshot": {"schema_version": 1},
    }


def test_each_connection_receives_a_fresh_snapshot_before_live_events():
    """A reconnect must recover state that changed while the client was away."""

    state = {
        "schema_version": 1,
        "game": {"phase": "READY", "revision": 0},
        "cells": {},
    }
    service = WebSocketService(lambda: state, host="127.0.0.1", port=0)
    service.start()

    async def scenario():
        uri = f"ws://127.0.0.1:{service.port}"

        async with websockets.connect(uri) as client:
            initial = json.loads(await asyncio.wait_for(client.recv(), timeout=2))
            assert initial == {
                "type": "snapshot",
                "snapshot": state,
            }

            assert service.broadcast("/game/state", ["RUNNING", "", 1])
            live = json.loads(await asyncio.wait_for(client.recv(), timeout=2))
            assert live == {
                "type": "event",
                "address": "/game/state",
                "args": ["RUNNING", "", 1],
            }

        # This mutation represents state changes missed while disconnected.
        state["game"] = {"phase": "RESETTING", "revision": 2}
        state["cells"] = {"1:1": {"id": "ORB-1", "voice": "aespa_1"}}

        async with websockets.connect(uri) as reconnected:
            recovered = json.loads(
                await asyncio.wait_for(reconnected.recv(), timeout=2)
            )
            assert recovered == {
                "type": "snapshot",
                "snapshot": state,
            }

    try:
        asyncio.run(scenario())
    finally:
        service.close()
        service.close()

    assert service.running is False
