"""공연용 MIDI 출력기를 CLI와 Admin에서 진단한다."""

from __future__ import annotations

import argparse
import threading
import time
from copy import deepcopy

from server.inout import midi_out
from server.voice_catalog import list_catalog_voices, load_voice_catalog


_job_lock = threading.Lock()
_job_stop = threading.Event()
_job_serial = 0
_job = {
    "id": 0,
    "running": False,
    "mode": None,
    "completed": 0,
    "total": 0,
    "failed": 0,
    "stopped": False,
    "current_voice": "",
    "current_pitch": None,
    "results": [],
    "error": "",
}


def _job_snapshot() -> dict:
    with _job_lock:
        return deepcopy(_job)


def _test_voices() -> list[str]:
    """오브 등록 여부와 관계없이 전체 멤버를 그룹·번호 순서로 반환한다."""

    names = set(list_catalog_voices())
    names.update(name.strip() for name in load_voice_catalog()["voices"] if name.strip())

    def sort_key(name: str) -> tuple:
        group, _, member = name.rpartition("_")
        return (group.lower(), int(member), name) if member.isdigit() else (name.lower(), 0, name)

    return sorted(names, key=sort_key)


def status_payload(*, initialize: bool = False) -> dict:
    """MIDI 포트·voice·진단 작업 상태를 Admin 형식으로 반환한다."""

    init_error = ""
    if initialize:
        _ok, init_error = midi_out.ensure_init()
    status = midi_out.runtime_status()
    if init_error and not status.get("error"):
        status["error"] = init_error
    return {
        "ok": bool(status.get("ready")),
        "midi": status,
        "voices": _test_voices(),
        "job": _job_snapshot(),
    }


def reconnect() -> dict:
    """진단 작업이 없을 때 MIDI 포트를 다시 연다."""

    if _job_snapshot()["running"]:
        raise RuntimeError("MIDI 테스트가 실행 중입니다.")
    midi_out.reinitialize()
    return status_payload()


def _update_job(job_id: int, **values) -> None:
    with _job_lock:
        if _job["id"] == job_id:
            _job.update(values)


def _append_results(job_id: int, results: list[dict]) -> None:
    with _job_lock:
        if _job["id"] != job_id:
            return
        _job["completed"] += len(results)
        _job["failed"] += sum(not result.get("ok") for result in results)
        _job["results"] = (_job["results"] + deepcopy(results))[-64:]


def _run_test(
    job_id: int,
    mode: str,
    voice: str,
    pitch: int,
    duration_ms: float,
    delay_ms: float,
    voices: list[str],
) -> None:
    try:
        notes = (
            [(voice, pitch)] if mode == "note" else
            [(current_voice, current_pitch)
             for current_voice in (voices if mode == "all_voices" else [voice])
             for current_pitch in range(8)]
        )
        # 전체 진단에서는 음 길이가 순회 간격보다 길어도 음을 겹치지 않는다.
        interval_ms = max(duration_ms, delay_ms) if mode == "all_voices" else delay_ms
        for index, (current_voice, current_pitch) in enumerate(notes):
            if _job_stop.is_set():
                break
            _update_job(job_id, current_voice=current_voice, current_pitch=current_pitch)
            _append_results(
                job_id,
                [midi_out.trigger(current_voice, current_pitch, duration_ms=duration_ms)],
            )
            wait_ms = interval_ms if index < len(notes) - 1 else duration_ms
            if _job_stop.wait(wait_ms / 1000.0):
                break
    except Exception as exc:
        _update_job(job_id, error=str(exc))
    finally:
        _update_job(job_id, running=False, stopped=_job_stop.is_set())


def start_test(payload: dict) -> dict:
    """단음·선택 voice 8음·전체 voice 8음 순회를 시작한다."""

    global _job_serial, _job
    mode = str(payload.get("mode") or "note").strip().lower()
    if mode not in {"note", "voice", "all_voices"}:
        raise ValueError("mode must be note, voice, or all_voices")

    voices = _test_voices()
    voice = str(payload.get("voice") or (voices[0] if voices else "")).strip()
    if mode in {"note", "voice"} and voice not in voices:
        raise ValueError(f"unknown voice: {voice}")
    if mode == "all_voices" and not voices:
        raise ValueError("등록된 voice가 없습니다.")

    try:
        pitch = int(payload.get("pitch", 0))
        duration_ms = float(payload.get("duration_ms", 180))
        delay_ms = float(payload.get("delay_ms", 220))
    except (TypeError, ValueError) as exc:
        raise ValueError("pitch, duration_ms, delay_ms must be numbers") from exc
    if not 0 <= pitch <= 7:
        raise ValueError("pitch must be 0..7")
    if not 30 <= duration_ms <= 5000:
        raise ValueError("duration_ms must be 30..5000")
    if not 30 <= delay_ms <= 5000:
        raise ValueError("delay_ms must be 30..5000")

    ok, error = midi_out.ensure_init()
    if not ok:
        raise RuntimeError(error or "MIDI 초기화 실패")

    with _job_lock:
        if _job["running"]:
            raise RuntimeError("MIDI 테스트가 이미 실행 중입니다.")
        _job_serial += 1
        job_id = _job_serial
        total = 1 if mode == "note" else 8 if mode == "voice" else len(voices) * 8
        _job = {
            "id": job_id,
            "running": True,
            "mode": mode,
            "completed": 0,
            "total": total,
            "failed": 0,
            "stopped": False,
            "current_voice": "",
            "current_pitch": None,
            "results": [],
            "error": "",
        }
        _job_stop.clear()

    threading.Thread(
        target=_run_test,
        args=(job_id, mode, voice, pitch, duration_ms, delay_ms, voices),
        daemon=True,
        name=f"orbbridge-midi-debug-{job_id}",
    ).start()
    return _job_snapshot()


def stop_test() -> dict:
    _job_stop.set()
    return _job_snapshot()


def _wait_for_job() -> dict:
    while True:
        snapshot = _job_snapshot()
        if not snapshot["running"]:
            return snapshot
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description="OrbBridge MIDI 진단")
    parser.add_argument("--list", action="store_true", help="포트와 voice 목록")
    parser.add_argument("--voice", default="", help="테스트할 voice")
    parser.add_argument("--pitch", type=int, default=0, help="0..7")
    parser.add_argument("--all-notes", action="store_true", help="선택 voice의 8음 순회")
    parser.add_argument("--all-voices", action="store_true", help="전체 voice의 C~C high 8음을 멤버별로 순회")
    parser.add_argument("--duration-ms", type=float, default=180)
    parser.add_argument("--delay-ms", type=float, default=220)
    args = parser.parse_args()

    try:
        if args.list:
            status = status_payload(initialize=True)
            print("available:", ", ".join(status["midi"]["available_ports"]) or "none")
            print("opened:", ", ".join(status["midi"]["opened_ports"]) or "none")
            print("voices:", ", ".join(status["voices"]) or "none")
            return 0 if status["ok"] else 1

        mode = "all_voices" if args.all_voices else "voice" if args.all_notes else "note"
        job = start_test(
            {
                "mode": mode,
                "voice": args.voice,
                "pitch": args.pitch,
                "duration_ms": args.duration_ms,
                "delay_ms": args.delay_ms,
            }
        )
        print(f"[midi debug] job {job['id']} {mode}")
        finished = _wait_for_job()
        for result in finished["results"]:
            print(result)
        return 1 if finished["error"] or finished["failed"] else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[midi debug] {exc}")
        return 1
    finally:
        midi_out.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
