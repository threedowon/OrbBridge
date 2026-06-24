"""Melody Editor — compact UI for BPM · note · duration on the active melody."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from melody_editor_core import load_active_melody, save_active_melody
from melody_preview import play_melody, stop_preview
from melody_register_core import NOTE_CHOICES

UI_CANDIDATES = ("Inter", "Segoe UI", "Helvetica Neue", "Manrope", "Arial")
DURATION_CHOICES = ("0.25", "0.5", "0.75", "1", "1.5", "2", "3", "4")

C = {
    "canvas": "#ffffff",
    "canvas_soft": "#e8ebe6",
    "primary": "#9fe870",
    "primary_active": "#cdffad",
    "primary_pale": "#e2f6d5",
    "ink": "#0e0f0c",
    "body": "#454745",
    "mute": "#868685",
    "on_primary": "#0e0f0c",
    "btn_muted": "#d8dbd6",
    "btn_muted_active": "#c5c9c4",
}


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


def _display_note(note: str) -> str:
    note = str(note or "").strip()
    return note if note in NOTE_CHOICES else NOTE_CHOICES[0]


class MelodyEditorApp:
    def __init__(self) -> None:
        self._closing = False
        self._file_id = ""
        self._data: dict = {}
        self._rows: list[dict] = []

        self.root = tk.Tk()
        ui = _pick_font(self.root, UI_CANDIDATES, "TkDefaultFont")
        self.root.title("Melody Editor")
        self.root.configure(bg=C["canvas_soft"])
        self.root.minsize(360, 420)
        self.root.geometry("400x480")

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

        self._build()
        self._load()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _btn(self, parent, text, cmd, *, primary=False, muted=False) -> tk.Button:
        if primary:
            bg, active = C["primary"], C["primary_active"]
        elif muted:
            bg, active = C["btn_muted"], C["btn_muted_active"]
        else:
            bg, active = C["canvas"], C["primary_active"]
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self._fonts["btn_sm" if not primary else "btn"],
            bg=bg,
            activebackground=active,
            fg=C["on_primary"],
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
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

        top = tk.Frame(card, bg=C["canvas"])
        top.pack(fill="x", padx=14, pady=(14, 8))
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
        )
        spin.pack(side="left")

        hdr = tk.Frame(card, bg=C["canvas_soft"])
        hdr.pack(fill="x", padx=14, pady=(6, 4))
        for text, w in (("step", 5), ("note", 8), ("duration", 10)):
            tk.Label(
                hdr,
                text=text,
                font=self._fonts["label"],
                bg=C["canvas_soft"],
                fg=C["body"],
                width=w,
                anchor="w",
            ).pack(side="left", padx=(0, 4))

        self.steps_frame = tk.Frame(card, bg=C["canvas"])
        self.steps_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        actions = tk.Frame(card, bg=C["canvas"])
        actions.pack(fill="x", padx=14, pady=(0, 14))
        right = tk.Frame(actions, bg=C["canvas"])
        right.pack(side="right")
        self._btn(right, "미리듣기", self._preview, muted=True).pack(side="left", padx=(0, 8))
        self._btn(right, "저장", self._save, primary=True).pack(side="left")

    def _clear_rows(self) -> None:
        for row in self._rows:
            row["frame"].destroy()
        self._rows.clear()

    def _build_rows(self) -> None:
        self._clear_rows()

        for i, step in enumerate(self._data.get("steps") or []):
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
            ttk.Combobox(
                frame,
                textvariable=note_var,
                values=list(NOTE_CHOICES),
                width=6,
                state="readonly",
            ).pack(side="left", padx=(0, 8))

            dur_var = tk.StringVar(value=str(step.get("duration", 1)))
            ttk.Combobox(frame, textvariable=dur_var, values=DURATION_CHOICES, width=6).pack(
                side="left", padx=(0, 8)
            )

            self._rows.append(
                {
                    "frame": frame,
                    "note_var": note_var,
                    "dur_var": dur_var,
                }
            )

    def _collect(self) -> dict:
        data = dict(self._data)
        try:
            data["bpm"] = int(self.bpm_var.get().strip())
        except ValueError:
            data["bpm"] = 120

        steps = []
        for i, row in enumerate(self._rows):
            base = dict((self._data.get("steps") or [])[i])
            base["step"] = i + 1
            note = row["note_var"].get().strip()
            base["note"] = note if note in NOTE_CHOICES else NOTE_CHOICES[0]
            try:
                base["duration"] = float(row["dur_var"].get().strip())
            except ValueError:
                base["duration"] = 1.0
            steps.append(base)
        data["steps"] = steps
        data["_file_id"] = self._file_id
        return data

    def _load(self) -> None:
        stop_preview()
        self._file_id, self._data = load_active_melody()
        self.bpm_var.set(str(self._data.get("bpm", 120)))
        self._build_rows()

    def _preview(self) -> None:
        data = self._collect()
        err = play_melody(data["steps"], int(data.get("bpm") or 120))
        if err:
            messagebox.showerror("Melody Editor", err)

    def _save(self) -> None:
        data = self._collect()
        try:
            save_active_melody(data, file_id=self._file_id, sync_stage=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Editor", str(exc))
            return
        self._data = data
        messagebox.showinfo("Melody Editor", "저장됨")

    def _on_close(self) -> None:
        self._closing = True
        stop_preview()
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_close()


def run_gui() -> None:
    MelodyEditorApp().run()
