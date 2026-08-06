"""loopMIDI를 통해 Ableton으로 공연용 MIDI 음표를 출력한다."""

from __future__ import annotations

import heapq
import itertools
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from app.app_paths import data_dir


PORTMAPPING_PATH = data_dir() / "portmapping.json"
NOTE_BASE = 36
LISTEN_VOICE = "_listen"

_init_lock = threading.RLock()
_ports: dict[str, object] = {}
_port_locks: dict[str, threading.Lock] = {}
_map: dict = {}
_ready = False
_init_error: str | None = None

# note_off는 음표마다 Timer를 만들지 않고 작업 스레드 하나가 처리한다.
_note_condition = threading.Condition()
_off_heap: list[tuple[float, int, tuple[str, int, int]]] = []
_active_notes: dict[tuple[str, int, int], int] = {}
_generations = itertools.count(1)
_off_thread: threading.Thread | None = None
_off_thread_stop = False

_VOICE_RE = re.compile(r"^([a-zA-Z0-9]+)_(\d+)$")


@dataclass
class _PreparedNote:
    meta: dict
    port_name: str
    port: object
    channel: int
    note: int
    velocity: int
    duration_ms: float


def _load_portmapping(path: Path | None = None) -> dict:
    with (path or PORTMAPPING_PATH).open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("portmapping must be an object")
    return document


def output_names() -> list[str]:
    """운영 상태와 진단 UI에서 사용할 MIDI 출력 장치 목록을 반환한다."""

    import mido

    return list(mido.get_output_names())


def _matching_output_name(configured_name: str, available: list[str]) -> str:
    if configured_name in available:
        return configured_name
    lowered = configured_name.lower()
    for actual in available:
        actual_lower = actual.lower()
        if actual_lower == lowered or lowered in actual_lower or actual_lower in lowered:
            return actual
    raise RuntimeError(
        f"MIDI Port '{configured_name}' not found. Available: {available}"
    )


def _start_note_worker() -> None:
    global _off_thread, _off_thread_stop
    with _note_condition:
        if _off_thread is not None and _off_thread.is_alive():
            return
        _off_thread_stop = False
        _off_thread = threading.Thread(
            target=_note_off_worker,
            daemon=True,
            name="orbbridge-midi-note-off",
        )
        _off_thread.start()


def ensure_init(path: Path | None = None) -> tuple[bool, str]:
    """portmapping의 loopMIDI 포트를 열며 중복 호출도 안전하게 처리한다."""

    global _ready, _init_error, _map
    with _init_lock:
        if _ready:
            return True, _init_error or ""
        try:
            import mido
        except ImportError:
            _init_error = "mido not installed - run: pip install mido python-rtmidi"
            return False, _init_error

        try:
            _map = _load_portmapping(path)
            needed = sorted(
                {
                    str(entry["port"])
                    for entry in _map.values()
                    if isinstance(entry, dict) and entry.get("port")
                }
            )
            available = output_names()
            missing: list[str] = []
            for configured_name in needed:
                try:
                    actual_name = _matching_output_name(configured_name, available)
                    _ports[configured_name] = mido.open_output(actual_name)
                    _port_locks[configured_name] = threading.Lock()
                    print(f"[midi] open '{configured_name}'")
                except Exception as exc:
                    missing.append(configured_name)
                    print(f"[midi] skip '{configured_name}': {exc}")

            if not _ports:
                _init_error = (
                    f"No MIDI ports opened. needed={needed} available={available}"
                )
                return False, _init_error

            _ready = True
            _init_error = (
                f"Some ports missing: {missing} (available={available})"
                if missing
                else None
            )
            if _init_error:
                print(f"[midi] WARN: {_init_error}")
            _start_note_worker()
            return True, _init_error or ""
        except Exception as exc:
            _init_error = str(exc)
            print(f"[midi] init failed: {exc}")
            return False, _init_error


def runtime_status() -> dict:
    """Admin이 표시할 포트 초기화 상태를 부작용 없이 반환한다."""

    try:
        available = output_names()
        output_error = ""
    except Exception as exc:
        available = []
        output_error = str(exc)

    with _init_lock:
        required = sorted(
            {
                str(entry["port"])
                for entry in _map.values()
                if isinstance(entry, dict) and entry.get("port")
            }
        )
        return {
            "ready": _ready,
            "error": _init_error or output_error,
            "required_ports": required,
            "available_ports": available,
            "opened_ports": sorted(_ports),
        }


