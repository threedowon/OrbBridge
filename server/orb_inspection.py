"""Physical-only inventory session, independent of game cells."""
import re
import random
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from server import data_store as store

MALE_GROUPS = {"bts", "cortis", "enhypen", "riize", "skz", "txt"}

class OrbInspection:
    def __init__(self, mapping_path=None):
        self.path = Path(mapping_path or store.MAPPING_PATH)
        self.lock = threading.RLock()
        self.mode = "idle"
        self.seen = set()
        self.cells = {}
        self.heartbeats = {}
        self.last_read = {}
        self.started = None
        self.auto_register = False
        self.auto_voices = []
        self.auto_error = ""
        self.auto_recent = []

    @property
    def active(self):
        return self.mode in ("running", "paused")

    @property
    def version(self):
        return (store.load_orb_versions(self.path) or {}).get("active", "original")

    def eligible_voices(self, version=None):
        from server.voice_catalog import load_voice_catalog
        voices = load_voice_catalog().get("voices", {})
        return {v: p for v, p in voices.items()
                if (version or self.version) == "original" or store.voice_group(v) in MALE_GROUPS}

    def target_count(self):
        if self.version == "original":
            return len(self.eligible_voices()) * 4
        versions = store.load_orb_versions(self.path) or {}
        original = versions.get("mappings", {}).get("original", {})
        return max(192, len(original), len(store.load_mapping(self.path)))

    def backup(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = self.path.parent / "orb_backups" / (stamp + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        store.save_config(store.load_mapping(self.path), backup)
        versions = store.load_orb_versions(self.path)
        if versions is not None:
            store.save_config(versions, backup.with_suffix(".versions.json"))
        return backup

    def select_version(self, version, *, reshuffle=False):
        if version not in ("original", "male"):
            raise ValueError("올바른 오브 버전을 선택하세요")
        if not self.active:
            raise RuntimeError("점검 세션을 먼저 시작하세요")
        with self.lock:
            versions = store.load_orb_versions(self.path) or {
                "active": "original", "mappings": {"original": store.load_mapping(self.path)}}
            mappings = versions["mappings"]
            voices = list(self.eligible_voices(version))
            if not voices:
                raise ValueError("선택한 버전에 등록 가능한 아티스트가 없습니다")
            # Keep saved assignments stable; fill only IDs absent from this version.
            ids = sorted({oid for mapping in mappings.values() for oid in mapping})
            target = {} if reshuffle else dict(mappings.get(version, {}))
            counts = {v: sum(e.get("voice") == v for e in target.values()) for v in voices}
            random.shuffle(ids)
            for oid in ids:
                if oid in target:
                    continue
                minimum = min(counts.values())
                voice = random.choice([v for v in voices if counts[v] == minimum])
                target[oid] = {"voice": voice}
                counts[voice] += 1
            backup = self.backup()
            mappings[version] = target
            versions["active"] = version
            store.save_config(versions, store.orb_versions_path(self.path))
            self.start()
            self.auto_voices = []
            self.auto_recent = []
            self.auto_error = ""
            return {"ok": True, "backup": str(backup)}

    def guard(self, fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with self.lock:
                if self.active:
                    raise RuntimeError("ORB 점검 중에는 게임을 실행할 수 없습니다")
                return fn(*args, **kwargs)
        return wrapped

    def start(self):
        self.auto_register = False
        self.mode = "running"
        self.seen.clear()
        self.cells.clear()
        self.heartbeats.clear()
        self.last_read.clear()
        self.started = datetime.now().isoformat(timespec="seconds")

    def ingest(self, event):
        with self.lock:
            if not self.active:
                return
            sid, cell = event.get("slave"), event.get("cell")
            if type(sid) is not int or not 1 <= sid <= 6:
                return
            kind = event.get("type")
            if kind == "heartbeat":
                self.heartbeats[sid] = time.monotonic()
                mask = event.get("state", event.get("occupancy_mask"))
                if type(mask) is int and 0 <= mask <= 65535:
                    for key in list(self.cells):
                        if key[0] == sid and not mask & (1 << (key[1]-1)):
                            self.cells.pop(key, None)
                return
            if type(cell) is not int or not 1 <= cell <= 16:
                return
            key = (sid, cell)
            if kind == "removed":
                self.cells.pop(key, None)
            elif kind in ("added", "snapshot"):
                raw = str(event.get("id", ""))
                if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2}", raw):
                    return
                oid = raw.upper()
                self.last_read[sid] = time.monotonic()
                # An ID moved to another socket must not occupy both positions.
                self.cells = {k:v for k,v in self.cells.items() if v != oid}
                self.cells[key] = oid
                if self.mode == "running":
                    self.seen.add(oid)
                    if self.auto_register:
                        try:
                            mapping = store.load_mapping(self.path)
                            if store.find_orb(oid, mapping)[0] is None:
                                voice = self.next_voice(mapping)
                                if voice is None:
                                    self.auto_register = False
                                else:
                                    self.register({"id": oid, "voice": voice}, automatic=True)
                                    self.auto_recent = [{"id": oid, "voice": voice}] + self.auto_recent[:19]
                                    if self.next_voice(store.load_mapping(self.path)) is None:
                                        self.auto_register = False
                        except (OSError, ValueError, RuntimeError) as exc:
                            self.auto_register = False
                            self.auto_error = f"자동 등록 중단: {exc}"

    def next_voice(self, mapping):
        counts = {voice: 0 for voice in self.auto_voices}
        for entry in mapping.values():
            voice = entry.get("voice")
            if voice in counts:
                counts[voice] += 1
        if sum(counts.values()) >= self.target_count():
            return None
        candidates = [voice for voice in self.auto_voices
                      if self.version == "male" or counts[voice] < 4]
        if not candidates:
            return None
        return min(candidates, key=lambda v: counts[v])

    def begin_registration(self):
        with self.lock:
            if not self.active:
                raise RuntimeError("점검 세션을 먼저 시작하세요")
            self.auto_voices = sorted(self.eligible_voices())
            random.shuffle(self.auto_voices)
            if not self.auto_voices:
                raise ValueError("등록할 아티스트 목록이 없습니다")
            if self.next_voice(store.load_mapping(self.path)) is None:
                raise ValueError("현재 버전의 등록 목표가 이미 채워져 있습니다")
            self.auto_error = ""
            self.auto_recent = []
            self.mode = "running"
            self.auto_register = True

    def snapshot(self):
        with self.lock:
            mapping = store.load_mapping(self.path)
            now = time.monotonic()
            online = {sid for sid, stamp in self.heartbeats.items() if now-stamp < 8}
            # Discard stale locations so a reconnect needs a fresh ID read.
            self.cells = {k:v for k,v in self.cells.items()
                          if now-max(self.heartbeats.get(k[0], 0), self.last_read.get(k[0], 0)) < 8}
            return {"mode": self.mode, "started": self.started,
                "auto_register": self.auto_register, "auto_error": self.auto_error,
                "auto_next": self.next_voice(mapping), "auto_recent": list(self.auto_recent),
                "auto_target": self.target_count() if self.auto_voices else 0,
                "version": self.version, "target_count": self.target_count(),
                "voices": self.eligible_voices(),
                "seen": sorted(self.seen), "mapping": mapping,
                "cells": [{"slave":s,"cell":c,"id":oid} for (s,c),oid in self.cells.items()],
                "slaves": [{"slave":s,"online":s in online} for s in range(1,7)],
                "missing": [oid for oid in mapping if oid.upper() not in self.seen]}

    def register(self, data, *, automatic=False):
        with self.lock:
            if self.auto_register and not automatic:
                raise RuntimeError("자동 등록을 중지한 뒤 수동 등록·교체를 진행하세요")
            if not self.active:
                raise RuntimeError("점검 시작 후 오브를 읽혀 주세요")
            self.snapshot()
            oid = str(data.get("id", "")).upper().strip()
            if oid not in self.cells.values():
                raise ValueError("현재 슬롯에서 읽힌 ID를 선택하세요")
            mapping = store.load_mapping(self.path)
            if oid in mapping:
                raise ValueError("이미 등록된 ID입니다")
            old = data.get("replace_id")
            if old:
                if old not in mapping:
                    raise ValueError("교체할 기존 ID가 없습니다")
                entry = dict(mapping[old])
            else:
                entry = {"voice": store.validate_voice(str(data.get("voice", "")))}
                if self.version == "male" and entry["voice"] not in self.eligible_voices():
                    raise ValueError("남자 아이돌 버전에 포함된 아티스트를 선택하세요")
            backup = self.backup()
            if old:
                del mapping[old]
            mapping[oid] = entry
            versions = store.load_orb_versions(self.path)
            if old and versions is not None:
                for other in versions["mappings"].values():
                    if old in other:
                        other[oid] = other.pop(old)
                versions["mappings"][versions["active"]] = mapping
                store.save_config(versions, store.orb_versions_path(self.path))
            else:
                store.save_mapping(mapping, self.path)
            return {"ok": True, "backup": str(backup)}
