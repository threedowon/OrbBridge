"""screen/melodies JSON을 편집하는 Melody Register 화면이다."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

from melody_preview import play_melody, stop_preview
from melody_register_core import (
    EDITOR_NOTE_CHOICES_UI,
    NOTE_CHOICES,
    NOTE_CHOICES_UI,
    NOTE_REST,
    MelodyRegisterState,
    delete_melody,
    list_melody_ids,
    load_melody,
    melody_duration_sec,
    melodies_dir,
    new_melody_template,
    save_melody,
    stage_data_path,
    validate_melody,
)

MONO_CANDIDATES = ("JetBrains Mono", "Consolas", "Cascadia Mono", "Courier New")
UI_CANDIDATES = ("Inter", "Segoe UI", "Helvetica Neue", "Manrope", "Arial")
DURATION_CHOICES = ("0.25", "0.5", "0.75", "1", "1.5", "2", "3", "4")

C = {
    "canvas": "#ffffff",
    "canvas_soft": "#e8ebe6",
    "primary": "#9fe870",
    "primary_active": "#cdffad",
    "primary_neutral": "#c5edab",
    "primary_pale": "#e2f6d5",
    "ink": "#0e0f0c",
    "body": "#454745",
    "mute": "#868685",
    "on_primary": "#0e0f0c",
    "positive": "#2ead4b",
    "warning_deep": "#b86700",
    "negative": "#d03238",
}


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


class MelodyRegisterApp:
    def __init__(self, state: MelodyRegisterState) -> None:
        self.state = state
        self._closing = False
        self._active_id = ""
        self._data: dict | None = None
        self._dirty = False

        self.root = tk.Tk()
        ui = _pick_font(self.root, UI_CANDIDATES, "TkDefaultFont")
        mono = _pick_font(self.root, MONO_CANDIDATES, "TkFixedFont")
        self.root.title("Melody Register")
        self.root.configure(bg=C["canvas_soft"])
        self.root.minsize(900, 680)
        self.root.geometry("980x760")

        self._fonts = {
            "title": tkfont.Font(family=ui, size=26, weight="bold"),
            "section": tkfont.Font(family=ui, size=15, weight="bold"),
            "body": tkfont.Font(family=ui, size=12),
            "label": tkfont.Font(family=ui, size=11, weight="bold"),
            "mono": tkfont.Font(family=mono, size=10),
            "btn": tkfont.Font(family=ui, size=11, weight="bold"),
            "btn_sm": tkfont.Font(family=ui, size=10, weight="bold"),
        }

        self.song_id_var = tk.StringVar(master=self.root)
        self.title_var = tk.StringVar(master=self.root)
        self.artist_var = tk.StringVar(master=self.root)
        self.bpm_var = tk.StringVar(master=self.root, value="120")
        self.range_low_var = tk.StringVar(master=self.root, value="C4")
        self.range_high_var = tk.StringVar(master=self.root, value="C5")
        self.sync_stage_var = tk.BooleanVar(master=self.root, value=True)
        self.summary_var = tk.StringVar(master=self.root, value="")
        self.step_note_var = tk.StringVar(master=self.root, value="C4")
        self.step_dur_var = tk.StringVar(master=self.root, value="1")
        self.step_lyric_var = tk.StringVar(master=self.root, value="")

        self._setup_styles()
        self._build()
        state.add_log_listener(self._on_log)
        self._refresh_list()
        self._load_logs()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background=C["canvas"],
            fieldbackground=C["canvas"],
            foreground=C["ink"],
            rowheight=30,
            font=self._fonts["body"],
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=self._fonts["label"],
            background=C["canvas_soft"],
            foreground=C["ink"],
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", C["primary_pale"])],
            foreground=[("selected", C["ink"])],
        )

    def _btn(self, parent, text, cmd, *, primary=False, small=False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self._fonts["btn_sm" if small else "btn"],
            bg=C["primary"] if primary else C["canvas"],
            activebackground=C["primary_active"],
            fg=C["on_primary"],
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
        )

    def _label(self, parent, text) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            font=self._fonts["label"],
            bg=parent["bg"] if "bg" in parent.keys() else C["canvas"],
            fg=C["body"],
        )

    def _entry(self, parent, textvariable, width=16) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=textvariable,
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["ink"],
            relief="solid",
            bd=1,
            width=width,
        )

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=C["canvas_soft"])
        outer.pack(fill="both", expand=True, padx=14, pady=14)

        header = tk.Frame(outer, bg=C["canvas_soft"])
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header,
            text="Melody Register",
            font=self._fonts["title"],
            bg=C["canvas_soft"],
            fg=C["ink"],
        ).pack(side="left")
        tk.Label(
            header,
            text=str(melodies_dir()),
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["mute"],
        ).pack(side="right")

        body = tk.Frame(outer, bg=C["canvas_soft"])
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=C["canvas"], width=220)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        tk.Label(
            left,
            text="멜로디",
            font=self._fonts["section"],
            bg=C["canvas"],
            fg=C["ink"],
        ).pack(anchor="w", padx=12, pady=(12, 6))

        list_frame = tk.Frame(left, bg=C["canvas"])
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.listbox = tk.Listbox(
            list_frame,
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["ink"],
            selectbackground=C["primary_pale"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["primary_neutral"],
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        left_btns = tk.Frame(left, bg=C["canvas"])
        left_btns.pack(fill="x", padx=8, pady=(0, 12))
        self._btn(left_btns, "새 멜로디", self._new_melody, small=True).pack(
            side="left", padx=(0, 6)
        )
        self._btn(left_btns, "삭제", self._delete_melody, small=True).pack(side="left")

        right = tk.Frame(body, bg=C["canvas"])
        right.pack(side="left", fill="both", expand=True)

        meta = tk.Frame(right, bg=C["canvas"])
        meta.pack(fill="x", padx=14, pady=(12, 8))

        row1 = tk.Frame(meta, bg=C["canvas"])
        row1.pack(fill="x", pady=4)
        self._label(row1, "songId").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._entry(row1, self.song_id_var, 14).grid(row=0, column=1, sticky="w", padx=(0, 16))
        self._label(row1, "title").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self._entry(row1, self.title_var, 18).grid(row=0, column=3, sticky="w", padx=(0, 16))
        self._label(row1, "artist").grid(row=0, column=4, sticky="w", padx=(0, 8))
        self._entry(row1, self.artist_var, 14).grid(row=0, column=5, sticky="w")

        row2 = tk.Frame(meta, bg=C["canvas"])
        row2.pack(fill="x", pady=4)
        self._label(row2, "BPM").grid(row=0, column=0, sticky="w", padx=(0, 8))
        bpm_spin = tk.Spinbox(
            row2,
            from_=40,
            to=240,
            increment=1,
            textvariable=self.bpm_var,
            width=6,
            font=self._fonts["body"],
            command=self._mark_dirty_summary,
        )
        bpm_spin.grid(row=0, column=1, sticky="w", padx=(0, 16))
        bpm_spin.bind("<KeyRelease>", lambda _e: self._mark_dirty_summary())

        self._label(row2, "range").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Combobox(
            row2,
            textvariable=self.range_low_var,
            values=NOTE_CHOICES_UI,
            width=5,
            state="readonly",
        ).grid(row=0, column=3, sticky="w")
        tk.Label(row2, text="–", bg=C["canvas"], fg=C["body"]).grid(row=0, column=4)
        ttk.Combobox(
            row2,
            textvariable=self.range_high_var,
            values=NOTE_CHOICES_UI,
            width=5,
            state="readonly",
        ).grid(row=0, column=5, sticky="w", padx=(0, 16))

        tk.Checkbutton(
            row2,
            text="저장 시 stage_data.json 동기화",
            variable=self.sync_stage_var,
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["body"],
            activebackground=C["canvas"],
        ).grid(row=0, column=6, sticky="w")

        actions = tk.Frame(meta, bg=C["canvas"])
        actions.pack(fill="x", pady=(10, 4))
        self._btn(actions, "미리듣기", self._preview).pack(side="left", padx=(0, 8))
        self._btn(actions, "정지", self._stop_preview, small=True).pack(side="left", padx=(0, 8))
        self._btn(actions, "저장", self._save_current, primary=True).pack(side="left", padx=(0, 8))
        tk.Label(
            actions,
            textvariable=self.summary_var,
            font=self._fonts["mono"],
            bg=C["canvas"],
            fg=C["mute"],
        ).pack(side="right")

        tk.Label(
            right,
            text="Steps — note · duration(박) · lyric",
            font=self._fonts["section"],
            bg=C["canvas"],
            fg=C["ink"],
        ).pack(anchor="w", padx=14, pady=(4, 4))

        tree_wrap = tk.Frame(right, bg=C["canvas"])
        tree_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        cols = ("step", "note", "duration", "lyric", "ms")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=12)
        for col, title, w in (
            ("step", "#", 40),
            ("note", "note", 70),
            ("duration", "duration", 80),
            ("lyric", "lyric", 120),
            ("ms", "ms", 70),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="center" if col != "lyric" else "w")
        tree_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_step_select)

        edit = tk.Frame(right, bg=C["canvas"])
        edit.pack(fill="x", padx=14, pady=(0, 8))
        self._label(edit, "선택 step").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            edit,
            textvariable=self.step_note_var,
            values=[*EDITOR_NOTE_CHOICES_UI],
            width=6,
            state="readonly",
        ).pack(side="left", padx=(0, 8))
        ttk.Combobox(
            edit,
            textvariable=self.step_dur_var,
            values=DURATION_CHOICES,
            width=6,
        ).pack(side="left", padx=(0, 8))
        self._entry(edit, self.step_lyric_var, 12).pack(side="left", padx=(0, 8))
        self._btn(edit, "적용", self._apply_step_edit, small=True).pack(side="left", padx=(0, 12))
        self._btn(edit, "+ step", self._add_step, small=True).pack(side="left", padx=(0, 4))
        self._btn(edit, "− step", self._remove_step, small=True).pack(side="left", padx=(0, 4))
        self._btn(edit, "▲", self._move_step_up, small=True).pack(side="left", padx=(0, 4))
        self._btn(edit, "▼", self._move_step_down, small=True).pack(side="left")

        log_frame = tk.Frame(outer, bg=C["canvas"])
        log_frame.pack(fill="x", pady=(10, 0))
        tk.Label(
            log_frame,
            text="Log",
            font=self._fonts["label"],
            bg=C["canvas"],
            fg=C["ink"],
        ).pack(anchor="w", padx=10, pady=(8, 4))
        self.log_text = tk.Text(
            log_frame,
            height=5,
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["body"],
            relief="flat",
            state="disabled",
        )
        self.log_text.pack(fill="x", padx=10, pady=(0, 10))

        hint = tk.Label(
            outer,
            text=f"stage_data: {stage_data_path()}",
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["mute"],
        )
        hint.pack(anchor="w")

        for var in (
            self.song_id_var,
            self.title_var,
            self.artist_var,
            self.bpm_var,
            self.range_low_var,
            self.range_high_var,
        ):
            var.trace_add("write", lambda *_: self._mark_dirty_summary())

    def _collect_form(self) -> dict:
        if not self._data:
            self._data = new_melody_template("custom")
        data = dict(self._data)
        data["songId"] = self.song_id_var.get().strip()
        data["title"] = self.title_var.get().strip()
        data["artist"] = self.artist_var.get().strip()
        try:
            data["bpm"] = int(self.bpm_var.get().strip())
        except ValueError:
            data["bpm"] = 120
        data["range"] = [self.range_low_var.get(), self.range_high_var.get()]
        data["_file_id"] = self._active_id or data.get("_file_id", data["songId"])
        return data

    def _refresh_summary(self, data: dict | None = None) -> None:
        data = data or self._collect_form()
        steps = data.get("steps") or []
        beats = sum(float(s.get("duration") or 1) for s in steps)
        sec = melody_duration_sec(data)
        bpm = data.get("bpm", 120)
        beat_ms = 60_000 / max(1, int(bpm))
        self.summary_var.set(
            f"{len(steps)} steps · {beats:g}박 · {sec:.2f}s @ {bpm} BPM (1박={beat_ms:.0f}ms)"
        )

    def _mark_dirty_summary(self, *_args) -> None:
        self._dirty = True
        self._refresh_summary()

    def _refresh_list(self, select: str | None = None) -> None:
        ids = list_melody_ids()
        self.listbox.delete(0, "end")
        for file_id in ids:
            self.listbox.insert("end", file_id)
        target = select or self._active_id
        if target and target in ids:
            idx = ids.index(target)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def _load_melody(self, file_id: str) -> None:
        stop_preview()
        data = load_melody(file_id)
        self._active_id = file_id
        self._data = data
        self._dirty = False

        self.song_id_var.set(data.get("songId", file_id))
        self.title_var.set(data.get("title", ""))
        self.artist_var.set(data.get("artist", ""))
        self.bpm_var.set(str(data.get("bpm", 120)))
        rng = data.get("range") or ["C4", "C5"]
        self.range_low_var.set(rng[0])
        self.range_high_var.set(rng[1])

        self._rebuild_tree()
        self._refresh_summary(data)
        self.state.push_log("info", f"열기 · {file_id}.json")

    def _rebuild_tree(self, select_index: int = 0) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self._data:
            return
        bpm = max(1, int(self._data.get("bpm") or 120))
        beat_ms = 60_000 / bpm
        t0 = 0.0
        for i, step in enumerate(self._data.get("steps") or []):
            dur = float(step.get("duration") or 1)
            ms = int(t0)
            t0 += beat_ms * dur
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    i + 1,
                    step.get("note", NOTE_REST),
                    dur,
                    step.get("lyric", ""),
                    ms,
                ),
            )
        children = self.tree.get_children()
        if children:
            idx = max(0, min(select_index, len(children) - 1))
            self.tree.selection_set(children[idx])
            self.tree.focus(children[idx])
            self._on_step_select()

    def _on_list_select(self, _event=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        file_id = self.listbox.get(sel[0])
        if file_id == self._active_id and self._data:
            return
        if self._dirty and not messagebox.askyesno(
            "Melody Register", "저장하지 않은 변경이 있습니다. 계속할까요?"
        ):
            self._refresh_list(select=self._active_id)
            return
        self._load_melody(file_id)

    def _selected_step_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _on_step_select(self, _event=None) -> None:
        idx = self._selected_step_index()
        if idx is None or not self._data:
            return
        steps = self._data.get("steps") or []
        if idx >= len(steps):
            return
        step = steps[idx]
        self.step_note_var.set(step.get("note", "C4"))
        self.step_dur_var.set(str(step.get("duration", 1)))
        self.step_lyric_var.set(step.get("lyric", ""))

    def _apply_step_edit(self) -> None:
        idx = self._selected_step_index()
        if idx is None or not self._data:
            return
        try:
            dur = float(self.step_dur_var.get().strip())
        except ValueError:
            messagebox.showerror("Melody Register", "duration은 숫자여야 합니다.")
            return
        if dur <= 0:
            messagebox.showerror("Melody Register", "duration은 0보다 커야 합니다.")
            return
        steps = self._data.setdefault("steps", [])
        steps[idx]["note"] = self.step_note_var.get().strip() or NOTE_REST
        steps[idx]["duration"] = dur
        lyric = self.step_lyric_var.get().strip()
        if lyric:
            steps[idx]["lyric"] = lyric
        elif "lyric" in steps[idx]:
            del steps[idx]["lyric"]
        self._dirty = True
        self._rebuild_tree(select_index=idx)
        self._refresh_summary()

    def _add_step(self) -> None:
        if not self._data:
            return
        steps = self._data.setdefault("steps", [])
        last_note = steps[-1].get("note", "C4") if steps else "C4"
        steps.append({"step": len(steps) + 1, "note": last_note, "duration": 1})
        self._dirty = True
        self._rebuild_tree(select_index=len(steps) - 1)
        self._refresh_summary()

    def _remove_step(self) -> None:
        idx = self._selected_step_index()
        if idx is None or not self._data:
            return
        steps = self._data.get("steps") or []
        if len(steps) <= 1:
            messagebox.showinfo("Melody Register", "최소 1개 step이 필요합니다.")
            return
        if not messagebox.askyesno("Melody Register", f"step {idx + 1}을 삭제할까요?"):
            return
        steps.pop(idx)
        self._dirty = True
        self._rebuild_tree(select_index=max(0, idx - 1))
        self._refresh_summary()

    def _swap_steps(self, a: int, b: int) -> None:
        if not self._data:
            return
        steps = self._data.get("steps") or []
        if a < 0 or b < 0 or a >= len(steps) or b >= len(steps):
            return
        steps[a], steps[b] = steps[b], steps[a]
        self._dirty = True
        self._rebuild_tree(select_index=b)

    def _move_step_up(self) -> None:
        idx = self._selected_step_index()
        if idx is not None and idx > 0:
            self._swap_steps(idx, idx - 1)

    def _move_step_down(self) -> None:
        idx = self._selected_step_index()
        if idx is not None and self._data:
            steps = self._data.get("steps") or []
            if idx < len(steps) - 1:
                self._swap_steps(idx, idx + 1)

    def _preview(self) -> None:
        if not self._data:
            return
        data = self._collect_form()
        data["steps"] = list(self._data.get("steps") or [])
        err = play_melody(
            data["steps"],
            int(data.get("bpm") or 120),
            on_step=self._on_preview_beat,
        )
        if err:
            messagebox.showerror("Melody Register", err)

    def _on_preview_beat(self, index: int) -> None:
        if index < 0:
            self.root.after(0, lambda: self.tree.selection_remove(self.tree.selection()))
            return
        self.root.after(0, lambda i=index: self._highlight_step(i))

    def _highlight_step(self, index: int) -> None:
        children = self.tree.get_children()
        if 0 <= index < len(children):
            self.tree.selection_set(children[index])
            self.tree.see(children[index])

    def _stop_preview(self) -> None:
        stop_preview()

    def _save_current(self) -> None:
        if not self._data or not self._active_id:
            messagebox.showinfo("Melody Register", "멜로디를 선택하세요.")
            return
        data = self._collect_form()
        data["steps"] = list(self._data.get("steps") or [])
        errors = validate_melody(data)
        if errors:
            messagebox.showerror("Melody Register", "\n".join(errors))
            return
        try:
            old_id = self._active_id
            new_id = data["songId"].strip() or old_id
            save_melody(
                data,
                file_id=new_id,
                sync_stage=self.sync_stage_var.get(),
                state=self.state,
            )
            if new_id != old_id:
                from melody_register_core import melody_path

                old_path = melody_path(old_id)
                new_path = melody_path(new_id)
                if old_path != new_path and old_path.is_file():
                    old_path.unlink()
            self._active_id = new_id
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Register", str(exc))
            self.state.push_log("error", str(exc))
            return
        self._dirty = False
        self._data = load_melody(self._active_id)
        self._refresh_list(select=self._active_id)
        messagebox.showinfo("Melody Register", f"{self._active_id}.json 저장됨")

    def _new_melody(self) -> None:
        name = simpledialog.askstring(
            "Melody Register",
            "새 songId (파일명, 예: my_song):",
            parent=self.root,
        )
        if not name:
            return
        name = name.strip().replace(" ", "_")
        try:
            from melody_register_core import melody_path

            if melody_path(name).exists():
                messagebox.showerror("Melody Register", f"{name}.json 이 이미 있습니다.")
                return
            data = new_melody_template(name)
            save_melody(data, file_id=name, sync_stage=False, state=self.state)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Register", str(exc))
            return
        self._refresh_list(select=name)
        self._load_melody(name)

    def _delete_melody(self) -> None:
        if not self._active_id:
            return
        if not messagebox.askyesno(
            "Melody Register", f"{self._active_id}.json 을 삭제할까요?"
        ):
            return
        try:
            delete_melody(self._active_id, self.state)
        except OSError as exc:
            messagebox.showerror("Melody Register", str(exc))
            return
        self._active_id = ""
        self._data = None
        self._refresh_list()
        ids = list_melody_ids()
        if ids:
            self._load_melody(ids[0])

    def _on_log(self, entry: dict) -> None:
        if self._closing:
            return
        ts = datetime.fromtimestamp(entry["at"]).strftime("%H:%M:%S")
        line = f"[{ts}] {entry['message']}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("1.0", line)
        self.log_text.configure(state="disabled")

    def _load_logs(self) -> None:
        for entry in reversed(self.state.logs):
            self._on_log(entry)

    def _on_close(self) -> None:
        self._closing = True
        stop_preview()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        ids = list_melody_ids()
        if ids:
            self._load_melody(ids[0])
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_close()


def run_gui(state: MelodyRegisterState) -> None:
    MelodyRegisterApp(state).run()
