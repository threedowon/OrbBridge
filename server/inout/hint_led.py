"""Map hints and PLAY beat cursors to timed slot ring commands."""
import time
import threading
from dataclasses import dataclass
from functools import wraps

from server.text_log import write_log


@dataclass
class _DfestaRelease:
    run: int
    next_at: float
    expires_at: float
    attempts: int = 0
    successes: int = 0


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class HintLedBridge:
    RELEASE_ATTEMPTS = 4
    RELEASE_INTERVAL_S = 0.3
    RELEASE_TTL_S = 2.0

    def __init__(self, send_line, clock=time.perf_counter, *, fade_ms=600, tray_easing="none", log=write_log):
        self._lock = threading.RLock()
        if type(fade_ms) is not int or not 0 <= fade_ms <= 3000:
            raise ValueError("hint_led_fade_ms must be an integer from 0 to 3000")
        self.send_line = lambda command: send_line(f"{command},{fade_ms}" if command.startswith('H') else command)
        if tray_easing not in ("none", "easein", "easeout"):
            raise ValueError("tray_led_easing must be none, easein, or easeout")
        self.tray_easing = ("none", "easein", "easeout").index(tray_easing)
        self.clock = clock
        self.log = log
        self.run_id = None
        self.latest_run = -1
        self._reserved_run = -1
        self._release = None
        self._last_release_run = None
        self.mode = None
        self.tray_active = False
        self.dfesta_held = False
        self.breath_command = None
        self.breath_sent_at = 0

    @_serialized
    def cancel_release(self, reason):
        """Cancel only pending retries, without changing the current LED effect."""
        pending = self._release
        if pending is None:
            return
        self._release = None
        failures = pending.attempts - pending.successes
        level = 'WARN' if failures else 'INFO'
        self.log(f'[led] [{level}] DFESTA 복귀 명령 전송 집계 | run={pending.run} | '
                 f'attempts={pending.attempts}/{self.RELEASE_ATTEMPTS} | '
                 f'usb_ok={pending.successes} | usb_failed={failures} | '
                 f'종료={reason} | 슬레이브 수신·적용 미확인')

    @_serialized
    def prepare_playback(self, run_id):
        # Reserve ownership before the asynchronous start callback/MIDI gate.
        # A late cancellation of the previous run must not re-arm its retries.
        if run_id > max(self.latest_run, self._reserved_run):
            self._reserved_run = run_id
            self.cancel_release('새 재생')

    def _begin_release(self):
        if self.latest_run < self._reserved_run or self._last_release_run == self.latest_run:
            return
        self.cancel_release('새 복귀 요청')
        self._last_release_run = self.latest_run
        now = self.clock()
        self._release = _DfestaRelease(self.latest_run, now, now + self.RELEASE_TTL_S)
        self._retry_release(now)

    def _retry_release(self, now):
        pending = self._release
        if pending is None:
            return
        if now >= pending.expires_at:
            self.cancel_release('재전송 기한 만료')
            return
        if now < pending.next_at:
            return
        # One attempt per poll; a delayed poll must never burst onto RS485.
        first_failure = pending.attempts == pending.successes
        pending.attempts += 1
        if self.send_line('H0,0,5'):
            pending.successes += 1
        elif first_failure:
            self.log(f'[led] [ERROR] DFESTA 복귀 명령 USB 전송 실패 | run={pending.run} | '
                     f'attempt={pending.attempts}/{self.RELEASE_ATTEMPTS}')
        pending.next_at = self.clock() + self.RELEASE_INTERVAL_S
        # A successful USB write is not a slave ACK: still send the remaining
        # bounded repeats to cover a transient loss on the downstream bus.
        if pending.attempts >= self.RELEASE_ATTEMPTS:
            self.cancel_release('재전송 종료')

    @_serialized
    def sync_ready(self, snapshot, playback, inspection=False):
        if inspection:
            self.cancel_release('오브 점검')
        elif playback.get('active') and self._release is not None:
            run = playback.get('run_id')
            if run is None or run > self._release.run:
                self.cancel_release('새 재생')
        self._retry_release(self.clock())
        columns = ['0'] * 12
        if (not inspection and not self.dfesta_held and snapshot.get('completion', {}).get('waiting_for_dfesta')
                and not playback.get('active') and self.run_id is None):
            for cell in snapshot.get('cells', {}).values():
                if cell.get('result') != 'correct':
                    continue
                slave, slot = cell.get('slave'), cell.get('cell')
                if type(slave) is int and type(slot) is int and 1 <= slave <= 6 and 1 <= slot <= 16:
                    columns[(slave - 1) * 2 + (slot - 1) // 8] = str((slot - 1) % 8 + 1)
        command = 'B' + ''.join(columns)
        now = self.clock()
        if command != self.breath_command or (command != 'B000000000000' and now - self.breath_sent_at >= 0.5):
            if self.send_line(command):
                self.breath_command, self.breath_sent_at = command, now

    @_serialized
    def cancel_dfesta(self, run_id):
        # The scheduler's start/beat callbacks may still be queued. Invalidate
        # this run before releasing the lights so delayed cues cannot relight it.
        if run_id < max(self.latest_run, self._reserved_run):
            return
        self.latest_run = run_id
        self.run_id = None
        self.dfesta_held = False
        self._begin_release()

    @_serialized
    def observe_snapshot(self, previous, current, action):
        game_tray_removed = previous.get('state') == 'RUNNING' and (current.get('end_reason') or current.get('cleanup_end_reason')) == 'tray_removed'
        if ((self.dfesta_held or self._release is not None) and action == 'reset') or game_tray_removed:
            # Ignore subsequent queued beats/end from this run. H4 holds blackout.
            self.run_id = None
            self.dfesta_held = False
            self.tray_active = False
            self.cancel_release('리셋/트레이 정리')
            self.send_line('H0,0,4')
        elif self.dfesta_held and current.get('is_correct') is False:
            # Stop this run's visual cues, including queued beats/end, while the
            # slave fades the accumulated result back to ordinary orb sensing.
            self.run_id = None
            self.dfesta_held = False
            self._begin_release()

    @_serialized
    def tray_transition(self, previous, current, snapshot):
        if previous != current:
            self.cancel_release('게임 상태 변경')
        if current == 'RUNNING' and previous != 'RUNNING':
            initial = snapshot.get('initial_hint') or {}
            remaining = float(initial.get('remaining_s', 0))
            duration = min(60000, max(0, int((remaining - 1.0) * 1000)))
            if initial.get('pending') and duration > 0:
                self.tray_active = True
                self.send_line(f'T{duration}' + (f',{self.tray_easing}' if self.tray_easing else ''))
        elif current != 'RUNNING' and self.tray_active:
            self.tray_active = False
            self.send_line('H0,0,0')

    @_serialized
    def handle(self, event):
        run = int(event["run_id"])
        kind = event["type"]
        if kind == "start":
            if run <= self.latest_run or run < self._reserved_run:
                return
            self.cancel_release('새 재생')
            self.latest_run = run
            self.tray_active = False
            was_active = self.run_id is not None or self.dfesta_held
            self.mode = event["mode"]
            self.dfesta_held = self.mode == "dfesta"
            enabled = self.mode in ("play", "dfesta") or (event.get("hint_led") and self.mode == "listen")
            self.run_id = run if enabled else None
            if self.run_id is not None:
                if self.mode != "play":
                    self.send_line("H0,0,1")
            elif was_active:
                self.send_line("H0,0,0")
            return
        if run != self.run_id:
            return
        if kind == "end":
            # A natural hint ending lets the final note decay; cancellation is immediate.
            if self.mode != "dfesta" or event.get("cancelled"):
                self.dfesta_held = False
                self.send_line("H0,0,3" if self.mode == "listen" and not event.get("cancelled") else "H0,0,0")
            self.run_id = None
        elif kind == "beat":
            notes = event.get("notes") or []
            step = int(event["step"])
            # Do not replay an old visual cue delayed in the event queue.
            elapsed = max(0, (self.clock() - event.get("emitted_at", self.clock())) * 1000)
            duration = min(60000 if self.mode == "dfesta" else 2000, int(event["duration_ms"] - elapsed))
            if self.mode == "dfesta":
                pitch = event.get("answer_pitch")
                if duration > 0 and 0 <= step < 12:
                    mask = (1 << (pitch + 1)) - 1 if type(pitch) is int and 0 <= pitch < 8 else 0
                    self.send_line(f"Q{step // 2 + 1},{step % 2 + 1},{mask},{duration}")
                return
            if self.mode == "play" and duration > 0 and 0 <= step < 12:
                mask = 0
                for note in notes:
                    pitch = note.get("pitch")
                    if type(pitch) is int and 0 <= pitch < 8:
                        mask |= 1 << pitch
                self.send_line(f"P{step // 2 + 1},{step % 2 + 1},{mask},{duration}")
                return
            if not notes or duration <= 0 or not 0 <= step < 12:
                if self.mode != "listen":
                    self.send_line("H0,0,1")
                return
            pitch = notes[0].get("pitch")
            if type(pitch) is not int or not 0 <= pitch <= 7:
                return
            slave = step // 2 + 1
            cell = (step % 2) * 8 + pitch + 1
            self.send_line(f"H{slave},{cell},{duration}")
