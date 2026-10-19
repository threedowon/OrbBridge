import json
from collections import Counter

import pytest

from server import data_store as store, voice_catalog
from server.orb_inspection import OrbInspection, MALE_GROUPS


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    voices = {f"{group}_{i}": {} for group, count in
              {"aespa": 4, "ive": 6, "bts": 7, "cortis": 5,
               "enhypen": 7, "riize": 6, "skz": 8, "txt": 5}.items()
              for i in range(1, count + 1)}
    monkeypatch.setattr(voice_catalog, "load_voice_catalog", lambda: {"voices": voices})
    mapping = {":".join(f"{b:02X}" for b in bytes([45]) + i.to_bytes(7, "big")):
               {"voice": list(voices)[i % 48]} for i in range(192)}
    path = tmp_path / "orb_mapping.json"
    path.write_text(json.dumps(mapping))
    session = OrbInspection(path)
    session.start()
    return session, mapping


def test_version_switch_preserves_original_and_balances_192(inventory):
    session, original = inventory
    session.seen.update(original)
    session.select_version("male")
    male = store.load_mapping(session.path)
    counts = Counter(v["voice"] for v in male.values())
    assert set(male) == set(original)
    assert len(counts) == 38 and set(counts.values()) == {5, 6}
    assert all(store.voice_group(v) in MALE_GROUPS for v in counts)
    assert not session.seen
    assert session.snapshot()["version"] == "male"
    assert len(session.snapshot()["voices"]) == 38
    assert OrbInspection(session.path).version == "male"
    session.select_version("original")
    assert store.load_mapping(session.path) == original
    session.select_version("male")
    assert store.load_mapping(session.path) == male
    with pytest.raises(ValueError, match="목표"):
        session.begin_registration()


def test_reshuffle_is_balanced_and_preserves_other_version(inventory):
    session, original = inventory
    session.select_version("male")
    male = store.load_mapping(session.path)
    session.select_version("original", reshuffle=True)
    current = store.load_mapping(session.path)
    assert set(current) == set(original)
    assert set(Counter(e["voice"] for e in current.values()).values()) == {4}
    session.select_version("male")
    assert store.load_mapping(session.path) == male


def test_register_replacement_follows_id_in_both_versions(inventory):
    session, original = inventory
    session.select_version("male")
    old = next(iter(original))
    old_male = store.load_mapping(session.path)[old]
    new = "2D:FF:FF:FF:FF:FF:FF:FF"
    session.ingest({"type": "added", "slave": 1, "cell": 1, "id": new})
    session.register({"id": new, "replace_id": old})
    assert store.load_mapping(session.path)[new] == old_male
    session.select_version("original")
    assert store.load_mapping(session.path)[new] == original[old]
    assert old not in store.load_mapping(session.path)


def test_switch_write_failure_keeps_active_version(inventory, monkeypatch):
    session, original = inventory
    session.select_version("original")
    save = store.save_config
    def fail(document, path=None):
        if path == store.orb_versions_path(session.path):
            raise OSError("disk full")
        return save(document, path)
    monkeypatch.setattr(store, "save_config", fail)
    with pytest.raises(OSError):
        session.select_version("male")
    assert session.version == "original"
    assert store.load_mapping(session.path) == original


def test_default_mapping_reader_and_writer_use_active_version(inventory, monkeypatch):
    session, original = inventory
    monkeypatch.setattr(store, "MAPPING_PATH", session.path)
    session.select_version("male")
    mapping = store.load_mapping()
    key = next(iter(mapping))
    mapping[key] = {"voice": "bts_1"}
    store.save_mapping(mapping)
    assert store.load_mapping()[key] == {"voice": "bts_1"}
    session.select_version("original")
    assert store.load_mapping() == original


def test_male_manual_registration_rejects_other_groups(inventory):
    session, _ = inventory
    session.select_version("male")
    new = "2D:FF:FF:FF:FF:FF:FF:FF"
    session.ingest({"type": "added", "slave": 1, "cell": 1, "id": new})
    with pytest.raises(ValueError, match="남자 아이돌"):
        session.register({"id": new, "voice": "ive_1"})


def test_male_new_registration_reaches_192_with_no_female_voices(inventory):
    session, original = inventory
    session.path.write_text("{}")
    session.select_version("male")
    session.begin_registration()
    for index, oid in enumerate(original):
        session.ingest({"type": "added", "slave": index % 6 + 1,
                        "cell": index % 16 + 1, "id": oid})
    mapping = store.load_mapping(session.path)
    counts = Counter(e["voice"] for e in mapping.values())
    assert len(mapping) == 192 and len(counts) == 38
    assert set(counts.values()) == {5, 6}
    assert not session.auto_register
    session.select_version("original")
    assert len(store.load_mapping(session.path)) == 192
    assert set(Counter(e["voice"] for e in store.load_mapping(session.path).values()).values()) == {4}
