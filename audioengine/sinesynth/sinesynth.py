"""
SineSynth
================================
에이블톤이 설치되지 않은 테스트용 PC에서 Python -> loopMIDI 파이프라인을
검증하기 위한 로컬 대체 프로그램이다.

기능:
  - portmapping.json에 정의된 loopMIDI 입력 포트를 실행 시 한 번 연다
  - 수신된 포트/채널을 그룹/멤버 이름으로 변환한다
  - note_on / note_off마다 사인파를 재생한다
  - 로그를 고정 열 너비의 테이블로 표시한다
  - 마스터 피치와 MIDI 36~43 각 음의 개별 오프셋을 조정한다
  - MIDI 36~43 수동 테스트 버튼을 제공한다
  - 설정은 스크립트와 같은 폴더의 sinesynth_settings.json에 저장한다
"""

import importlib.util
import json
import math
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import numpy as np

try:
    import mido
except ImportError:
    mido = None

try:
    import sounddevice as sd
except ImportError:
    sd = None


def _load_app_paths_module():
    """상위 폴더에서 app/app_paths.py를 찾아 파일 경로로 직접 로드한다."""
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "app" / "app_paths.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("app_paths", candidate)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module, ancestor
    return None, None


def _find_existing_portmapping():
    """app_paths를 못 찾았을 때 상위 폴더의 data/portmapping.json을 탐색한다."""
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "data" / "portmapping.json"
        if candidate.is_file():
            return candidate
    return None


_app_paths_module, _found_root = _load_app_paths_module()
if _app_paths_module is not None:
    _DEFAULT_CONFIG_PATH = _app_paths_module.data_dir() / "portmapping.json"
else:
    print("[경고] app/app_paths.py를 찾지 못했다. data/portmapping.json을 직접 탐색한다.")
    _fallback = _find_existing_portmapping()
    if _fallback is not None:
        _DEFAULT_CONFIG_PATH = _fallback
    else:
        _DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "portmapping.json"

SAMPLE_RATE = 48000
ATTACK_TIME = 0.01
RELEASE_TIME = 0.02
DEFAULT_HOLD_TIME = 4.0 #note_off 누락시 4초뒤 off
BLOCK_SIZE = 512

BASE_NOTES = tuple(range(36, 44))
BASE_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G")
DEFAULT_CONFIG_PATH = _DEFAULT_CONFIG_PATH
SETTINGS_PATH = Path(__file__).resolve().with_name("sinesynth_settings.json")


# --------------------------------------------------------------------------
# 설정 / 라우팅 테이블
# --------------------------------------------------------------------------

class Routing:
    """portmapping.json을 로드하고 포트/채널을 그룹/멤버로 해석한다."""

    def __init__(self, config_path: Path):
        with config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        self.groups = {}
        self.port_to_group = {}
        self.group_to_port_index = {}

        unique_ports = {}
        for key, entry in raw.items():
            port_name = str(entry.get("port", key))
            port_key = port_name.lower()

            if port_key not in unique_ports:
                unique_ports[port_key] = len(unique_ports) + 1

            self.groups[key] = entry
            self.port_to_group[port_key] = key
            self.group_to_port_index[key] = unique_ports[port_key]

    def match_loop_port(self, midi_port_name: str):
        """실제 MIDI 입력 포트명에 대응하는 설정 그룹을 반환한다."""
        name_l = midi_port_name.lower()
        for port_key, group_key in self.port_to_group.items():
            if port_key in name_l:
                return group_key
        return None

    def resolve(self, group_key: str, channel: int):
        """channel은 설정 파일과 동일하게 1부터 시작한다."""
        entry = self.groups.get(group_key)
        if entry is None:
            return group_key, f"ch{channel}"

        members = entry.get("members")
        if not members:
            return group_key, f"ch{channel}"

        for name, ch in members.items():
            if ch == channel:
                return group_key, name
        return group_key, f"ch{channel} (미매핑)"

    def port_index(self, group_key: str) -> int:
        return self.group_to_port_index.get(group_key, 0)

    def configured_port_name(self, group_key: str) -> str:
        entry = self.groups.get(group_key, {})
        return str(entry.get("port", group_key))

    def all_group_keys(self):
        return list(self.groups.keys())