def reinitialize() -> tuple[bool, str]:
    """Admin 재연결 요청에서 모든 포트를 닫고 다시 연다."""

    shutdown()
    return ensure_init()


def _stop_note_worker() -> None:
    global _off_thread, _off_thread_stop
    with _note_condition:
        _off_thread_stop = True
        _note_condition.notify_all()
        thread = _off_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=1.0)
    with _note_condition:
        _off_thread = None


def shutdown() -> None:
    """예약 음표를 끄고 MIDI 포트를 닫는다."""

    global _ready, _init_error, _map
    with _init_lock:
        _ready = False
        _stop_note_worker()
        with _note_condition:
            active_keys = list(_active_notes)
            _active_notes.clear()
            _off_heap.clear()

        try:
            import mido
        except ImportError:
            mido = None

        if mido is not None:
            for port_name, channel, note in active_keys:
                port = _ports.get(port_name)
                lock = _port_locks.get(port_name)
                if port is None or lock is None:
                    continue
                with lock:
                    try:
                        port.send(
                            mido.Message(
                                "note_off", note=note, velocity=0, channel=channel
                            )
                        )
                    except Exception:
                        pass

        for port in _ports.values():
            try:
                port.close()
            except Exception:
                pass
        _ports.clear()
        _port_locks.clear()
        _map = {}
        _init_error = None


def voice_group_and_index(voice: str) -> tuple[str, int] | None:
    match = _VOICE_RE.match(str(voice or "").strip())
    if not match:
        return None
    return match.group(1).lower(), int(match.group(2))


def resolve_route(voice: str) -> tuple[str, int] | None:
    """voice 키를 설정된 포트 이름과 0 기준 채널로 변환한다."""

    voice = str(voice or "").strip()
    if not voice:
        return None
    if not _map:
        ensure_init()

    entry = _map.get(voice)
    if isinstance(entry, dict) and entry.get("port") and "members" not in entry:
        channel = max(0, min(15, int(entry.get("channel", 1)) - 1))
        return str(entry["port"]), channel
    if voice == "listen":
        return resolve_route(LISTEN_VOICE)

    parsed = voice_group_and_index(voice)
    if not parsed:
        return None
    group, member_number = parsed
    group_info = _map.get(group)
    if not isinstance(group_info, dict):
        return None

    port_name = str(group_info.get("port") or group)
    members = group_info.get("members") or {}

    # voice_catalog의 실제 이름이 portmapping의 멤버 키와 같으면 그 채널을 우선한다.
    try:
        from app.voice_catalog import load_voice_catalog, normalize_profile

        profile = normalize_profile(
            load_voice_catalog().get("voices", {}).get(voice) or {}
        )
        labels = profile.get("labels") or {}
        for label in (labels.get("kr"), labels.get("en")):
            if not label:
                continue
            for member_name, channel in members.items():
                if str(member_name).lower() == str(label).lower():
                    return port_name, max(0, min(15, int(channel) - 1))
    except (OSError, ValueError, TypeError):
        pass

    if members:
        ordered = sorted(members.items(), key=lambda item: int(item[1]))
        index = max(0, min(len(ordered) - 1, member_number - 1))
        return port_name, max(0, min(15, int(ordered[index][1]) - 1))
    return port_name, max(0, min(15, member_number - 1))


def pitch_to_note(pitch_index: int) -> int:
    return NOTE_BASE + max(0, min(7, int(pitch_index)))


def cell_to_pitch(cell: int) -> int:
    return max(0, min(7, (int(cell) - 1) % 8))


def _invalidate_generation(key: tuple[str, int, int], generation: int) -> None:
    with _note_condition:
        if _active_notes.get(key) == generation:
            _active_notes.pop(key, None)


def _activate_note(
    key: tuple[str, int, int],
    duration_ms: float,
) -> tuple[int, bool]:
    with _note_condition:
        was_active = key in _active_notes
        generation = next(_generations)
        _active_notes[key] = generation
        heapq.heappush(
            _off_heap,
            (time.monotonic() + duration_ms / 1000.0, generation, key),
        )
        _note_condition.notify()
        return generation, was_active


