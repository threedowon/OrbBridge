import json

import pytest

from tools.debug import slave_test


def _status(cells_override=None, **top):
    cells = []
    for n in range(1, 17):
        c = {"c": n, "g": n + 3, "p": 0, "r": 0, "id": "", "held": 0, "crc": 0,
             "fam": 0, "flip": 0, "bnc": 0, "add": 0, "rm": 0, "low": 0, "seen": 0}
        if cells_override and n in cells_override:
            c.update(cells_override[n])
        cells.append(c)
    evt = {"type": "status", "fw": "slave_test", "id": 3, "dip": [1, 1, 0], "up": 5000,
           "state": 0, "effect": 0, "bright": 50, "rs485_rx": 0, "rs485_tx": 2, "cells": cells}
    evt.update(top)
    return json.dumps(evt)


@pytest.fixture(autouse=True)
def _fresh_state():
    slave_test.reset_session()
    with slave_test._lock:
        slave_test._board.update({"open": True, "error": "", "status": None, "hello": None,
                                  "last_rx_ms": 0.0, "last_status_ms": 0.0})
    yield
    slave_test.reset_session()
    with slave_test._lock:
        slave_test._board.update({"open": False, "status": None, "hello": None})


def test_status_line_builds_cell_rows_and_checklist():
    slave_test.handle_line(_status({
        1: {"p": 1, "r": 1, "id": "2D:11:22:33:44:55:66:77", "held": 1200, "seen": 1},
        2: {"low": 1},
        3: {"seen": 1, "bnc": 4},
    }))
    snap = slave_test.snapshot()

    assert snap["board"]["alive"] is True
    rows = {r["cell"]: r for r in snap["cells"]}
    assert len(rows) == 16
    assert rows[1]["present"] and rows[1]["verdict"] == "ok"
    assert rows[1]["id"] == "2D:11:22:33:44:55:66:77"
    assert rows[2]["stuck_low"] and rows[2]["verdict"] == "bad"
    assert rows[3]["verdict"] == "warn"
    assert rows[4]["verdict"] == "idle"

    chk = {c["key"]: c for c in snap["checklist"]}
    assert chk["firmware"]["state"] == "ok"
    assert chk["dip"]["state"] == "ok" and "3" in chk["dip"]["detail"]
    assert chk["pullup"]["state"] == "bad" and "2" in chk["pullup"]["detail"]
    assert chk["contact"]["state"] == "warn" and chk["contact"]["detail"].startswith("2/16")
    assert chk["quality"]["state"] == "warn" and "3" in chk["quality"]["detail"]
    assert chk["memtest"]["state"] == "idle"


def test_dip_zero_is_flagged():
    slave_test.handle_line(_status(id=0))
    chk = {c["key"]: c for c in slave_test.snapshot()["checklist"]}
    assert chk["dip"]["state"] == "bad"


def test_events_are_recorded_and_memtest_attached_to_cell():
    slave_test.handle_line(_status({5: {"p": 1, "seen": 1, "id": "2D:AA"}}))
    slave_test.handle_line('{"type":"added","cell":5,"gpio":15,"id":"2D:AA","family":"2D"}')
    slave_test.handle_line('{"type":"bounce","cell":5,"gpio":15,"gap_ms":40}')
    slave_test.handle_line('{"type":"memtest","cell":5,"gpio":15,"present":1,"ok":5,"tries":5,'
                           '"ms":30,"id":"2D:AA","data":"00:11:22:33:44:55:66:77"}')
    slave_test.handle_line('{"type":"removed","cell":5,"gpio":15,"id":"2D:AA","held_ms":800}')
    slave_test.handle_line("boot noise line")
    slave_test.handle_line("{not json")

    snap = slave_test.snapshot()
    types = [e["type"] for e in snap["events"]]
    assert types == ["added", "bounce", "memtest", "removed"]
    row = {r["cell"]: r for r in snap["cells"]}[5]
    assert row["memtest"]["ok"] == 5 and row["memtest"]["tries"] == 5
    chk = {c["key"]: c for c in snap["checklist"]}
    assert chk["memtest"]["state"] == "ok"
    # status 줄은 원시 로그에 남기지 않고, 잡음·깨진 JSON 은 종류를 구분해 남긴다.
    kinds = [r["kind"] for r in snap["raw"]]
    assert kinds == ["json", "json", "json", "json", "noise", "badjson"]
    assert snap["board"]["noise_lines"] == 2


def test_memtest_failure_marks_checklist_bad():
    slave_test.handle_line(_status({7: {"p": 1, "seen": 1}}))
    slave_test.handle_line('{"type":"memtest","cell":7,"present":1,"ok":2,"tries":5,"ms":30,"id":"","data":""}')
    chk = {c["key"]: c for c in slave_test.snapshot()["checklist"]}
    assert chk["memtest"]["state"] == "bad" and "7" in chk["memtest"]["detail"]


def test_hello_and_walk_are_kept():
    slave_test.handle_line('{"type":"hello","fw":"slave_test","ver":1,"id":2,"dip":[0,1,0],"buses":[4]}')
    slave_test.handle_line('{"type":"walk","slot":9,"gpio":8}')
    snap = slave_test.snapshot()
    assert snap["board"]["hello"]["ver"] == 1
    assert snap["walk_slot"] == 9


def test_send_command_rejects_unknown_and_requires_port():
    assert slave_test.send_command("Q1") == (False, "알 수 없는 명령")
    assert slave_test.send_command("") == (False, "알 수 없는 명령")
    ok, err = slave_test.send_command("C3")
    assert ok is False and "미연결" in err


def test_send_command_writes_line_to_serial():
    class FakeSerial:
        def __init__(self):
            self.buf = b""

        def write(self, data):
            self.buf += data

        def flush(self):
            pass

    fake = FakeSerial()
    with slave_test._lock:
        slave_test._ser = fake
    try:
        assert slave_test.send_command(" L12 ") == (True, "")
        assert fake.buf == b"L12\n"
    finally:
        with slave_test._lock:
            slave_test._ser = None
