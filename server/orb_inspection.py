"""Physical-only inventory session, independent of game cells."""
import json
import re
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from server import data_store as store

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
        candidates = [voice for voice in self.auto_voices if counts[voice] < 4]
        return min(candidates, key=lambda voice: counts[voice]) if candidates else None

    def begin_registration(self):
        with self.lock:
            if not self.active:
                raise RuntimeError("점검 세션을 먼저 시작하세요")
            from server.voice_catalog import load_voice_catalog
            self.auto_voices = sorted(load_voice_catalog().get("voices", {}))
            if not self.auto_voices:
                raise ValueError("등록할 아티스트 목록이 없습니다")
            if self.next_voice(store.load_mapping(self.path)) is None:
                raise ValueError("모든 아티스트가 이미 4개씩 등록되어 있습니다")
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
            from server.voice_catalog import load_voice_catalog
            return {"mode": self.mode, "started": self.started,
                "auto_register": self.auto_register, "auto_error": self.auto_error,
                "auto_next": self.next_voice(mapping), "auto_recent": list(self.auto_recent),
                "auto_target": len(self.auto_voices) * 4,
                "voices": load_voice_catalog().get("voices", {}),
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
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = self.path.parent / "orb_backups" / (stamp + ".json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(self.path.read_bytes())
            if old:
                del mapping[old]
            mapping[oid] = entry
            temp = self.path.with_suffix(".orb.tmp")
            temp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            temp.replace(self.path)
            return {"ok": True, "backup": str(backup)}
