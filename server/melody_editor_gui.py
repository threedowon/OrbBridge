"""Melody Editor — compact UI for BPM · note · duration on the active melody."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk

from melody_editor_core import (
    create_melody,
    list_melody_ids,
    load_active_melody,
    load_melody_for_editor,
    normalize_beat_steps,
    save_melody_editor,
)
from melody_preview import play_melody, stop_preview
from melody_register_core import NOTE_CHOICES, NOTE_CHOICES_UI, NOTE_REST

NOTE_REST_DISPLAY = "-"
EDITOR_NOTE_CHOICES_DISPLAY = (*NOTE_CHOICES_UI, NOTE_REST_DISPLAY)

UI_CANDIDATES = ("Inter", "Segoe UI", "Helvetica Neue", "Manrope", "Arial")
DURATION_CHOICES = ("0.25", "0.5", "0.75", "1", "1.5", "2", "3", "4")

C = {
    "canvas": "#ffffff",
    "canvas_soft": "#f2f2f2",
    "canvas_muted": "#e6e6e6",
    "ink": "#111111",
    "body": "#444444",
    "mute": "#888888",
    "border": "#cccccc",
    "primary": "#1a1a1a",
    "primary_active": "#333333",
    "on_primary": "#ffffff",
    "btn_muted": "#e0e0e0",
    "btn_muted_active": "#cacaca",
    "on_muted": "#222222",
}


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


def _display_note(note: str) -> str:
    note = str(note or "").strip()
    upper = note.upper()
    if upper == NOTE_REST or upper in ("R", "X", "?") or note == NOTE_REST_DISPLAY:
        return NOTE_REST_DISPLAY
    return note if note in NOTE_CHOICES else NOTE_CHOICES[0]


def _note_from_display(display: str) -> str:
    note = str(display or "").strip()
    upper = note.upper()
    if note == NOTE_REST_DISPLAY or upper == NOTE_REST or upper in ("R", "X", "?"):
        return NOTE_REST
    if note in NOTE_CHOICES:
        return note
    return NOTE_CHOICES[0]


class MelodyEditorApp:
    def __init__(self) -> None:
        self._closing = False
        self._file_id = ""
        self._data: dict = {}
        self._rows: list[dict] = []
        self._dirty = False

        self.root = tk.Tk()
        ui = _pick_font(self.root, UI_CANDIDATES, "TkDefaultFont")
        self.root.title("Melody Editor")
        self.root.configure(bg=C["canvas_soft"])
        self.root.minsize(380, 520)
        self.root.geometry("400x600")

        self._fonts = {
            "title": tkfont.Font(family=ui, size=22, weight="bold"),
            "section": tkfont.Font(family=ui, size=12, weight="bold"),
            "body": tkfont.Font(family=ui, size=11),
            "label": tkfont.Font(family=ui, size=10, weight="bold"),
            "mono": tkfont.Font(family=ui, size=10),
            "btn": tkfont.Font(family=ui, size=11, weight="bold"),
            "btn_sm": tkfont.Font(family=ui, size=10, weight="bold"),
        }

        self.bpm_var = tk.StringVar(master=self.root, value="120")
        self.song_var = tk.StringVar(master=self.root, value="")
        self.bpm_var.trace_add("write", self._mark_dirty)

        self._setup_styles()
        self._build()
        self._load()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Mono.TCombobox",
            fieldbackground=C["canvas"],
            background=C["canvas_muted"],
            foreground=C["ink"],
            arrowcolor=C["body"],
            bordercolor=C["border"],
            lightcolor=C["border"],
            darkcolor=C["border"],
        )
        style.map(
            "Mono.TCombobox",
            fieldbackground=[("readonly", C["canvas"])],
            selectbackground=[("readonly", C["canvas_muted"])],
            selectforeground=[("readonly", C["ink"])],
        )

    def _btn(self, parent, text, cmd, *, primary=False, muted=False) -> tk.Button:
        if primary:
            bg, active, fg = C["primary"], C["primary_active"], C["on_primary"]
        elif muted:
            bg, active, fg = C["btn_muted"], C["btn_muted_active"], C["on_muted"]
        else:
            bg, active, fg = C["canvas"], C["canvas_muted"], C["ink"]
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self._fonts["btn_sm" if not primary else "btn"],
            bg=bg,
            activebackground=active,
            fg=fg,
            activeforeground=fg,
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["border"],
        )

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=C["canvas_soft"])
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(
            outer,
            text="Melody Editor",
            font=self._fonts["title"],
            bg=C["canvas_soft"],
            fg=C["ink"],
        ).pack(anchor="w", pady=(0, 10))

        card = tk.Frame(outer, bg=C["canvas"])
        card.pack(fill="both", expand=True)

        song_bar = tk.Frame(card, bg=C["canvas"])
        song_bar.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(song_bar, text="노래", font=self._fonts["label"], bg=C["canvas"], fg=C["body"]).pack(
            side="left", padx=(0, 8)
        )
        self.song_combo = ttk.Combobox(
            song_bar,
            textvariable=self.song_var,
            state="readonly",
            width=20,
            style="Mono.TCombobox",
        )
        self.song_combo.pack(side="left")
        self.song_combo.bind("<<ComboboxSelected>>", self._on_song_select)

        top = tk.Frame(card, bg=C["canvas"])
        top.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(top, text="BPM", font=self._fonts["label"], bg=C["canvas"], fg=C["body"]).pack(
            side="left", padx=(0, 8)
        )
        spin = tk.Spinbox(
            top,
            from_=40,
            to=240,
            width=5,
            textvariable=self.bpm_var,
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["ink"],
            buttonbackground=C["canvas_muted"],
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["border"],
            relief="flat",
        )
        spin.pack(side="left")

        hdr = tk.Frame(card, bg=C["canvas_muted"])
        hdr.pack(fill="x", padx=14, pady=(6, 4))
        for text, w in (("step", 5), ("note", 8), ("duration", 10)):
            tk.Label(
                hdr,
                text=text,
                font=self._fonts["label"],
                bg=C["canvas_muted"],
                fg=C["body"],
                width=w,
                anchor="w",
            ).pack(side="left", padx=(0, 4))

        steps_wrap = tk.Frame(card, bg=C["canvas"])
        steps_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self._steps_canvas = tk.Canvas(
            steps_wrap,
            bg=C["canvas"],
            highlightthickness=0,
            borderwidth=0,
        )
        steps_scroll = ttk.Scrollbar(steps_wrap, orient="vertical", command=self._steps_canvas.yview)
        self._steps_canvas.configure(yscrollcommand=steps_scroll.set)
        self._steps_canvas.pack(side="left", fill="both", expand=True)
        steps_scroll.pack(side="right", fill="y")

        self.steps_frame = tk.Frame(self._steps_canvas, bg=C["canvas"])
        self._steps_window = self._steps_canvas.create_window((0, 0), window=self.steps_frame, anchor="nw")
        self.steps_frame.bind("<Configure>", self._on_steps_frame_configure)
        self._steps_canvas.bind("<Configure>", self._on_steps_canvas_configure)
        self._steps_canvas.bind_all("<MouseWheel>", self._on_steps_mousewheel, add="+")

        actions = tk.Frame(card, bg=C["canvas"])
        actions.pack(fill="x", padx=14, pady=(0, 14))
        self._btn(actions, "새 노래", self._new_song, muted=True).pack(side="left")
        right = tk.Frame(actions, bg=C["canvas"])
        right.pack(side="right")
        self._btn(right, "미리듣기", self._preview, muted=True).pack(side="left", padx=(0, 8))
        self._btn(right, "저장", self._save, primary=True).pack(side="left")

    def _on_steps_frame_configure(self, _event=None) -> None:
        self._steps_canvas.configure(scrollregion=self._steps_canvas.bbox("all"))

    def _on_steps_canvas_configure(self, event) -> None:
        self._steps_canvas.itemconfigure(self._steps_window, width=event.width)

    def _on_steps_mousewheel(self, event) -> None:
        if not self._steps_canvas.winfo_exists():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        cur = widget
        while cur is not None:
            if cur is self._steps_canvas or cur is self.steps_frame:
                self._steps_canvas.yview_scroll(int(-event.delta / 120), "units")
                break
            cur = cur.master

    def _mark_dirty(self, *_args) -> None:
        self._dirty = True

    def _refresh_song_list(self, select: str | None = None) -> None:
        ids = list_melody_ids()
        self.song_combo["values"] = ids
        target = select or self._file_id
        if target in ids:
            self.song_var.set(target)
        elif ids:
            self.song_var.set(ids[0])
        else:
            self.song_var.set("")

    def _load_file(self, file_id: str) -> None:
        stop_preview()
        self._file_id, self._data = load_melody_for_editor(file_id)
        self.bpm_var.set(str(self._data.get("bpm", 120)))
        self._refresh_song_list(select=file_id)
        self._build_rows()
        self._dirty = False

    def _on_song_select(self, _event=None) -> None:
        file_id = self.song_var.get().strip()
        if not file_id or file_id == self._file_id:
            return
        if self._dirty and not messagebox.askyesno(
            "Melody Editor", "저장하지 않은 변경이 있습니다. 계속할까요?"
        ):
            self._refresh_song_list(select=self._file_id)
            return
        try:
            self._load_file(file_id)
        except FileNotFoundError as exc:
            messagebox.showwarning("Melody Editor", str(exc))
            try:
                self._load()
            except FileNotFoundError as exc2:
                messagebox.showerror("Melody Editor", str(exc2))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Editor", str(exc))
            self._refresh_song_list(select=self._file_id)

    def _new_song(self) -> None:
        if self._dirty and not messagebox.askyesno(
            "Melody Editor", "저장하지 않은 변경이 있습니다. 새 노래를 만드시겠습니까?"
        ):
            return
        name = simpledialog.askstring(
            "Melody Editor",
            "새 songId (파일명, 예: my_song):",
            parent=self.root,
        )
        if not name:
            return
        try:
            file_id, data = create_melody(name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Editor", str(exc))
            return
        self._file_id = file_id
        self._data = data
        self.bpm_var.set(str(data.get("bpm", 120)))
        self._refresh_song_list(select=file_id)
        self._build_rows()
        self._dirty = False

    def _clear_rows(self) -> None:
        for row in self._rows:
            row["frame"].destroy()
        self._rows.clear()

    def _build_rows(self) -> None:
        self._clear_rows()
        steps = normalize_beat_steps(self._data.get("steps") or [], file_id=self._file_id)
        self._data["steps"] = steps

        for i, step in enumerate(steps):
            frame = tk.Frame(self.steps_frame, bg=C["canvas"])
            frame.pack(fill="x", pady=2)

            tk.Label(
                frame,
                text=str(i + 1),
                font=self._fonts["body"],
                bg=C["canvas"],
                fg=C["ink"],
                width=4,
                anchor="w",
            ).pack(side="left", padx=(0, 4))

            note_var = tk.StringVar(value=_display_note(step.get("note", "C4")))
            note_var.trace_add("write", self._mark_dirty)
            ttk.Combobox(
                frame,
                textvariable=note_var,
                values=list(EDITOR_NOTE_CHOICES_DISPLAY),
                width=6,
                state="readonly",
                style="Mono.TCombobox",
            ).pack(side="left", padx=(0, 8))

            dur_var = tk.StringVar(value=str(step.get("duration", 1)))
            dur_var.trace_add("write", self._mark_dirty)
            dur_entry = tk.Entry(
                frame,
                textvariable=dur_var,
                width=6,
                font=self._fonts["mono"],
                bg=C["canvas"],
                fg=C["ink"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=C["border"],
                highlightcolor=C["border"],
            )
            dur_entry.pack(side="left", padx=(0, 8))

            self._rows.append(
                {
                    "frame": frame,
                    "note_var": note_var,
                    "dur_var": dur_var,
                    "dur_entry": dur_entry,
                }
            )

        self._bind_duration_tabs()
        self.root.after_idle(self._on_steps_frame_configure)
        self._dirty = False

    def _bind_duration_tabs(self) -> None:
        entries = [row["dur_entry"] for row in self._rows]
        for i, entry in enumerate(entries):
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            prv = entries[i - 1] if i > 0 else None

            def focus_entry(widget: tk.Entry | None, _event: tk.Event | None = None) -> str:
                if widget is not None:
                    widget.focus_set()
                    widget.select_range(0, tk.END)
                    widget.icursor(tk.END)
                return "break"

            entry.bind(
                "<Tab>",
                lambda e, n=nxt: focus_entry(n, e),
            )
            entry.bind(
                "<Shift-Tab>",
                lambda e, p=prv: focus_entry(p, e),
            )

    def _collect(self) -> dict:
        song_id = self.song_var.get().strip() or self._file_id
        data = {
            "songId": str(self._data.get("songId") or song_id),
            "title": str(self._data.get("title") or song_id),
            "artist": str(self._data.get("artist") or ""),
            "range": list(self._data.get("range") or ["C4", "C5"]),
            "_file_id": self._file_id,
        }
        try:
            data["bpm"] = int(self.bpm_var.get().strip())
        except ValueError:
            data["bpm"] = 120

        steps = []
        base_steps = normalize_beat_steps(self._data.get("steps") or [], file_id=self._file_id)
        for i, row in enumerate(self._rows):
            base = dict(base_steps[i])
            base["step"] = i + 1
            note = row["note_var"].get().strip()
            base["note"] = _note_from_display(note)
            try:
                base["duration"] = float(row["dur_var"].get().strip())
            except ValueError:
                base["duration"] = 1.0
            base.pop("lyric", None)
            steps.append(base)
        data["steps"] = steps
        return data

    def _load(self) -> None:
        stop_preview()
        try:
            self._file_id, self._data = load_active_melody()
        except FileNotFoundError as exc:
            messagebox.showerror("Melody Editor", str(exc))
            self._file_id = ""
            self._data = {}
            self._refresh_song_list()
            self._rows = []
            self._dirty = False
            return
        self.bpm_var.set(str(self._data.get("bpm", 120)))
        self._refresh_song_list(select=self._file_id)
        self._build_rows()
        self._dirty = False

    def _preview(self) -> None:
        data = self._collect()
        err = play_melody(data["steps"], int(data.get("bpm") or 120))
        if err:
            messagebox.showerror("Melody Editor", err)

    def _save(self) -> None:
        data = self._collect()
        old_id = self._file_id
        new_id = self.song_var.get().strip() or data["songId"].strip() or old_id
        data["songId"] = new_id
        try:
            save_melody_editor(
                data,
                file_id=new_id,
                old_file_id=old_id,
                sync_stage=True,
                set_active=True,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Editor", str(exc))
            return
        self._file_id = new_id
        self._data = data
        self._refresh_song_list(select=new_id)
        self._dirty = False
        messagebox.showinfo("Melody Editor", f"{new_id}.json 저장됨 · active 설정됨")

    def _on_close(self) -> None:
        self._closing = True
        self._steps_canvas.unbind_all("<MouseWheel>")
        stop_preview()
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_close()


def run_gui() -> None:
    MelodyEditorApp().run()
