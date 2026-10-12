"""Minimal fixed-step piano roll for the eight supported sample pitches."""
from __future__ import annotations

from copy import deepcopy
from math import ceil
from queue import Empty, SimpleQueue
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from tools.melody_preview import play_melody, stop_preview
from tools.melody_register_core import (
    MelodyRegisterState, NOTE_CHOICES_UI, NOTE_REST, delete_melody,
    list_melody_ids, load_melody, load_rotation_settings, melody_path,
    new_melody_template, save_melody, save_rotation_settings, validate_melody,
)
from tools.melody_piano_roll import (
    GRID_CHOICES, duration_label, edit_step, grid_beats, set_note,
    snap_duration, step_at, step_spans, twelve_steps,
    MIN_DURATION, MAX_DURATION,
)

C = dict(bg="#202224", panel="#2b2d30", grid="#282b2e", alternate="#2d3033",
         line="#3a3d40", major="#62676b", text="#e0e3e5", muted="#999fa4",
         note="#86bdb7", accent="#efb16a", rest="#454a4e")
RULER, ROW, PAD = 30, 34, 12
BOTTOM = RULER + ROW * 8
VELOCITY_TOP = BOTTOM + 32
HEIGHT = VELOCITY_TOP + 102


class MelodyRegisterApp:
    def __init__(self, state: MelodyRegisterState):
        self.state, self._data, self._active_id = state, None, ""
        self._dirty, self._closing = False, False
        self._selected, self._drag = 0, None
        self._undo, self._redo = [], []
        self._preview_queue = SimpleQueue()
        self._preview_token, self._preview_step = 0, -1
        self.root = tk.Tk()
        self.root.title("Melody Register")
        self.root.geometry("1260x760")
        self.root.minsize(1060, 700)
        self.root.configure(bg=C["bg"])
        self.root.option_add("*Font", ("Segoe UI", 10))
        for name, value in dict(bpm="120", grid="1/16", step_note="C4", step_dur="1",
                                velocity="127", step_lyric="", harmony_a="", harmony_b="",
                                status="멜로디를 선택하거나 새로 만드세요.", summary="", selection="STEP 01 / 12").items():
            setattr(self, name + "_var", tk.StringVar(self.root, value))
        self.snap_var = tk.BooleanVar(self.root, True)
        self.triplet_var = tk.BooleanVar(self.root, False)
        self.zoom_var = tk.IntVar(self.root, 72)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=C["panel"], background=C["panel"],
                        foreground=C["text"], arrowcolor=C["text"], borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", C["panel"])], foreground=[("readonly", C["text"])])
        style.configure("Horizontal.TScrollbar", background=C["major"], troughcolor=C["bg"], borderwidth=0, arrowsize=12)
        self._build()
        self._refresh_list()
        ids = list_melody_ids()
        if ids:
            self._load_melody(ids[0])
        self.root.bind("<Control-s>", lambda e: self._save_current())
        self.canvas.bind("<Control-z>", lambda e: self._history(False))
        self.canvas.bind("<Control-y>", lambda e: self._history(True))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.root.after(30, self._poll_preview)

    def _frame(self, parent, **kw):
        return tk.Frame(parent, bg=C["panel"], **kw)

    def _label(self, parent, text=None, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=C["text"], **kw)

    def _button(self, parent, text, command, accent=False):
        return tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                         bg=C["accent"] if accent else C["rest"], fg=C["bg"] if accent else C["text"],
                         activebackground=C["major"], activeforeground=C["text"],
                         padx=12, pady=5, cursor="hand2", takefocus=False)

    def _entry(self, parent, variable, width=7):
        return tk.Entry(parent, textvariable=variable, width=width, relief="flat", bg=C["bg"],
                        fg=C["text"], insertbackground=C["text"], highlightthickness=1, highlightbackground=C["line"])

    def _combo(self, parent, variable, values, width=7):
        return ttk.Combobox(parent, textvariable=variable, values=values, width=width, state="readonly")

    def _build(self):
        top = self._frame(self.root)
        top.pack(fill="x", padx=12, pady=(12, 8))
        self._label(top, "MELODY REGISTER", font=("Segoe UI", 12, "bold")).pack(side="left", padx=12)
        self._label(top, "BPM").pack(side="left", padx=(20, 6))
        bpm = self._entry(top, self.bpm_var, 5)
        bpm.pack(side="left", pady=10)
        bpm.bind("<Return>", self._commit_bpm)
        bpm.bind("<FocusOut>", self._commit_bpm)
        self._button(top, "▶ 미리듣기", self._preview).pack(side="left", padx=(16, 5))
        self._button(top, "■", self._stop_preview).pack(side="left")
        self._button(top, "저장  Ctrl+S", self._save_current, True).pack(side="right", padx=10)
        self._label(top, textvariable=self.summary_var).pack(side="right", padx=8)
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=12)
        library = self._frame(body, width=174)
        library.pack(side="left", fill="y", padx=(0, 8))
        library.pack_propagate(False)
        self._label(library, "LIBRARY", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=12)
        self.listbox = tk.Listbox(library, bg=C["panel"], fg=C["text"], relief="flat", selectbackground=C["rest"],
                                 selectforeground=C["accent"], bd=0, highlightthickness=0, exportselection=False)
        self.listbox.pack(fill="both", expand=True, padx=8)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        for label, command in (("+ 새 멜로디", self._new_melody), ("곡 설정", self._metadata),
                               ("게임 재생 순서", self._rotation), ("멜로디 삭제", self._delete_melody)):
            self._button(library, label, command).pack(fill="x", padx=8, pady=4)
        editor = self._frame(body)
        editor.pack(side="left", fill="both", expand=True)
        toolbar = self._frame(editor)
        toolbar.pack(fill="x", padx=10, pady=8)
        self._label(toolbar, "GRID").pack(side="left", padx=(0, 6))
        grid = self._combo(toolbar, self.grid_var, GRID_CHOICES, 5)
        grid.pack(side="left")
        grid.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        for text, var in (("Triplet", self.triplet_var), ("Snap to grid", self.snap_var)):
            tk.Checkbutton(toolbar, text=text, variable=var, command=self._redraw, bg=C["panel"], fg=C["text"],
                           selectcolor=C["bg"], activebackground=C["panel"], activeforeground=C["text"]).pack(side="left", padx=6)
        self._button(toolbar, "↶", lambda: self._history(False)).pack(side="left", padx=(12, 3))
        self._button(toolbar, "↷", lambda: self._history(True)).pack(side="left")
        self._button(toolbar, "전체 보기", self._fit).pack(side="left", padx=6)
        tk.Scale(toolbar, from_=36, to=144, variable=self.zoom_var, orient="horizontal", command=lambda _: self._redraw(),
                 length=120, showvalue=False, bg=C["panel"], fg=C["text"], troughcolor=C["bg"],
                 highlightthickness=0, sliderrelief="flat").pack(side="right")
        self._label(toolbar, "Zoom").pack(side="right")
        roll = self._frame(editor)
        roll.pack(fill="both", expand=True, padx=10)
        keys = tk.Canvas(roll, width=52, height=HEIGHT, bg=C["panel"], highlightthickness=0)
        keys.pack(side="left", fill="y")
        for i, note in enumerate(NOTE_CHOICES_UI):
            y = RULER + i * ROW
            keys.create_rectangle(0, y, 50, y + ROW, fill=C["rest"], outline=C["panel"])
            keys.create_text(25, y + ROW / 2, text=note, fill=C["text"], font=("Consolas", 10))
        keys.create_text(25, VELOCITY_TOP + 45, text="VEL", fill=C["muted"], font=("Consolas", 9))
        viewport = self._frame(roll)
        viewport.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, height=HEIGHT, bg=C["grid"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(viewport, orient="horizontal", command=self.canvas.xview)
        scroll.pack(fill="x")
        self.canvas.configure(xscrollcommand=scroll.set)
        for event, fn in (("<Configure>", lambda e: self._redraw()), ("<Button-1>", self._press),
                          ("<B1-Motion>", self._motion), ("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None)),
                          ("<Button-3>", self._rest_click), ("<Delete>", lambda e: self._rest_selected()),
                          ("<space>", lambda e: self._preview()), ("<Left>", lambda e: self._select(max(0, self._selected - 1))),
                          ("<Right>", lambda e: self._select(min(11, self._selected + 1)))):
            self.canvas.bind(event, fn)
        inspector = self._frame(editor)
        inspector.pack(fill="x", padx=12, pady=(8, 4))
        self._label(inspector, textvariable=self.selection_var, font=("Consolas", 10)).pack(side="left", padx=(0, 14))
        for label, var, values in (("Note", self.step_note_var, (*NOTE_CHOICES_UI, NOTE_REST)),
                                   ("Length", self.step_dur_var, None), ("Velocity", self.velocity_var, None)):
            self._label(inspector, label).pack(side="left", padx=(0, 5))
            widget = self._combo(inspector, var, values) if values else self._entry(inspector, var)
            widget.pack(side="left", padx=(0, 12))
            widget.bind("<Return>", lambda e: self._apply_step_edit())
        self._button(inspector, "적용", self._apply_step_edit).pack(side="left")
        self._button(inspector, "쉼표", self._rest_selected).pack(side="left", padx=6)
        harmony = self._frame(editor)
        harmony.pack(fill="x", padx=12, pady=(4, 10))
        for label, var in (("화음 A", self.harmony_a_var), ("화음 B", self.harmony_b_var)):
            self._label(harmony, label).pack(side="left", padx=(0, 5))
            self._combo(harmony, var, ("", *NOTE_CHOICES_UI)).pack(side="left", padx=(0, 12))
        self._label(harmony, "가사").pack(side="left", padx=(0, 5))
        self._entry(harmony, self.step_lyric_var, 18).pack(side="left")
        self._label(harmony, "  화음·가사도 적용 버튼으로 반영").pack(side="left")
        self._label(self.root, "클릭: 음 입력  ·  위아래 드래그: 음정  ·  오른쪽 끝 드래그: 길이  ·  아래 막대: 세기  ·  우클릭: 쉼표",
                    anchor="w").pack(fill="x", padx=16, pady=(9, 2))
        self._label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=16, pady=(2, 10))

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for i, name in enumerate(list_melody_ids()):
            self.listbox.insert("end", name)
            if name == self._active_id:
                self.listbox.selection_set(i)

    def _discard_ok(self):
        return not self._dirty or messagebox.askyesno("미저장 변경", "저장하지 않은 변경을 버릴까요?", parent=self.root)

    def _on_list_select(self, event=None):
        selection = self.listbox.curselection()
        if selection:
            name = self.listbox.get(selection[0])
            if name != self._active_id:
                if self._discard_ok():
                    self._load_melody(name)
                else:
                    self._refresh_list()

    def _load_melody(self, name):
        try:
            data = load_melody(name)
            errors = validate_melody(data)
            if errors:
                raise ValueError("\n".join(errors))
            if any(not MIN_DURATION <= s["duration"] <= MAX_DURATION for s in data["steps"]):
                raise ValueError("피아노롤은 step당 1/96–16박을 지원합니다. 원본 파일은 변경하지 않았습니다.")
            count = len(data["steps"])
            data["steps"] = twelve_steps(data["steps"])
        except (OSError, ValueError) as exc:
            messagebox.showerror("열기 실패", str(exc), parent=self.root)
            return
        self._stop_preview()
        self._data, self._active_id, self._dirty = data, name, count != 12
        self._undo.clear()
        self._redo.clear()
        self.bpm_var.set(str(data["bpm"]))
        self._select(0)
        self._refresh_list()
        self.root.after_idle(self._fit)
        self.status_var.set(f"{name}.json · " + (f"{count} step → 빈 칸을 REST로 채워 12 step 구성 (미저장)" if count != 12 else "12 step / C4–C5"))

    def _checkpoint(self):
        self._undo.append(deepcopy(self._data))
        self._undo = self._undo[-80:]
        self._redo.clear()

    def _fit(self):
        if self._data and self.canvas.winfo_width() > 100:
            total = step_spans(self._data["steps"])[-1][1]
            self.zoom_var.set(max(36, min(144, int((self.canvas.winfo_width() - PAD * 2) / total))))
            self.canvas.xview_moveto(0)
            self._redraw()

    def _changed(self):
        self._stop_preview()
        self._dirty = True
        self._select(self._selected)

    def _select(self, index):
        self._selected = index
        if self._data:
            step = self._data["steps"][index]
            self.selection_var.set(f"STEP {index + 1:02d} / 12")
            for var, value in ((self.step_note_var, step["note"]), (self.step_dur_var, duration_label(step["duration"])),
                               (self.velocity_var, str(step.get("velocity", 127))), (self.step_lyric_var, step.get("lyric", ""))):
                var.set(value)
            for var, value in zip((self.harmony_a_var, self.harmony_b_var), step.get("harmony", ("", ""))):
                var.set("" if value == NOTE_REST else value)
        self._redraw()

    def _redraw(self):
        if not hasattr(self, "canvas") or not self._data:
            return
        c = self.canvas
        c.delete("all")
        spans = step_spans(self._data["steps"])
        px, total = self.zoom_var.get(), spans[-1][1]
        width = max(c.winfo_width(), ceil(total * px) + PAD * 2)
        c.configure(scrollregion=(0, 0, width, HEIGHT))
        for row in range(8):
            y = RULER + row * ROW
            c.create_rectangle(0, y, width, y + ROW, fill=C["grid"] if row % 2 else C["alternate"], outline=C["line"])
        quantum = grid_beats(self.grid_var.get(), self.triplet_var.get())
        for i in range(ceil(total / quantum) + 1):
            beat = i * quantum
            x, integer = PAD + beat * px, abs(beat - round(beat)) < 1e-8
            c.create_line(x, RULER, x, BOTTOM, fill=C["major"] if integer else C["line"])
            if integer:
                c.create_text(x + 4, 14, text=str(round(beat) + 1), fill=C["muted"], anchor="w", font=("Consolas", 9))
        c.create_line(0, VELOCITY_TOP + 88, width, VELOCITY_TOP + 88, fill=C["line"])
        for i, ((start, end), step) in enumerate(zip(spans, self._data["steps"])):
            x1, x2 = PAD + start * px, PAD + end * px
            color = C["accent"] if i == self._selected else C["note"]
            if i == self._preview_step:
                c.create_rectangle(x1, RULER, x2, HEIGHT, fill=C["major"], stipple="gray25", outline="")
            note = step["note"]
            if note != NOTE_REST:
                y = RULER + NOTE_CHOICES_UI.index(note) * ROW
                c.create_rectangle(x1 + 1, y + 3, max(x1 + 3, x2 - 2), y + ROW - 3, fill=color, outline="")
                if x2 - x1 > 25:
                    c.create_text(x1 + 6, y + ROW / 2, text=note, fill=C["bg"], anchor="w", font=("Consolas", 9))
                c.create_line(x2 - 5, y + 8, x2 - 5, y + ROW - 8, fill=C["bg"])
            c.create_rectangle(x1, BOTTOM + 3, x2, BOTTOM + 25, fill=C["rest"] if i == self._selected else C["panel"], outline=C["line"])
            if x2 - x1 > 16:
                c.create_text((x1 + x2) / 2, BOTTOM + 14, text=f"{i + 1}" + (" R" if note == NOTE_REST else ""),
                              fill=color if note != NOTE_REST else C["muted"], font=("Consolas", 9))
            bar_x, height = (x1 + x2) / 2, step.get("velocity", 127) / 127 * 74
            c.create_rectangle(bar_x - 3, VELOCITY_TOP + 88 - height, bar_x + 3, VELOCITY_TOP + 88,
                               fill=color if note != NOTE_REST else C["rest"], outline="")
        self.summary_var.set(f"12 steps  ·  {total:g} beats  ·  {total * 60 / self._data['bpm']:.2f}s")
        self.root.title(f"{'● ' if self._dirty else ''}{self._active_id or 'New'} — Melody Register")

    def _press(self, event):
        if not self._data:
            return
        self.canvas.focus_set()
        x = self.canvas.canvasx(event.x)
        index = step_at(self._data["steps"], (x - PAD) / self.zoom_var.get())
        if index is None:
            return
        self._select(index)
        if event.y < RULER or BOTTOM <= event.y < VELOCITY_TOP:
            return
        start, end = step_spans(self._data["steps"])[index]
        row = max(0, min(7, int((event.y - RULER) // ROW)))
        step = self._data["steps"][index]
        mode = "velocity" if event.y >= VELOCITY_TOP else (
            "resize" if step["note"] == NOTE_CHOICES_UI[row] and abs(x - (PAD + end * self.zoom_var.get())) <= 9 else "pitch")
        self._checkpoint()
        self._drag = (mode, index, start)
        self._motion(event)

    def _motion(self, event):
        if not self._drag:
            return
        mode, index, start = self._drag
        step = self._data["steps"][index]
        if mode == "resize":
            beats = (self.canvas.canvasx(event.x) - PAD) / self.zoom_var.get() - start
            step["duration"] = snap_duration(beats, self.grid_var.get(), self.triplet_var.get(), self.snap_var.get())
        elif mode == "pitch":
            set_note(step, NOTE_CHOICES_UI[max(0, min(7, int((event.y - RULER) // ROW)))])
        else:
            step["velocity"] = max(1, min(127, round((VELOCITY_TOP + 88 - event.y) / 74 * 127)))
        self._changed()

    def _rest_click(self, event):
        if self._data:
            i = step_at(self._data["steps"], (self.canvas.canvasx(event.x) - PAD) / self.zoom_var.get())
            if i is not None:
                self._selected = i
                self._rest_selected()

    def _rest_selected(self):
        if self._data:
            self._checkpoint()
            set_note(self._data["steps"][self._selected], NOTE_REST)
            self._changed()

    def _apply_step_edit(self):
        if not self._data:
            return
        candidate = deepcopy(self._data)
        try:
            edit_step(candidate, self._selected, note=self.step_note_var.get(), duration=self.step_dur_var.get(),
                      velocity=self.velocity_var.get(), harmony=[self.harmony_a_var.get(), self.harmony_b_var.get()],
                      lyric=self.step_lyric_var.get())
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            messagebox.showerror("음표 수정", str(exc), parent=self.root)
            return
        self._checkpoint()
        self._data = candidate
        self._changed()

    def _commit_bpm(self, event=None):
        if not self._data:
            return True
        try:
            bpm = int(self.bpm_var.get())
            if not 20 <= bpm <= 300:
                raise ValueError
        except ValueError:
            self.status_var.set("BPM은 20–300 사이의 정수로 입력하세요.")
            return False
        if bpm != self._data["bpm"]:
            self._checkpoint()
            self._data["bpm"] = bpm
            self._changed()
        return True

    def _history(self, redo):
        source, dest = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if source:
            dest.append(deepcopy(self._data))
            self._data = source.pop()
            self.bpm_var.set(str(self._data["bpm"]))
            self._changed()

    def _save_current(self):
        if not self._data or not self._commit_bpm():
            return
        try:
            save_melody(self._data, file_id=self._active_id, state=self.state)
        except (OSError, ValueError) as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self.root)
            return
        self._dirty = False
        self._refresh_list()
        self._redraw()
        self.status_var.set(f"저장됨 · {self._active_id}.json")

    def _new_melody(self):
        if not self._discard_ok():
            return
        name = simpledialog.askstring("새 멜로디", "파일명 / songId", parent=self.root)
        if not name:
            return
        try:
            path = melody_path(name)
            if path.exists():
                raise ValueError("같은 이름의 멜로디가 이미 있습니다.")
        except ValueError as exc:
            messagebox.showerror("새 멜로디", str(exc), parent=self.root)
            return
        self._stop_preview()
        self._active_id, self._data = path.stem, new_melody_template(path.stem)
        self._undo.clear()
        self._redo.clear()
        self.bpm_var.set("120")
        self._dirty = True
        self._select(0)
        self.status_var.set("새 멜로디 · 12개의 빈 슬롯에 클릭으로 음을 입력하세요.")

    def _delete_melody(self):
        if self._active_id and melody_path(self._active_id).exists():
            if messagebox.askyesno("멜로디 삭제", f"{self._active_id}.json을 삭제할까요?", parent=self.root):
                try:
                    delete_melody(self._active_id, self.state)
                    self._load_melody(list_melody_ids()[0])
                except (OSError, ValueError) as exc:
                    messagebox.showerror("삭제 실패", str(exc), parent=self.root)

    def _dialog(self, title):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=C["panel"])
        win.transient(self.root)
        win.grab_set()
        return win

    def _metadata(self):
        if not self._data:
            return
        win = self._dialog("곡 설정")
        variables = {}
        for row, (key, label, value) in enumerate((("title", "Title", self._data.get("title", "")),
            ("artist", "Artist", self._data.get("artist", "")),
            ("count", "DFesta 완료 횟수", self._data.get("game_rules", {}).get("dfesta_presses_required", 1)))):
            variables[key] = tk.StringVar(win, str(value))
            self._label(win, label).grid(row=row, column=0, padx=16, pady=12)
            self._entry(win, variables[key], 24).grid(row=row, column=1, padx=16)

        def apply():
            candidate = deepcopy(self._data)
            candidate.update(title=variables["title"].get(), artist=variables["artist"].get())
            candidate["game_rules"] = {"dfesta_presses_required": variables["count"].get()}
            errors = validate_melody(candidate)
            if errors:
                messagebox.showerror("곡 설정", "\n".join(errors), parent=win)
                return
            self._checkpoint()
            self._data = candidate
            self._changed()
            win.destroy()
        self._button(win, "적용", apply, True).grid(row=3, column=1, pady=14)

    def _rotation(self):
        try:
            settings = load_rotation_settings()
        except (OSError, ValueError) as exc:
            messagebox.showerror("게임 순서", str(exc), parent=self.root)
            return
        win = self._dialog("게임 재생 순서")
        mode = tk.StringVar(win, settings["mode"])
        self._combo(win, mode, ("sequence", "random", "fixed"), 20).pack(padx=16, pady=12)
        box = tk.Listbox(win, bg=C["bg"], fg=C["text"], width=30, height=10, exportselection=False)
        box.pack(padx=16)
        for item in settings["playlist"]:
            box.insert("end", item)
        row = self._frame(win)
        row.pack(padx=16, pady=10)

        def add():
            if self._active_id in list_melody_ids() and self._active_id not in box.get(0, "end"):
                box.insert("end", self._active_id)

        def move(delta):
            sel = box.curselection()
            if sel and 0 <= sel[0] + delta < box.size():
                i, text = sel[0], box.get(sel[0])
                box.delete(i)
                box.insert(i + delta, text)
                box.selection_set(i + delta)

        def save():
            try:
                save_rotation_settings(mode.get(), list(box.get(0, "end")))
                win.destroy()
                self.status_var.set("게임 재생 순서 저장됨")
            except (OSError, ValueError) as exc:
                messagebox.showerror("게임 순서", str(exc), parent=win)
        for label, cmd in (("현재 곡 추가", add), ("제외", lambda: box.delete(box.curselection()[0]) if box.curselection() else None),
                           ("↑", lambda: move(-1)), ("↓", lambda: move(1))):
            self._button(row, label, cmd).pack(side="left", padx=2)
        self._button(win, "순서 저장", save, True).pack(pady=(0, 12))

    def _preview(self):
        if self._data and self._commit_bpm():
            self._stop_preview()
            token = self._preview_token
            error = play_melody(deepcopy(self._data["steps"]), self._data["bpm"],
                                on_step=lambda step: self._preview_queue.put((token, step)),
                                on_error=lambda error: self._preview_queue.put((token, error)))
            self.status_var.set(error or "미리듣기 · 기준음 / velocity 반영 · 실제 멤버 음색은 Ableton에서 확인")

    def _stop_preview(self):
        stop_preview()
        self._preview_token += 1
        self._preview_step = -1
        self._redraw()

    def _poll_preview(self):
        try:
            while True:
                token, step = self._preview_queue.get_nowait()
                if token == self._preview_token:
                    if isinstance(step, str):
                        self.status_var.set("미리듣기 오류 · " + step)
                    else:
                        self._preview_step = step
                        self._redraw()
        except Empty:
            pass
        if not self._closing:
            self._poll_id = self.root.after(30, self._poll_preview)

    def _on_close(self):
        if self._discard_ok():
            self._closing = True
            self._stop_preview()
            self.root.after_cancel(self._poll_id)
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def run_gui(state: MelodyRegisterState):
    MelodyRegisterApp(state).run()
