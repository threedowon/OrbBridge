"""Melody Editor — compact UI for BPM · note · duration on the active melody."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from melody_editor_core import (
    active_melody_id,
    load_active_melody,
    save_active_melody,
    timing_summary,
)
from melody_preview import play_melody, stop_preview
from melody_register_core import NOTE_CHOICES, melodies_dir

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
}


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


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
        self.root.minsize(420, 480)
        self.root.geometry("460x560")

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
        self.subtitle_var = tk.StringVar(master=self.root)
        self.summary_var = tk.StringVar(master=self.root)

        self._build()
        self._reload()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _btn(self, parent, text, cmd, *, primary=False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self._fonts["btn_sm" if not primary else "btn"],
            bg=C["primary"] if primary else C["canvas"],
            activebackground=C["primary_active"],
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
        ).pack(anchor="w")

        tk.Label(
            outer,
            textvariable=self.subtitle_var,
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["mute"],
        ).pack(anchor="w", pady=(2, 10))

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
            command=self._update_summary,
        )
        spin.pack(side="left")
        spin.bind("<KeyRelease>", lambda _e: self._update_summary())

        tk.Label(
            top,
            textvariable=self.summary_var,
            font=self._fonts["mono"],
            bg=C["canvas"],
            fg=C["mute"],
        ).pack(side="right")

        hdr = tk.Frame(card, bg=C["canvas_soft"])
        hdr.pack(fill="x", padx=14, pady=(6, 4))
        for text, w in (("step", 5), ("note", 8), ("duration", 10), ("ms", 7)):
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
        self._btn(actions, "미리듣기", self._preview).pack(side="left", padx=(0, 6))
        self._btn(actions, "정지", self._stop).pack(side="left", padx=(0, 6))
        self._btn(actions, "다시 불러오기", self._reload).pack(side="left", padx=(0, 6))
        self._btn(actions, "저장", self._save, primary=True).pack(side="right")

    def _clear_rows(self) -> None:
        for row in self._rows:
            row["frame"].destroy()
        self._rows.clear()

    def _build_rows(self) -> None:
        self._clear_rows()
        bpm = max(1, int(self._data.get("bpm") or 120))
        beat_ms = 60_000 / bpm
        t0 = 0.0

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

            note_var = tk.StringVar(value=step.get("note", "C4"))
            ttk.Combobox(
                frame,
                textvariable=note_var,
                values=[*NOTE_CHOICES, "?"],
                width=6,
                state="readonly",
            ).pack(side="left", padx=(0, 8))

            dur_var = tk.StringVar(value=str(step.get("duration", 1)))
            ttk.Combobox(frame, textvariable=dur_var, values=DURATION_CHOICES, width=6).pack(
                side="left", padx=(0, 8)
            )

            ms = int(t0)
            ms_label = tk.Label(
                frame,
                text=f"{ms}",
                font=self._fonts["mono"],
                bg=C["canvas"],
                fg=C["mute"],
                width=6,
                anchor="w",
            )
            ms_label.pack(side="left")

            lyric = str(step.get("lyric", "")).strip()
            if lyric:
                tk.Label(
                    frame,
                    text=lyric,
                    font=self._fonts["body"],
                    bg=C["canvas"],
                    fg=C["mute"],
                ).pack(side="left", padx=(8, 0))

            note_var.trace_add("write", lambda *_: self._update_summary())
            dur_var.trace_add("write", lambda *_: self._update_summary())

            self._rows.append(
                {
                    "frame": frame,
                    "note_var": note_var,
                    "dur_var": dur_var,
                    "ms_label": ms_label,
                }
            )
            t0 += beat_ms * float(step.get("duration") or 1)

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
            base["note"] = row["note_var"].get().strip() or "?"
            try:
                base["duration"] = float(row["dur_var"].get().strip())
            except ValueError:
                base["duration"] = 1.0
            steps.append(base)
        data["steps"] = steps
        data["_file_id"] = self._file_id
        return data

    def _update_ms_labels(self) -> None:
        bpm = max(1, int(self.bpm_var.get().strip() or "120"))
        beat_ms = 60_000 / bpm
        t0 = 0.0
        for row in self._rows:
            try:
                dur = float(row["dur_var"].get().strip())
            except ValueError:
                dur = 1.0
            ms = int(t0)
            t0 += beat_ms * dur
            for child in row["frame"].winfo_children():
                if isinstance(child, tk.Label) and child.cget("font") == str(self._fonts["mono"]):
                    child.configure(text=str(ms))
                    break

    def _update_summary(self) -> None:
        try:
            data = self._collect()
            self.summary_var.set(timing_summary(data))
            self._update_ms_labels()
        except Exception:
            pass

    def _reload(self) -> None:
        stop_preview()
        self._file_id, self._data = load_active_melody()
        title = self._data.get("title") or self._file_id
        self.subtitle_var.set(f"{title} · {self._file_id}.json · {melodies_dir()}")
        self.bpm_var.set(str(self._data.get("bpm", 120)))
        self._build_rows()
        self._update_summary()

    def _preview(self) -> None:
        data = self._collect()
        err = play_melody(data["steps"], int(data.get("bpm") or 120))
        if err:
            messagebox.showerror("Melody Editor", err)

    def _stop(self) -> None:
        stop_preview()

    def _save(self) -> None:
        data = self._collect()
        try:
            save_active_melody(data, file_id=self._file_id, sync_stage=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Melody Editor", str(exc))
            return
        self._data = data
        messagebox.showinfo("Melody Editor", f"{self._file_id}.json 저장됨\n(stage_data 동기화됨)")

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
