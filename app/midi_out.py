"""loopMIDI를 통해 Ableton으로 MIDI를 출력한다.

mido와 python-rtmidi를 사용한다. 오브 voice 키를 portmapping의 그룹·멤버
채널로 변환하고 pitch index 0..7을 MIDI 음표 36..43에 대응시킨다.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from pathlib import Path

from app.app_paths import data_dir

PORTMAPPING_PATH = data_dir() / "portmapping.json"
NOTE_BASE = 36  # 기준 음은 C1이다.
LISTEN_VOICE = "_listen"

_lock = threading.Lock()          # 초기화와 포트 매핑을 보호한다.
_send_lock = threading.Lock()     # rtmidi가 스레드 안전하지 않아 전송을 보호한다.
_ports: dict[str, object] = {}
_map: dict = {}
_ready = False
_init_error: str | None = None

_VOICE_RE = re.compile(r"^([a-zA-Z0-9]+)_(\d+)$")


def _load_portmapping(path: Path | None = None) -> dict:
    p = path or PORTMAPPING_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _output_names() -> list[str]:
    import mido

    return list(mido.get_output_names())


def _open_output(port_name: str):
    import mido

    names = _output_names()
    # 정확히 같은 포트 이름을 먼저 찾는다.
    if port_name in names:
        return mido.open_output(port_name)
    # 대소문자를 무시한 이름과 부분 문자열을 차례로 찾는다.
    lower = port_name.lower()
    for n in names:
        if n.lower() == lower or lower in n.lower() or n.lower() in lower:
            return mido.open_output(n)
    raise RuntimeError(f"MIDI Port '{port_name}' not found. Available: {names}")


def ensure_init(path: Path | None = None) -> tuple[bool, str]:
    """portmapping의 loopMIDI 포트를 열며 중복 호출도 안전하게 처리한다."""
    global _ready, _init_error, _map
    with _lock:
        if _ready:
            return True, ""
        try:
            import mido  # noqa: F401
        except ImportError:
            _init_error = "mido not installed - run: pip install mido python-rtmidi"
            return False, _init_error

        try:
            _map = _load_portmapping(path)
            needed: set[str] = set()
            for key, group in _map.items():
                if not isinstance(group, dict):
                    continue
                if "port" in group:
                    needed.add(str(group["port"]))

            available = _output_names()
            missing: list[str] = []
            for port_name in sorted(needed):
                if port_name in _ports:
                    continue
                try:
                    _ports[port_name] = _open_output(port_name)
                    print(f"[midi] open '{port_name}'")
                except Exception as exc:
                    missing.append(port_name)
                    print(f"[midi] skip '{port_name}': {exc}")

            if not _ports:
                _init_error = (
                    f"No MIDI ports opened. needed={sorted(needed)} available={available}"
                )
                return False, _init_error

            _ready = True
            if missing:
                _init_error = f"Some ports missing: {missing} (available={available})"
                print(f"[midi] WARN: {_init_error}")
            else:
                _init_error = None
            return True, _init_error or ""
        except Exception as exc:
            _init_error = str(exc)
            print(f"[midi] init failed: {exc}")
            return False, _init_error


def shutdown() -> None:
    global _ready
    with _lock:
        for port in _ports.values():
            try:
                port.close()
            except Exception:
                pass
        _ports.clear()
        _ready = False


def voice_group_and_index(voice: str) -> tuple[str, int] | None:
    m = _VOICE_RE.match(voice.strip())
    if not m:
        return None
    return m.group(1).lower(), int(m.group(2))


def resolve_route(voice: str) -> tuple[str, int] | None:
    """voice 키를 포트 이름과 0 기준 채널로 변환한다."""
    voice = (voice or "").strip()
    if not voice:
        return None

    if not _map:
        ensure_init()

    # _listen처럼 멤버 그룹이 없는 단축 매핑을 처리한다.
    if voice in _map:
        entry = _map[voice]
        if isinstance(entry, dict) and "port" in entry and "members" not in entry:
            ch = int(entry.get("channel", 1))
            return str(entry["port"]), max(0, min(15, ch - 1))

    if voice in ("listen", LISTEN_VOICE):
        if LISTEN_VOICE in _map:
            return resolve_route(LISTEN_VOICE)
        return None

    gi = voice_group_and_index(voice)
    if not gi:
        return None
    group, member_num = gi
    group_info = _map.get(group)
    if not isinstance(group_info, dict):
        return None
    port = str(group_info.get("port") or group)
    members = group_info.get("members") or {}

    try:
        from app.voice_store import load_voice_catalog, normalize_profile

        catalog = load_voice_catalog()
        profile = normalize_profile(catalog.get("voices", {}).get(voice) or {})
        labels = profile.get("labels") or {}
        for key in (labels.get("kr"), labels.get("en")):
            if not key:
                continue
            if key in members:
                return port, max(0, min(15, int(members[key]) - 1))
            for mk, mv in members.items():
                if str(mk).lower() == str(key).lower():
                    return port, max(0, min(15, int(mv) - 1))
    except Exception:
        pass

    if members:
        by_ch = sorted(members.items(), key=lambda kv: int(kv[1]))
        idx = max(0, min(len(by_ch) - 1, member_num - 1))
        return port, max(0, min(15, int(by_ch[idx][1]) - 1))

    return port, max(0, min(15, member_num - 1))


def pitch_to_note(pitch_index: int) -> int:
    return NOTE_BASE + max(0, min(7, int(pitch_index)))


def note_name_to_pitch(name: str) -> int | None:
    order = ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")
    up = (name or "").strip().upper()
    if up in order:
        return order.index(up)
    letter_map = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}
    if up in letter_map:
        return letter_map[up]
    return None


def cell_to_pitch(cell: int) -> int:
    row = cell if cell <= 8 else cell - 8
    return max(0, min(7, row - 1))


def _send_note_off(port, note: int, channel: int) -> None:
    import mido

    with _send_lock:
        try:
            port.send(mido.Message("note_off", note=note, velocity=0, channel=channel))
        except Exception:
            pass


def _schedule_note_off(port, note: int, channel: int, duration_ms: float) -> None:
    threading.Timer(
        max(30.0, float(duration_ms)) / 1000.0,
        lambda: _send_note_off(port, note, channel),
    ).start()


def trigger(
    voice: str,
    pitch: int = 0,
    *,
    velocity: int = 127,
    duration_ms: float = 320,
) -> dict:
    """note_on을 보내고 예약된 시각에 note_off를 보낸다."""
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
    if not results:
        return {"ok": False, "error": "empty"}
    return results[0]


def trigger_many(notes: list[dict]) -> list[dict]:
    """여러 note_on을 한 번에 잠가 전송하고 note_off를 예약한다."""
    ok, err = ensure_init()
    if not ok and not _ports:
        return [{"ok": False, "error": err or "midi not ready"} for _ in notes]

    prepared: list[tuple[dict, object, str, int, int, int, float]] = []
    # 각 항목은 결과·포트·포트명·채널·음표·세기·길이로 구성한다.

    for item in notes:
        voice = str(item.get("voice") or LISTEN_VOICE)
        pitch = item.get("pitch")
        if pitch is None and item.get("note"):
            pitch = note_name_to_pitch(str(item["note"]))
        if pitch is None:
            pitch = 0
        velocity = int(item.get("velocity", 127))
        duration_ms = float(item.get("duration_ms", 320))
        route = resolve_route(voice)
        if route is None:
            prepared.append(
                ({"ok": False, "error": f"unknown voice route: {voice}", "voice": voice},
                 None, "", 0, 0, 0, 0.0)
            )
            continue
        port_name, channel = route
        port = _ports.get(port_name)
        if port is None:
            prepared.append(
                ({"ok": False, "error": f"port not open: {port_name}", "voice": voice},
                 None, port_name, channel, 0, 0, 0.0)
            )
            continue
        note = pitch_to_note(int(pitch))
        vel = max(1, min(127, velocity))
        dur = max(30.0, duration_ms)
        prepared.append(
            (
                {
                    "ok": True,
                    "voice": voice,
                    "port": port_name,
                    "channel": channel + 1,
                    "note": note,
                    "pitch": int(pitch),
                    "duration_ms": dur,
                },
                port,
                port_name,
                channel,
                note,
                vel,
                dur,
            )
        )

    # 여러 HTTP 스레드의 note_on이 섞이지 않도록 한 잠금에서 묶어 보낸다.
    import mido

    with _send_lock:
        for meta, port, _pn, channel, note, vel, _dur in prepared:
            if not meta.get("ok") or port is None:
                continue
            try:
                port.send(mido.Message("note_on", note=note, velocity=vel, channel=channel))
            except Exception as exc:
                meta["ok"] = False
                meta["error"] = str(exc)

    for meta, port, _pn, channel, note, _vel, dur in prepared:
        if meta.get("ok") and port is not None:
            _schedule_note_off(port, note, channel, dur)

    return [meta for meta, *_rest in prepared]


def test_all_notes(delay: float = 0.12, duration_ms: float = 100) -> None:
    ok, err = ensure_init()
    if not ok and not _ports:
        raise RuntimeError(err or "midi init failed")

    from app.voice_store import list_catalog_voices, load_mapping

    voices = list_catalog_voices(load_mapping())
    for voice in voices:
        route = resolve_route(voice)
        if not route:
            print(f"  skip {voice} (no route)")
            continue
        if route[0] not in _ports:
            print(f"  skip {voice} (port '{route[0]}' not open)")
            continue
        print(f"=== {voice} -> {route[0]} ch{route[1] + 1} ===")
        for pitch in range(8):
            r = trigger(voice, pitch, duration_ms=duration_ms)
            print(f"  pitch {pitch}: {r}")
            time.sleep(delay)


def main() -> int:
    ap = argparse.ArgumentParser(description="OrbBridge MIDI -> loopMIDI test")
    ap.add_argument("--list", action="store_true", help="List MIDI output devices")
    ap.add_argument("--test", action="store_true", help="Trigger all voices x 8 pitches")
    ap.add_argument("--voice", default="", help="Trigger one voice")
    ap.add_argument("--pitch", type=int, default=0)
    ap.add_argument("--duration-ms", type=float, default=400)
    args = ap.parse_args()

    if args.list:
        try:
            for name in _output_names():
                print(f"  {name}")
        except Exception as exc:
            print(exc)
            return 1
        return 0

    if args.test:
        test_all_notes()
        time.sleep(0.5)
        shutdown()
        return 0

    if args.voice:
        print(trigger(args.voice, args.pitch, duration_ms=args.duration_ms))
        time.sleep(args.duration_ms / 1000.0 + 0.05)
        shutdown()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
