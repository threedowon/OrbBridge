import json
import pytest
from server.orb_inspection import OrbInspection

A="2D:01:02:03:04:05:06:07"
B="2D:11:02:03:04:05:06:07"
@pytest.fixture
def session(tmp_path):
    p=tmp_path/"orb_mapping.json"
    p.write_text(json.dumps({A:{"voice":"aespa_1"}}))
    s=OrbInspection(p)
    s.start()
    s.ingest({"type":"heartbeat","slave":1,"state":65535})
    return s

def add(s, oid=A, cell=1):
    s.ingest({"type":"added","slave":1,"cell":cell,"id":oid})

def test_accumulates_unique_ids_across_batches(session):
    add(session)
    session.ingest({"type":"removed","slave":1,"cell":1})
    assert session.snapshot()["cells"] == []
    add(session,cell=2)
    assert session.snapshot()["seen"] == [A]
    assert session.snapshot()["missing"] == []
    add(session,B)
    assert len(session.seen)==2

def test_pause_and_heartbeat_do_not_count_ids(session):
    session.mode="paused"
    add(session)
    assert not session.seen
    session.ingest({"type":"heartbeat","slave":1,"state":0})
    assert not session.cells

def test_stale_slave_not_lit_but_retains_history(session):
    add(session)
    session.heartbeats[1]-=10
    session.last_read[1]-=10
    assert session.snapshot()["cells"] == []
    assert session.seen == {A}

def test_register_replace_backup_and_validation(session):
    add(session,B)
    result=session.register({"id":B,"replace_id":A})
    from pathlib import Path
    assert A in json.loads(Path(result["backup"]).read_text())
    assert json.loads(session.path.read_text())=={B:{"voice":"aespa_1"}}
    with pytest.raises(ValueError):session.register({"id":B,"voice":"ive_1"})
    with pytest.raises(ValueError):session.register({"id":A,"voice":"ive_1"})

def test_guard_blocks_game_during_pause_too(session):
    fn=session.guard(lambda:42)
    with pytest.raises(RuntimeError):fn()
    session.mode="paused"
    with pytest.raises(RuntimeError):fn()
    session.mode="finished"
    assert fn()==42


def test_first_read_survives_until_first_heartbeat(session):
    session.heartbeats.clear()
    add(session)
    assert len(session.snapshot()["cells"]) == 1
    session.ingest({"type":"heartbeat","slave":1,"state":1})
    assert session.snapshot()["slaves"][0]["online"]


def test_new_registration_preserves_existing_and_rejects_stale(session):
    add(session,B)
    session.register({"id":B,"voice":"aespa_1"})
    assert len(json.loads(session.path.read_text()))==2
    session.heartbeats[1]-=10
    session.last_read[1]-=10
    with pytest.raises(ValueError):
        session.register({"id":B,"voice":"ive_1"})


def test_192_orbs_two_batches_four_per_voice(session):
    ids=[":".join(f"{b:02X}" for b in bytes([45])+i.to_bytes(7,"big")) for i in range(192)]
    mapping={oid:{"voice":f"artist_{i%48+1}"} for i,oid in enumerate(ids)}
    session.path.write_text(json.dumps(mapping))
    for batch in (ids[:96],ids[96:]):
        for sid in range(1,7):
            session.ingest({"type":"heartbeat","slave":sid,"state":65535})
        for i,oid in enumerate(batch):
            event={"type":"added","slave":i//16+1,"cell":i%16+1,"id":oid}
            session.ingest(event)
            session.ingest(event)
        for sid in range(1,7):
            session.ingest({"type":"heartbeat","slave":sid,"state":0})
    result=session.snapshot()
    assert len(result["seen"])==192 and not result["missing"] and not result["cells"]
    for voice in {v["voice"] for v in mapping.values()}:
        assert sum(mapping[oid]["voice"]==voice for oid in result["seen"])==4


def test_matrix_check_overrides_game_messages():
    from server.inout.matrix_display import MatrixDisplay
    matrix=MatrixDisplay({"enabled":False})
    assert matrix._payload({"orb_inspection":True,"state":"RESETTING"},{},0)=={"type":"status","text":"ORB CHECK"}


def test_auto_register_round_robin_192_unique_and_capacity(session, monkeypatch):
    from server import voice_catalog
    voices=[f"artist_{i:02d}" for i in range(48)]
    monkeypatch.setattr(voice_catalog,"load_voice_catalog",lambda:{"voices":dict.fromkeys(voices,{})})
    session.path.write_text("{}")
    session.begin_registration()
    ids=[":".join(f"{b:02X}" for b in bytes([45])+i.to_bytes(7,"big")) for i in range(193)]
    for i,oid in enumerate(ids):
        event={"type":"added","slave":i%6+1,"cell":i%16+1,"id":oid}
        session.ingest(event)
        session.ingest(event)
        if i==95:
            mapping=json.loads(session.path.read_text())
            assert len(mapping)==96
            assert all(sum(x["voice"]==v for x in mapping.values())==2 for v in voices)
    mapping=json.loads(session.path.read_text())
    assert len(mapping)==192 and ids[-1] not in mapping
    assert all(sum(x["voice"]==v for x in mapping.values())==4 for v in voices)
    assert not session.auto_register
    assert len({mapping[oid]["voice"] for oid in ids[:48]}) == 48
    assert mapping[ids[0]]["voice"] == mapping[ids[48]]["voice"]


def test_auto_registration_pause_resume_existing_and_disk_failure(session, monkeypatch):
    from server import voice_catalog
    monkeypatch.setattr(voice_catalog,"load_voice_catalog",lambda:{"voices":{"aespa_1":{},"ive_1":{}}})
    session.begin_registration()
    session.mode="paused"
    add(session,B)
    assert len(json.loads(session.path.read_text()))==1
    session.mode="running"
    add(session,B)
    assert json.loads(session.path.read_text())[B]["voice"]=="ive_1"
    session.auto_register=False
    session.begin_registration()
    assert session.next_voice(json.loads(session.path.read_text())) in ("aespa_1", "ive_1")
    with pytest.raises(RuntimeError): session.register({"id":B,"voice":"aespa_1"})
    monkeypatch.setattr(session,"register",lambda *a,**kw:(_ for _ in ()).throw(OSError("disk full")))
    add(session,"2D:AA:BB:CC:DD:EE:FF:00")
    assert not session.auto_register and "disk full" in session.auto_error