# --------------------------------------------------------------------------
# 트랜스포즈 엔진
# --------------------------------------------------------------------------

class Transpose:
    """마스터 오프셋과 MIDI 36~43 개별 오프셋을 관리한다."""

    def __init__(self):
        self.master = 0
        self.per_note = {note: 0 for note in BASE_NOTES}

    def compute(self, note: int):
        """원음에서 최종음, 마스터 가산값, 개별 가산값을 계산한다."""
        master_add = self.master
        individual_add = self.per_note.get(note, 0)
        final = max(0, min(127, note + master_add + individual_add))
        return final, master_add, individual_add


# --------------------------------------------------------------------------
# 사인파 보이스 엔진
# --------------------------------------------------------------------------

def midi_note_to_freq(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


class Voice:
    __slots__ = ("freq", "phase", "target_amp", "releasing", "release_start_t", "born_t")

    def __init__(self, freq, amp):
        self.freq = freq
        self.phase = 0.0
        self.target_amp = amp
        self.releasing = False
        self.release_start_t = None
        self.born_t = time.monotonic()


class SynthEngine:
    """note_on/off 이벤트로 구동되는 간단한 사인파 신디사이저이다."""

    def __init__(self):
        self.voices = {}
        self.lock = threading.Lock()
        self.master_volume = 0.4
        self.muted = False
        self.stream = None

    def start(self):
        if sd is None:
            return
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def note_on(self, key, note, velocity):
        freq = midi_note_to_freq(note)
        amp = (velocity / 127.0) * 0.35
        with self.lock:
            self.voices[key] = Voice(freq, amp)

    def note_off(self, key):
        with self.lock:
            voice = self.voices.get(key)
            if voice is not None and not voice.releasing:
                voice.releasing = True
                voice.release_start_t = time.monotonic()

    def _callback(self, outdata, frames, time_info, status):
        del time_info, status
        buf = np.zeros(frames, dtype=np.float32)
        now = time.monotonic()

        with self.lock:
            dead_keys = []
            for key, voice in self.voices.items():
                if not voice.releasing and (now - voice.born_t) > DEFAULT_HOLD_TIME:
                    voice.releasing = True
                    voice.release_start_t = now

                t = (np.arange(frames) / SAMPLE_RATE) + (
                    voice.phase / (2 * math.pi * voice.freq)
                )
                wave = np.sin(2 * math.pi * voice.freq * t)

                if voice.releasing:
                    elapsed = now - voice.release_start_t
                    env = max(0.0, 1.0 - elapsed / RELEASE_TIME) * voice.target_amp
                    if elapsed >= RELEASE_TIME:
                        dead_keys.append(key)
                else:
                    age = now - voice.born_t
                    env = min(1.0, age / ATTACK_TIME) * voice.target_amp

                buf += (wave * env).astype(np.float32)
                voice.phase = (
                    voice.phase + 2 * math.pi * voice.freq * frames / SAMPLE_RATE
                ) % (2 * math.pi * voice.freq)

            for key in dead_keys:
                del self.voices[key]

        if self.muted:
            buf[:] = 0.0
        buf *= self.master_volume
        np.clip(buf, -1.0, 1.0, out=buf)
        outdata[:, 0] = buf


# --------------------------------------------------------------------------
# MIDI 리스너
# --------------------------------------------------------------------------

class MidiListener:
    """MIDI 입력 포트 하나를 열고 수신 메시지를 큐로 전달한다."""

    def __init__(self, port_name, group_key, event_queue):
        self.port_name = port_name
        self.group_key = group_key
        self.event_queue = event_queue
        self._port = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            with mido.open_input(self.port_name) as port:
                self._port = port
                for msg in port:
                    if self._stop.is_set():
                        break
                    self.event_queue.put((self.group_key, self.port_name, msg))
        except Exception as exc:
            if not self._stop.is_set():
                self.event_queue.put((self.group_key, self.port_name, exc))
        finally:
            self._port = None

    def stop(self):
        self._stop.set()
        port = self._port
        if port is not None:
            try:
                port.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class App(tk.Tk):
    LOG_COLUMNS = (
        "time",
        "port_index",
        "port_name",
        "channel",
        "member",
        "event",
        "base_note",
        "master_add",
        "individual_add",
        "final_note",
        "velocity",
    )

    LOG_HEADINGS = {
        "time": "TIME",
        "port_index": "PORT IDX",
        "port_name": "PORT NAME",
        "channel": "CH",
        "member": "MEMBER NAME",
        "event": "NOTE ON/OFF",
        "base_note": "BASE NOTE",
        "master_add": "PITCH MASTER",
        "individual_add": "PITCH INDIVIDUAL",
        "final_note": "FINAL PITCH",
        "velocity": "VELOCITY",
    }

    LOG_WIDTHS = {
        "time": 76,
        "port_index": 66,
        "port_name": 130,
        "channel": 44,
        "member": 122,
        "event": 88,
        "base_note": 72,
        "master_add": 90,
        "individual_add": 108,
        "final_note": 76,
        "velocity": 66,
    }

    def __init__(self, config_path: Path):
        super().__init__()
        self.title("SineSynth - loopMIDI → 사인파 신디사이저")
        self.geometry("1280x760")
        self.minsize(1080, 650)

        self.config_path = config_path
        self.routing = Routing(config_path)
        self.event_queue = queue.Queue()
        self.synth = SynthEngine()
        self.transpose = Transpose()
        self.listeners = []
        self.manual_active = set()

        if mido is None:
            messagebox.showerror(
                "의존성 누락",
                "mido / python-rtmidi가 설치되어 있지 않다.\n"
                "pip install mido python-rtmidi",
            )
        if sd is None:
            messagebox.showwarning(
                "오디오 비활성화",
                "sounddevice/numpy가 설치되어 있지 않아 로그만 표시된다.\n"
                "pip install sounddevice numpy",
            )

        self._build_ui()
        self._load_settings()
        self.synth.start()
        self._open_ports_once()
        self.after(30, self._poll_events)

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="로그 지우기", command=self._clear_log).pack(side="left")

        self.mute_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top,
            text="음소거",
            variable=self.mute_var,
            command=self._on_mute,
        ).pack(side="left", padx=(12, 0))

        ttk.Label(top, text="볼륨").pack(side="left", padx=(12, 4))
        self.vol_var = tk.DoubleVar(value=self.synth.master_volume)
        ttk.Scale(
            top,
            from_=0,
            to=1,
            variable=self.vol_var,
            command=self._on_volume,
            length=160,
        ).pack(side="left")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(body, padding=4)
        body.add(left, weight=1)

        ttk.Label(left, text="라우팅 테이블", font=("", 10, "bold")).pack(anchor="w")
        route_table = ttk.Treeview(
            left,
            columns=("port_index", "channel", "member"),
            show="tree headings",
            height=15,
        )
        route_table.heading("#0", text="그룹 / 포트")
        route_table.heading("port_index", text="IDX")
        route_table.heading("channel", text="CH")
        route_table.heading("member", text="멤버")
        route_table.column("#0", width=170)
        route_table.column("port_index", width=42, anchor="center", stretch=False)
        route_table.column("channel", width=42, anchor="center", stretch=False)
        route_table.column("member", width=110)

        for key, entry in self.routing.groups.items():
            node = route_table.insert(
                "",
                "end",
                text=f"{key} ({entry.get('port', key)})",
                values=(self.routing.port_index(key), "", ""),
                open=False,
            )
            members = entry.get("members")
            if members:
                for name, channel in sorted(members.items(), key=lambda item: item[1]):
                    route_table.insert(node, "end", text="", values=("", channel, name))
            elif "channel" in entry:
                route_table.insert(
                    node,
                    "end",
                    text="",
                    values=("", entry["channel"], "(제어)"),
                )
        route_table.pack(fill="both", expand=True)

        ttk.Label(
            left,
            text="수동 테스트 (MIDI 36~43)",
            font=("", 10, "bold"),
        ).pack(anchor="w", pady=(10, 3))

        target_row = ttk.Frame(left)
        target_row.pack(fill="x")

        self.test_group = tk.StringVar()
        group_box = ttk.Combobox(
            target_row,
            textvariable=self.test_group,
            values=self.routing.all_group_keys(),
            width=12,
            state="readonly",
        )
        group_box.pack(side="left")
        group_box.bind("<<ComboboxSelected>>", self._refresh_member_choices)

        self.test_member = tk.StringVar()
        self.member_box = ttk.Combobox(
            target_row,
            textvariable=self.test_member,
            width=14,
            state="readonly",
        )
        self.member_box.pack(side="left", padx=(4, 0))

        note_grid = ttk.Frame(left)
        note_grid.pack(fill="x", pady=(6, 0))
        for index, (note, name) in enumerate(zip(BASE_NOTES, BASE_NOTE_NAMES)):
            button = ttk.Button(note_grid, text=f"{note}\n{name}", width=5)
            button.grid(row=0, column=index, padx=2, sticky="ew")
            button.bind(
                "<ButtonPress-1>",
                lambda event, raw_note=note: self._manual_note_on(raw_note),
            )
            button.bind(
                "<ButtonRelease-1>",
                lambda event, raw_note=note: self._manual_note_off(raw_note),
            )
            note_grid.columnconfigure(index, weight=1)

        if self.routing.all_group_keys():
            self.test_group.set(self.routing.all_group_keys()[0])
            self._refresh_member_choices()

        right = ttk.Frame(body, padding=4)
        body.add(right, weight=4)

        ttk.Label(right, text="실시간 MIDI 로그", font=("", 10, "bold")).pack(anchor="w")

        log_frame = ttk.Frame(right)
        log_frame.pack(fill="both", expand=True)

        self.log_table = ttk.Treeview(
            log_frame,
            columns=self.LOG_COLUMNS,
            show="headings",
            selectmode="browse",
        )
        for column in self.LOG_COLUMNS:
            anchor = "w" if column in ("port_name", "member") else "center"
            self.log_table.heading(column, text=self.LOG_HEADINGS[column])
            self.log_table.column(
                column,
                width=self.LOG_WIDTHS[column],
                minwidth=self.LOG_WIDTHS[column],
                anchor=anchor,
                stretch=False,
            )

        y_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_table.yview)
        x_scroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_table.xview)
        self.log_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.log_table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._build_transpose_panel()

    def _build_transpose_panel(self):
        panel = ttk.Labelframe(self, text="피치 설정", padding=8)
        panel.pack(fill="x", padx=8, pady=(0, 8))

        top_row = ttk.Frame(panel)
        top_row.pack(fill="x")

        ttk.Label(top_row, text="마스터 피치 (st, -48~+48)", font=("", 9, "bold")).pack(side="left")
        self.master_pitch_var = tk.IntVar(value=0)
        master_spin = ttk.Spinbox(
            top_row,
            from_=-48,
            to=48,
            textvariable=self.master_pitch_var,
            width=6,
            justify="center",
            command=self._on_master_pitch,
        )
        master_spin.pack(side="left", padx=(6, 0))
        master_spin.bind("<Return>", lambda event: self._on_master_pitch())
        master_spin.bind("<FocusOut>", lambda event: self._on_master_pitch())

        ttk.Button(top_row, text="설정 저장", command=self._save_settings).pack(side="right")
        ttk.Button(top_row, text="피치 초기화", command=self._reset_transpose).pack(side="right", padx=(0, 6))

        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(
            panel,
            text="MIDI 36~43 개별 오프셋 (st, -12~+12)",
            font=("", 9, "bold"),
        ).pack(anchor="w")

        grid = ttk.Frame(panel)
        grid.pack(fill="x", pady=(4, 0))

        self.note_offset_vars = {}
        for index, (note, name) in enumerate(zip(BASE_NOTES, BASE_NOTE_NAMES)):
            cell = ttk.Frame(grid, padding=4, relief="groove", borderwidth=1)
            cell.grid(row=0, column=index, padx=3, pady=3, sticky="nsew")
            ttk.Label(cell, text=f"{note}  {name}", font=("", 9, "bold")).pack()

            var = tk.IntVar(value=0)
            self.note_offset_vars[note] = var
            spin = ttk.Spinbox(
                cell,
                from_=-12,
                to=12,
                textvariable=var,
                width=5,
                justify="center",
                command=lambda raw_note=note: self._on_note_transpose(raw_note),
            )
            spin.pack()
            spin.bind(
                "<Return>",
                lambda event, raw_note=note: self._on_note_transpose(raw_note),
            )
            spin.bind(
                "<FocusOut>",
                lambda event, raw_note=note: self._on_note_transpose(raw_note),
            )
            grid.columnconfigure(index, weight=1)

    def _refresh_member_choices(self, *_):
        entry = self.routing.groups.get(self.test_group.get(), {})
        members = entry.get("members")
        if members:
            names = sorted(members.keys(), key=lambda name: members[name])
            self.member_box.configure(values=names)
            self.test_member.set(names[0])
        else:
            self.member_box.configure(values=[])
            self.test_member.set("")

    # -- 설정 핸들러 ------------------------------------------------------

    @staticmethod
    def _clamp_int(value, minimum, maximum, default=0):
        try:
            parsed = int(value)
        except (tk.TclError, TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _on_master_pitch(self, *_):
        value = self._clamp_int(self.master_pitch_var.get(), -48, 48)
        self.master_pitch_var.set(value)
        self.transpose.master = value

    def _on_note_transpose(self, note, *_):
        value = self._clamp_int(self.note_offset_vars[note].get(), -12, 12)
        self.note_offset_vars[note].set(value)
        self.transpose.per_note[note] = value

    def _reset_transpose(self):
        self.master_pitch_var.set(0)
        self.transpose.master = 0
        for note in BASE_NOTES:
            self.note_offset_vars[note].set(0)
            self.transpose.per_note[note] = 0

    def _load_settings(self):
        if not SETTINGS_PATH.is_file():
            return

        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))

            master = self._clamp_int(data.get("master_pitch", 0), -48, 48)
            self.transpose.master = master
            self.master_pitch_var.set(master)

            offsets = data.get("note_offsets", {})
            for note in BASE_NOTES:
                value = self._clamp_int(offsets.get(str(note), 0), -12, 12)
                self.transpose.per_note[note] = value
                self.note_offset_vars[note].set(value)

            volume = max(0.0, min(1.0, float(data.get("volume", self.synth.master_volume))))
            self.synth.master_volume = volume
            self.vol_var.set(volume)

            muted = bool(data.get("muted", False))
            self.synth.muted = muted
            self.mute_var.set(muted)
        except Exception as exc:
            print(f"[경고] 설정 로드 실패: {exc}")

    def _save_settings(self, show_message=True):
        self._on_master_pitch()
        for note in BASE_NOTES:
            self._on_note_transpose(note)

        data = {
            "master_pitch": self.transpose.master,
            "note_offsets": {
                str(note): self.transpose.per_note[note]
                for note in BASE_NOTES
            },
            "volume": round(float(self.synth.master_volume), 4),
            "muted": bool(self.synth.muted),
        }

        try:
            SETTINGS_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if show_message:
                messagebox.showinfo("저장 완료", f"설정을 저장했다.\n{SETTINGS_PATH}")
        except Exception as exc:
            if show_message:
                messagebox.showerror("저장 실패", str(exc))
            else:
                print(f"[경고] 설정 저장 실패: {exc}")

    # -- 동작 -------------------------------------------------------------

    def _open_ports_once(self):
        """실행 시 한 번만 실제 입력 포트를 조회하고 매칭된 포트를 연다."""
        if mido is None:
            return

        try:
            available = mido.get_input_names()
        except Exception as exc:
            self._log_system_event("MIDI INPUT ERROR", str(exc))
            return

        matched_group_keys = set()
        for port_name in available:
            group_key = self.routing.match_loop_port(port_name)
            if group_key is None or group_key in matched_group_keys:
                continue

            listener = MidiListener(port_name, group_key, self.event_queue)
            listener.start()
            self.listeners.append(listener)
            matched_group_keys.add(group_key)

        for group_key in self.routing.all_group_keys():
            if group_key not in matched_group_keys:
                self._log_system_event(
                    "PORT NOT FOUND",
                    self.routing.configured_port_name(group_key),
                    group_key=group_key,
                )

    def _clear_log(self):
        for item in self.log_table.get_children():
            self.log_table.delete(item)

    def _on_mute(self):
        self.synth.muted = self.mute_var.get()

    def _on_volume(self, _value):
        self.synth.master_volume = float(self.vol_var.get())

    def _manual_target(self):
        group_key = self.test_group.get()
        entry = self.routing.groups.get(group_key, {})
        member = self.test_member.get()
        channel = entry.get("members", {}).get(member, entry.get("channel", 1))
        port_name = self.routing.configured_port_name(group_key)
        return group_key, port_name, int(channel), member or "(제어)"

    def _manual_note_on(self, raw_note):
        group_key, port_name, channel, member = self._manual_target()
        key = ("manual", group_key, channel, raw_note)
        if key in self.manual_active:
            return

        final_note, master_add, individual_add = self.transpose.compute(raw_note)
        self.manual_active.add(key)
        self.synth.note_on(key, final_note, 100)
        self._log_event(
            group_key=group_key,
            port_name=port_name,
            channel=channel,
            member=member,
            event="NOTE_ON",
            base_note=raw_note,
            master_add=master_add,
            individual_add=individual_add,
            final_note=final_note,
            velocity=100,
        )

    def _manual_note_off(self, raw_note):
        group_key, port_name, channel, member = self._manual_target()
        key = ("manual", group_key, channel, raw_note)
        self.manual_active.discard(key)

        final_note, master_add, individual_add = self.transpose.compute(raw_note)
        self.synth.note_off(key)
        self._log_event(
            group_key=group_key,
            port_name=port_name,
            channel=channel,
            member=member,
            event="NOTE_OFF",
            base_note=raw_note,
            master_add=master_add,
            individual_add=individual_add,
            final_note=final_note,
            velocity=0,
        )

    # -- 이벤트 루프 ------------------------------------------------------

    def _poll_events(self):
        try:
            while True:
                group_key, port_name, msg = self.event_queue.get_nowait()

                if isinstance(msg, Exception):
                    self._log_system_event("PORT ERROR", str(msg), group_key=group_key)
                    continue

                if msg.type in ("note_on", "note_off"):
                    channel = msg.channel + 1
                    _, member = self.routing.resolve(group_key, channel)
                    raw_note = msg.note
                    final_note, master_add, individual_add = self.transpose.compute(raw_note)
                    key = (group_key, channel, raw_note)
                    is_on = msg.type == "note_on" and msg.velocity > 0

                    if is_on:
                        self.synth.note_on(key, final_note, msg.velocity)
                    else:
                        self.synth.note_off(key)

                    self._log_event(
                        group_key=group_key,
                        port_name=port_name,
                        channel=channel,
                        member=member,
                        event="NOTE_ON" if is_on else "NOTE_OFF",
                        base_note=raw_note,
                        master_add=master_add,
                        individual_add=individual_add,
                        final_note=final_note,
                        velocity=msg.velocity,
                    )
                elif hasattr(msg, "channel"):
                    channel = msg.channel + 1
                    _, member = self.routing.resolve(group_key, channel)
                    self._log_event(
                        group_key=group_key,
                        port_name=port_name,
                        channel=channel,
                        member=member,
                        event=msg.type.upper(),
                    )
                else:
                    self._log_system_event(msg.type.upper(), str(msg), group_key=group_key)
        except queue.Empty:
            pass

        self.after(30, self._poll_events)

    def _log_event(
        self,
        *,
        group_key,
        port_name,
        channel="-",
        member="-",
        event="-",
        base_note="-",
        master_add="-",
        individual_add="-",
        final_note="-",
        velocity="-",
    ):
        def signed(value):
            return f"{value:+d}" if isinstance(value, int) else value

        values = (
            time.strftime("%H:%M:%S"),
            self.routing.port_index(group_key),
            port_name,
            channel,
            member,
            event,
            base_note,
            signed(master_add),
            signed(individual_add),
            final_note,
            velocity,
        )
        item = self.log_table.insert("", "end", values=values)
        self.log_table.see(item)

    def _log_system_event(self, event, detail, group_key=""):
        self._log_event(
            group_key=group_key,
            port_name=self.routing.configured_port_name(group_key) if group_key else detail,
            event=event,
        )

    def destroy(self):
        self._save_settings(show_message=False)
        for listener in self.listeners:
            listener.stop()
        self.synth.stop()
        super().destroy()


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        print(f"설정 파일을 찾을 수 없다: {config_path}")
        sys.exit(1)

    app = App(config_path)
    app.mainloop()


if __name__ == "__main__":
    main()
