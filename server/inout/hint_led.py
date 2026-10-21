"""Map hints and PLAY beat cursors to timed slot ring commands."""
import time


class HintLedBridge:
    def __init__(self, send_line, clock=time.perf_counter, *, fade_ms=600):
        if type(fade_ms) is not int or not 0 <= fade_ms <= 3000:
            raise ValueError("hint_led_fade_ms must be an integer from 0 to 3000")
        self.send_line = lambda command: send_line(f"{command},{fade_ms}" if command.startswith('H') else command)
        self.clock = clock
        self.run_id = None
        self.latest_run = -1
        self.mode = None
        self.tray_active = False
        self.dfesta_held = False
        self.breath_command = None
        self.breath_sent_at = 0

    def sync_ready(self, snapshot, playback, inspection=False):
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

    def observe_snapshot(self, previous, current, action):
        tray_before = previous.get('tray', {})
        tray_after = current.get('tray', {})
        removed = (tray_before.get('present') is True and tray_after.get('present') is not True)
        if self.dfesta_held and (action == 'reset' or removed):
            # Ignore subsequent queued beats/end from this run. H4 holds blackout.
            self.run_id = None
            self.dfesta_held = False
            self.send_line('H0,0,4')

    def tray_transition(self, previous, current, snapshot):
        if current == 'RUNNING' and previous != 'RUNNING':
            initial = snapshot.get('initial_hint') or {}
            remaining = float(initial.get('remaining_s', 0))
            duration = min(60000, max(0, int((remaining - 1.0) * 1000)))
            if initial.get('pending') and duration > 0:
                self.tray_active = True
                self.send_line(f'T{duration}')
        elif current != 'RUNNING' and self.tray_active:
            self.tray_active = False
            self.send_line('H0,0,0')

    def handle(self, event):
        run = int(event["run_id"])
        kind = event["type"]
        if kind == "start":
            if run <= self.latest_run:
                return
            self.latest_run = run
            self.tray_active = False
            was_active = self.run_id is not None
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
