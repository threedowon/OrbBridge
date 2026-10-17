import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from server import simulation_orbs
from server.web import web_server


@pytest.mark.parametrize("registered_count", [0, 2, 12])
def test_tray_has_enough_orbs_without_changing_registration(monkeypatch, registered_count):
    monkeypatch.setattr(simulation_orbs, "load_voice_catalog", lambda: {
        "voices": {"aespa_1": {}, "bts_1": {}}
    })
    registered = {f"ORB-{i}": {"voice": "aespa_1"} for i in range(registered_count)}
    original = deepcopy(registered)
    mapping = simulation_orbs.simulation_orb_mapping(registered)
    assert len(mapping) >= 12
    assert registered == original
    assert list(mapping)[:registered_count] == sorted(registered)
    assert {entry["voice"] for entry in mapping.values()} <= {"aespa_1", "bts_1"}
    if registered_count == 12:
        assert mapping == registered
    else:
        assert all(entry["simulated"] for key, entry in mapping.items() if key not in registered)


def test_simulation_routes_are_separate_from_physical_registration(monkeypatch):
    from server import data_store

    handlers = []
    calls = []
    # Exercise the real HTTP handler without opening a network socket.
    class Server:
        def __init__(self, address, handler):
            handlers.append(handler)

        def serve_forever(self):
            pass

    monkeypatch.setattr(web_server, "_ThreadingHTTPServer", Server)
    monkeypatch.setattr(web_server, "_port_in_use", lambda *args: False)
    monkeypatch.setattr(web_server.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=target))
    monkeypatch.setattr(data_store, "load_mapping", lambda: {})
    web_server.start_http_server(
        {}, lambda event: calls.append(("physical", event)) or {"ok": True},
        on_simulation_event=lambda event: calls.append(("simulation", event)) or {"ok": True},
    )

    def request(path, body=None):
        handler = object.__new__(handlers[0])
        handler.path = path
        results = []
        handler._json = lambda code, data: results.append((code, data))
        if body is None:
            handler.do_GET()
        else:
            encoded = json.dumps(body).encode()
            handler.headers = {"Content-Length": str(len(encoded))}
            handler.rfile = io.BytesIO(encoded)
            handler.do_POST()
        return results[-1]

    assert request("/api/orbs") == (200, [])
    code, orbs = request("/api/simulation/orbs")
    assert code == 200 and len(orbs) >= 12
    event = {"type": "added", "slave": 1, "cell": 1, "id": orbs[0]["id"]}
    assert request("/api/simulation/event", event) == (200, {"ok": True})
    assert request("/api/event", event) == (200, {"ok": True})
    assert [source for source, _ in calls] == ["simulation", "physical"]
    assert request("/api/simulation/event", {"type": "added"})[0] == 400