def _note_off_worker() -> None:
    import mido

    while True:
        with _note_condition:
            while True:
                if _off_thread_stop:
                    return
                if not _off_heap:
                    _note_condition.wait()
                    continue
                deadline, generation, key = _off_heap[0]
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    _note_condition.wait(remaining)
                    continue
                heapq.heappop(_off_heap)
                if _active_notes.get(key) != generation:
                    continue
                break

        port_name, channel, note = key
        port = _ports.get(port_name)
        lock = _port_locks.get(port_name)
        if port is None or lock is None:
            _invalidate_generation(key, generation)
            continue

        # 포트 잠금 뒤 generation을 다시 확인해 새 note_on을 끊는 경합을 막는다.
        with lock:
            with _note_condition:
                if _active_notes.get(key) != generation:
                    continue
                _active_notes.pop(key, None)
            try:
                port.send(
                    mido.Message("note_off", note=note, velocity=0, channel=channel)
                )
            except Exception:
                pass


def _send_port_group(group: list[_PreparedNote]) -> None:
    import mido

    if not group:
        return
    lock = _port_locks[group[0].port_name]
    with lock:
        for item in group:
            key = (item.port_name, item.channel, item.note)
            generation, was_active = _activate_note(key, item.duration_ms)
            try:
                # 같은 음 재트리거 전에 이전 음을 닫아 Ableton의 note 상태를 정리한다.
                if was_active:
                    item.port.send(
                        mido.Message(
                            "note_off",
                            note=item.note,
                            velocity=0,
                            channel=item.channel,
                        )
                    )
                item.port.send(
                    mido.Message(
                        "note_on",
                        note=item.note,
                        velocity=item.velocity,
                        channel=item.channel,
                    )
                )
            except Exception as exc:
                _invalidate_generation(key, generation)
                item.meta["ok"] = False
                item.meta["error"] = str(exc)


def trigger(
    voice: str,
    pitch: int = 0,
    *,
    velocity: int = 127,
    duration_ms: float = 320,
) -> dict:
    """오브 1개 배치용 단음 편의 함수이다."""

    results = trigger_many(
        [
            {
                "voice": voice,
                "pitch": pitch,
                "velocity": velocity,
                "duration_ms": duration_ms,
            }
        ]
    )
    return results[0] if results else {"ok": False, "error": "empty"}


def trigger_many(notes: list[dict]) -> list[dict]:
    """같은 박자의 여러 음을 포트별 묶음으로 병렬 전송한다."""

    if not notes:
        return []
    ok, error = ensure_init()
    if not ok and not _ports:
        return [{"ok": False, "error": error or "midi not ready"} for _ in notes]

    results: list[dict] = [{} for _ in notes]
    groups: dict[str, list[_PreparedNote]] = {}
    for index, raw in enumerate(notes):
        if not isinstance(raw, dict):
            results[index] = {"ok": False, "error": "note must be an object"}
            continue
        voice = str(raw.get("voice") or LISTEN_VOICE)
        try:
            pitch = int(raw.get("pitch", 0))
            velocity = max(1, min(127, int(raw.get("velocity", 127))))
            duration_ms = max(30.0, float(raw.get("duration_ms", 320)))
            if not isfinite(duration_ms):
                raise ValueError
        except (TypeError, ValueError):
            results[index] = {
                "ok": False,
                "voice": voice,
                "error": "invalid pitch, velocity, or duration_ms",
            }
            continue

        route = resolve_route(voice)
        if route is None:
            results[index] = {
                "ok": False,
                "voice": voice,
                "error": f"unknown voice route: {voice}",
            }
            continue
        port_name, channel = route
        port = _ports.get(port_name)
        if port is None:
            results[index] = {
                "ok": False,
                "voice": voice,
                "error": f"port not open: {port_name}",
            }
            continue

        note = pitch_to_note(pitch)
        meta = {
            "ok": True,
            "voice": voice,
            "port": port_name,
            "channel": channel + 1,
            "note": note,
            "pitch": max(0, min(7, pitch)),
            "duration_ms": duration_ms,
        }
        results[index] = meta
        groups.setdefault(port_name, []).append(
            _PreparedNote(
                meta=meta,
                port_name=port_name,
                port=port,
                channel=channel,
                note=note,
                velocity=velocity,
                duration_ms=duration_ms,
            )
        )

    if len(groups) == 1:
        _send_port_group(next(iter(groups.values())))
    elif groups:
        with ThreadPoolExecutor(
            max_workers=len(groups),
            thread_name_prefix="orbbridge-midi-port",
        ) as executor:
            futures = [executor.submit(_send_port_group, group) for group in groups.values()]
            for future in futures:
                future.result()
    return results
