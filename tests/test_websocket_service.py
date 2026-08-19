import asyncio
import json

import websockets

from server.websocket_service import WebSocketService, event_message, snapshot_message


def test_wire_messages_validate_and_preserve_the_browser_event_shape():
    """브라우저 이벤트 이름과 인자 순서를 그대로 유지한다."""

    assert event_message("/game/state", ("RUNNING", 4)) == {
        "type": "event",
        "address": "/game/state",
        "args": ["RUNNING", 4],
    }
    assert snapshot_message({"schema_version": 3}) == {
        "type": "snapshot",
        "snapshot": {"schema_version": 3},
    }


def test_each_connection_receives_a_fresh_snapshot_before_live_events():
    """재연결하면 연결이 끊긴 동안 바뀐 상태를 복구한다."""

    state = {
        "schema_version": 3,
        "game": {"state": "READY", "revision": 0},
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

        # 연결이 끊긴 동안 놓친 상태 변경을 재현한다.
        state["game"] = {"state": "RESETTING", "revision": 2}
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
