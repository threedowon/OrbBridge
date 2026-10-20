"""Map hints and PLAY beat cursors to timed slot ring commands."""
import time


class HintLedBridge:
    def __init__(self, send_line, clock=time.perf_counter):
        self.send_line = send_line
        self.clock = clock
        self.run_id = None
        self.latest_run = -1
        self.mode = None

    def handle(self, event):
        run = int(event["run_id"])
        kind = event["type"]
        if kind == "start":
            if run <= self.latest_run:
                return
            self.latest_run = run
            was_active = self.run_id is not None
            self.mode = event["mode"]
            enabled = self.mode == "play" or (event.get("hint_led") and self.mode == "listen")
            self.run_id = run if enabled else None
            if self.run_id is not None:
                self.send_line("H0,0,1")
            elif was_active:
                self.send_line("H0,0,0")
            return
        if run != self.run_id:
            return
        if kind == "end":
            self.send_line("H0,0,0")
            self.run_id = None
        elif kind == "beat":
            notes = event.get("notes") or []
            step = int(event["step"])
            # Do not replay an old visual cue delayed in the event queue.
            elapsed = max(0, (self.clock() - event.get("emitted_at", self.clock())) * 1000)
            duration = min(2000, int(event["duration_ms"] - elapsed))
            if self.mode == "play" and duration > 0 and 0 <= step < 12:
                mask = 0
                for note in notes:
                    pitch = note.get("pitch")
                    if type(pitch) is int and 0 <= pitch < 8:
                        mask |= 1 << pitch
                self.send_line(f"P{step // 2 + 1},{step % 2 + 1},{mask},{duration}")
                return
            if not notes or duration <= 0 or not 0 <= step < 12:
                self.send_line("H0,0,1")
                return
            pitch = notes[0].get("pitch")
            if type(pitch) is not int or not 0 <= pitch <= 7:
                self.send_line("H0,0,1")
                return
            slave = step // 2 + 1
            cell = (step % 2) * 8 + pitch + 1
            self.send_line(f"H{slave},{cell},{duration}")
