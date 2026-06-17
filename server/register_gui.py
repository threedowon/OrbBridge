"""Tkinter desktop GUI for DS2431 orb registration."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from datetime import datetime

from mapping_store import load_mapping, suggest_voice
from register_core import (
    RegisterState,
    SerialManager,
    add_orb,
    friendly_serial_error,
    list_com_ports,
    remove_orb,
    update_orb,
)

# Palette — table UI와 톤 맞춤
C = {
    "bg": "#e8eaed",
    "card": "#ffffff",
    "ink": "#141414",
    "muted": "#6b7280",
    "line": "#d1d5db",
    "accent": "#5b8def",
    "accent_soft": "#eef4ff",
    "ok": "#16a34a",
    "ok_soft": "#dcfce7",
    "new": "#dc2626",
    "new_soft": "#fee2e2",
    "known": "#15803d",
    "warn": "#a16207",
    "warn_soft": "#fef9c3",
    "tab_track": "#eceef1",
}


class RegisterApp:
    def __init__(
        self,
        state: RegisterState,
        serial_mgr: SerialManager,
        default_port: str = "",
    ) -> None:
        self.state = state
        self.serial_mgr = serial_mgr
        self.default_port = default_port
        self._port_map: dict[str, str] = {}

        self.root = tk.Tk()
        self.list_search_var = tk.StringVar(master=self.root)
        self.list_edit_id_var = tk.StringVar(master=self.root)
        self.root.title("Orb Register")
        self.root.configure(bg=C["bg"])
        self.root.minsize(700, 680)
        self.root.geometry("760x720")

        self._fonts = {
            "title": tkfont.Font(family="Segoe UI", size=18, weight="bold"),
            "section": tkfont.Font(family="Segoe UI", size=11, weight="bold"),
            "body": tkfont.Font(family="Segoe UI", size=10),
            "mono": tkfont.Font(family="Consolas", size=11),
            "badge": tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            "chip": tkfont.Font(family="Segoe UI", size=9),
            "tab": tkfont.Font(family="Segoe UI", size=10),
            "tab_sel": tkfont.Font(family="Segoe UI", size=11, weight="bold"),
        }

        self._setup_styles()
        self._build()
        state.add_listener(self._on_detection)
        state.add_log_listener(self._on_log)

        self._refresh_ports(select=default_port)
        self._load_logs()
        self.root.after(300, self._poll_serial)
        self._refresh_all()

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Card.TLabelframe",
            background=C["card"],
            bordercolor=C["ink"],
            relief="solid",
            borderwidth=2,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=C["card"],
            foreground=C["ink"],
            font=self._fonts["section"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=C["card"],
            background=C["card"],
        )
        style.configure(
            "Treeview",
            background=C["card"],
            fieldbackground=C["card"],
            rowheight=28,
            font=self._fonts["body"],
        )
        style.configure(
            "Treeview.Heading",
            font=self._fonts["section"],
            background=C["accent_soft"],
        )

    def _style_tab_btn(self, btn: tk.Button, *, selected: bool) -> None:
        track = C["tab_track"]
        if selected:
            btn.configure(
                font=self._fonts["tab_sel"],
                bg=C["ink"],
                fg="#ffffff",
                activebackground="#2d2d2d",
                activeforeground="#ffffff",
                padx=14,
                pady=4,
            )
        else:
            btn.configure(
                font=self._fonts["tab"],
                bg=track,
                fg=C["muted"],
                activebackground="#e2e5e9",
                activeforeground=C["ink"],
                padx=12,
                pady=4,
            )

    def _switch_tab(self, tab: str) -> None:
        self._active_tab = tab
        if tab == "log":
            self.list_tab.pack_forget()
            self.log_tab.pack(fill=tk.BOTH, expand=True)
            self._style_tab_btn(self.tab_log_btn, selected=True)
            self._style_tab_btn(self.tab_list_btn, selected=False)
        else:
            self.log_tab.pack_forget()
            self.list_tab.pack(fill=tk.BOTH, expand=True)
            self._style_tab_btn(self.tab_log_btn, selected=False)
            self._style_tab_btn(self.tab_list_btn, selected=True)

    def _card(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=C["ink"], padx=2, pady=2)
        inner = tk.Frame(outer, bg=C["card"], padx=16, pady=14)
        inner.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            inner,
            text=title,
            font=self._fonts["section"],
            bg=C["card"],
            fg=C["ink"],
        ).pack(anchor=tk.W, pady=(0, 10))
        return outer, inner

    def _pill_btn(
        self,
        parent,
        text: str,
        command,
        *,
        primary: bool = False,
        small: bool = False,
    ) -> tk.Button:
        if primary:
            bg, fg, hover = C["ink"], "#ffffff", "#333333"
        else:
            bg, fg, hover = C["card"], C["ink"], C["accent_soft"]

        btn = tk.Button(
            parent,
            text=text,
            command=command,
            font=self._fonts["chip"] if small else self._fonts["body"],
            bg=bg,
            fg=fg,
            activebackground=hover,
            activeforeground="#ffffff" if primary else fg,
            relief=tk.FLAT,
            padx=14 if not small else 10,
            pady=6 if not small else 4,
            cursor="hand2",
            borderwidth=0 if primary else 1,
            highlightthickness=1,
            highlightbackground=C["ink"],
        )
        if primary:
            btn.bind("<Enter>", lambda e: btn.configure(bg=hover, fg="#ffffff"))
            btn.bind("<Leave>", lambda e: btn.configure(bg=C["ink"], fg="#ffffff"))
        else:
            btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
            btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
        return btn

    def _build(self) -> None:
        wrap = tk.Frame(self.root, bg=C["bg"], padx=20, pady=16)
        wrap.pack(fill=tk.BOTH, expand=True)

        # Header
        header = tk.Frame(wrap, bg=C["bg"])
        header.pack(fill=tk.X, pady=(0, 14))

        title_col = tk.Frame(header, bg=C["bg"])
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            title_col,
            text="Orb Register",
            font=self._fonts["title"],
            bg=C["bg"],
            fg=C["ink"],
        ).pack(anchor=tk.W)
        self.count_var = tk.StringVar()
        tk.Label(
            title_col,
            textvariable=self.count_var,
            font=self._fonts["body"],
            bg=C["bg"],
            fg=C["muted"],
        ).pack(anchor=tk.W, pady=(2, 0))

        self.banner = tk.Frame(wrap, bg=C["new_soft"], padx=14, pady=10)
        self.banner_msg = tk.Label(
            self.banner,
            font=self._fonts["body"],
            bg=C["new_soft"],
            fg=C["new"],
            wraplength=640,
            justify=tk.LEFT,
        )
        self.banner_msg.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            self.banner,
            text="✕",
            command=self._hide_banner,
            font=self._fonts["body"],
            bg=C["new_soft"],
            fg=C["new"],
            relief=tk.FLAT,
            borderwidth=0,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        status_row = tk.Frame(header, bg=C["bg"])
        status_row.pack(side=tk.RIGHT)
        self.status_dot = tk.Canvas(
            status_row, width=12, height=12, bg=C["bg"], highlightthickness=0
        )
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self._draw_dot(False)
        self.serial_var = tk.StringVar(value="연결 대기")
        tk.Label(
            status_row,
            textvariable=self.serial_var,
            font=self._fonts["body"],
            bg=C["bg"],
            fg=C["muted"],
        ).pack(side=tk.LEFT)

        # COM port card
        port_outer, port_inner = self._card(wrap, "Serial 포트")
        port_outer.pack(fill=tk.X, pady=(0, 12))

        port_row = tk.Frame(port_inner, bg=C["card"])
        port_row.pack(fill=tk.X)

        self.port_combo = ttk.Combobox(port_row, state="readonly", width=48)
        self.port_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.port_combo.bind("<<ComboboxSelected>>", self._on_port_selected)

        self._pill_btn(port_row, "새로고침", self._refresh_ports, small=True).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        # Detection — compact, no section title
        det_outer = tk.Frame(wrap, bg=C["ink"], padx=2, pady=2)
        det_outer.pack(fill=tk.X, pady=(0, 12))

        det_inner = tk.Frame(det_outer, bg=C["card"], padx=12, pady=8)
        det_inner.pack(fill=tk.X)

        badge_row = tk.Frame(det_inner, bg=C["card"])
        badge_row.pack(fill=tk.X)

        self.badge_label = tk.Label(
            badge_row,
            text="대기",
            font=self._fonts["badge"],
            bg=C["line"],
            fg=C["muted"],
            padx=8,
            pady=2,
        )
        self.badge_label.pack(side=tk.LEFT)

        self.id_var = tk.StringVar(value="오브를 리더에 태그하세요")
        tk.Label(
            badge_row,
            textvariable=self.id_var,
            font=self._fonts["mono"],
            bg="#f5f5f5",
            fg=C["ink"],
            padx=8,
            pady=4,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        self.known_var = tk.StringVar()
        tk.Label(
            det_inner,
            textvariable=self.known_var,
            font=self._fonts["chip"],
            bg=C["card"],
            fg=C["known"],
        ).pack(anchor=tk.W, pady=(4, 0))

        self.form = tk.Frame(det_inner, bg=C["card"])
        self.form.pack(fill=tk.X, pady=(6, 0))

        tk.Label(
            self.form,
            text="voice",
            font=self._fonts["chip"],
            bg=C["card"],
            fg=C["muted"],
        ).pack(anchor=tk.W)

        voice_row = tk.Frame(self.form, bg=C["card"])
        voice_row.pack(fill=tk.X, pady=(2, 4))
        self.voice_entry = tk.Entry(
            voice_row,
            font=self._fonts["mono"],
            relief=tk.FLAT,
            highlightthickness=2,
            highlightbackground=C["line"],
            highlightcolor=C["accent"],
        )
        self.voice_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        self.chip_frame = tk.Frame(self.form, bg=C["card"])
        self.chip_frame.pack(fill=tk.X, pady=(0, 4))

        btn_row = tk.Frame(self.form, bg=C["card"])
        btn_row.pack(fill=tk.X)
        self.btn_add = self._pill_btn(btn_row, "등록", self._do_add, primary=True)
        self.btn_add.pack(side=tk.LEFT)

        # Log + registered list (tabs)
        panel_outer = tk.Frame(wrap, bg=C["ink"], padx=2, pady=2)
        panel_outer.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        panel_inner = tk.Frame(panel_outer, bg=C["card"], padx=16, pady=14)
        panel_inner.pack(fill=tk.BOTH, expand=True)

        tab_bar = tk.Frame(panel_inner, bg=C["card"])
        tab_bar.pack(fill=tk.X, pady=(0, 8))

        tab_track = tk.Frame(tab_bar, bg=C["tab_track"], padx=3, pady=3)
        tab_track.pack(anchor=tk.W)

        tab_kw = dict(
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.tab_log_btn = tk.Button(
            tab_track, text="로그", command=lambda: self._switch_tab("log"), **tab_kw
        )
        self.tab_log_btn.pack(side=tk.LEFT, padx=(0, 2))
        self.tab_list_btn = tk.Button(
            tab_track,
            text="등록된 ID",
            command=lambda: self._switch_tab("list"),
            **tab_kw,
        )
        self.tab_list_btn.pack(side=tk.LEFT)

        content_area = tk.Frame(panel_inner, bg=C["card"])
        content_area.pack(fill=tk.BOTH, expand=True)

        self.log_tab = tk.Frame(content_area, bg=C["card"])
        self.list_tab = tk.Frame(content_area, bg=C["card"])

        self.log_box = tk.Frame(
            self.log_tab, bg="#f8f9fa", highlightthickness=1, highlightbackground=C["line"]
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            self.log_box,
            height=8,
            font=self._fonts["mono"],
            bg="#f8f9fa",
            fg=C["muted"],
            relief=tk.FLAT,
            padx=10,
            pady=8,
            wrap=tk.WORD,
            state=tk.DISABLED,
            cursor="arrow",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground=C["new"])
        self.log_text.tag_configure("warn", foreground=C["warn"])
        self.log_text.tag_configure("info", foreground=C["muted"])
        self.log_text.tag_configure("ok", foreground=C["ok"])

        list_toolbar = tk.Frame(self.list_tab, bg=C["card"])
        list_toolbar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            list_toolbar,
            text="voice 검색",
            font=self._fonts["chip"],
            bg=C["card"],
            fg=C["muted"],
        ).pack(side=tk.LEFT)
        self.list_search_entry = tk.Entry(
            list_toolbar,
            textvariable=self.list_search_var,
            font=self._fonts["mono"],
            relief=tk.FLAT,
            highlightthickness=2,
            highlightbackground=C["line"],
            highlightcolor=C["accent"],
        )
        self.list_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=4)
        self.list_search_var.trace_add("write", lambda *_: self._on_list_search())

        edit_row = tk.Frame(self.list_tab, bg=C["card"])
        edit_row.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            edit_row,
            text="voice",
            font=self._fonts["chip"],
            bg=C["card"],
            fg=C["muted"],
        ).pack(side=tk.LEFT)
        self.list_edit_voice = tk.Entry(
            edit_row,
            font=self._fonts["mono"],
            relief=tk.FLAT,
            highlightthickness=2,
            highlightbackground=C["line"],
            highlightcolor=C["accent"],
        )
        self.list_edit_voice.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=4)
        self._pill_btn(edit_row, "저장", self._do_update_voice, primary=True, small=True).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._pill_btn(edit_row, "선택 삭제", self._do_remove, small=True).pack(side=tk.LEFT)

        tk.Label(
            self.list_tab,
            textvariable=self.list_edit_id_var,
            font=self._fonts["chip"],
            bg=C["card"],
            fg=C["muted"],
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 6))

        self.tree_wrap = tk.Frame(self.list_tab, bg=C["card"])
        self.tree_wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "voice")
        self.tree = ttk.Treeview(self.tree_wrap, columns=cols, show="headings")
        self.tree.heading("id", text="ID")
        self.tree.heading("voice", text="voice")
        self.tree.column("id", width=440, stretch=True)
        self.tree.column("voice", width=120, stretch=False)
        self.tree.bind("<<TreeviewSelect>>", self._on_list_select)
        self.tree_scroll = ttk.Scrollbar(
            self.tree_wrap, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._switch_tab("log")

        bottom = tk.Frame(wrap, bg=C["bg"])
        bottom.pack(fill=tk.X, pady=(0, 0))
        self._pill_btn(bottom, "닫기", self.root.destroy).pack(side=tk.RIGHT)

        self._set_form_visible(False)

    def _hide_banner(self) -> None:
        self.banner.pack_forget()

    def _show_banner(self, kind: str, message: str) -> None:
        styles = {
            "error": (C["new_soft"], C["new"]),
            "warn": (C["warn_soft"], C["warn"]),
            "ok": (C["ok_soft"], C["known"]),
        }
        bg, fg = styles.get(kind, (C["line"], C["muted"]))
        self.banner.configure(bg=bg)
        self.banner_msg.configure(text=message, bg=bg, fg=fg)
        for child in self.banner.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(bg=bg, fg=fg)
        self.banner.pack(fill=tk.X, pady=(0, 12))

    def _on_log(self, entry: dict) -> None:
        self.root.after(0, lambda: self._append_log(entry))

    def _format_log_line(self, entry: dict) -> tuple[str, str]:
        ts = datetime.fromtimestamp(entry["at"]).strftime("%H:%M:%S")
        level = entry.get("level", "info")
        return f"{ts}  {entry['message']}\n", level

    def _append_log(self, entry: dict) -> None:
        line, level = self._format_log_line(entry)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line, level)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _load_logs(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for entry in reversed(self.state.snapshot()["logs"]):
            line, level = self._format_log_line(entry)
            self.log_text.insert(tk.END, line, level)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _user_message(self, kind: str, message: str) -> None:
        msg = friendly_serial_error(message)
        self.state.push_log(kind, msg)
        if kind in ("error", "warn"):
            self._show_banner(kind, msg)
        elif kind == "ok":
            self._show_banner("ok", msg)
            self.root.after(2500, self._hide_banner)

    def _draw_dot(self, on: bool) -> None:
        self.status_dot.delete("all")
        color = C["ok"] if on else "#f87171"
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline=C["ink"], width=1)

    def _refresh_ports(self, select: str | None = None) -> None:
        ports = list_com_ports()
        self._port_map = {p["label"]: p["device"] for p in ports}
        labels = list(self._port_map.keys())

        if not labels:
            labels = ["(사용 가능한 포트 없음)"]
            self._port_map = {}

        self.port_combo["values"] = labels

        pick_label = None
        if select:
            for label, device in self._port_map.items():
                if device == select:
                    pick_label = label
                    break

        if pick_label:
            self.port_combo.set(pick_label)
            self.serial_mgr.set_port(select)
        elif labels and labels[0] != "(사용 가능한 포트 없음)":
            self.port_combo.current(0)
            self.serial_mgr.set_port(self._port_map[labels[0]])
        else:
            self.port_combo.set(labels[0])
            self.serial_mgr.set_port("")

    def _on_port_selected(self, _event=None) -> None:
        label = self.port_combo.get()
        device = self._port_map.get(label, "")
        self.serial_mgr.set_port(device)

    def _on_detection(self, entry: dict) -> None:
        self.root.after(0, lambda: self._show_pending(entry))

    def _poll_serial(self) -> None:
        snap = self.state.snapshot()
        ok = snap["serial_ok"]
        self._draw_dot(ok)

        if ok:
            self.serial_var.set(f"연결됨 · {snap['serial_port']}")
        else:
            err = snap["serial_error"]
            port = self.serial_mgr.get_port() or "—"
            if err == "포트를 선택하세요":
                self.serial_var.set("포트 선택 필요")
            elif err:
                self.serial_var.set(f"끊김 · {port}")
            else:
                self.serial_var.set(f"대기 · {port}")

        self.count_var.set(f"{snap['mapping_count']}개 등록")
        self.root.after(500, self._poll_serial)

    def _set_badge(self, kind: str, text: str) -> None:
        styles = {
            "wait": (C["line"], C["muted"]),
            "new": (C["new_soft"], C["new"]),
            "known": (C["ok_soft"], C["known"]),
        }
        bg, fg = styles.get(kind, styles["wait"])
        self.badge_label.configure(text=text, bg=bg, fg=fg)

    def _rebuild_chips(self, groups: list[str]) -> None:
        for w in self.chip_frame.winfo_children():
            w.destroy()
        for g in groups:
            self._pill_btn(
                self.chip_frame,
                g,
                lambda grp=g: self._pick_group(grp),
                small=True,
            ).pack(side=tk.LEFT, padx=(0, 6), pady=2)

    def _pick_group(self, group: str) -> None:
        voice = suggest_voice(load_mapping(), group)
        self.voice_entry.delete(0, tk.END)
        self.voice_entry.insert(0, voice)

    def _show_pending(self, pending: dict | None) -> None:
        if not pending:
            self._set_badge("wait", "대기")
            self.id_var.set("오브를 리더에 태그하세요")
            self.known_var.set("")
            self._set_form_visible(False)
            return

        self.id_var.set(pending["id"])
        if pending["known"]:
            self._set_badge("known", "등록됨")
            self.known_var.set(f"voice · {pending['voice']}")
            self._set_form_visible(False)
        else:
            self._set_badge("new", "신규")
            self.known_var.set("")
            self._set_form_visible(True)
            snap = self.state.snapshot()
            self._rebuild_chips(snap["groups"])
            if not self.voice_entry.get().strip() and snap["groups"]:
                self._pick_group(snap["groups"][0])

    def _set_form_visible(self, on: bool) -> None:
        state = tk.NORMAL if on else tk.DISABLED
        self.voice_entry.configure(state=state)
        self.btn_add.configure(state=state)
        if on:
            self.btn_add.configure(fg="#ffffff")
        for w in self.chip_frame.winfo_children():
            w.configure(state=state)

    def _filtered_mapping_rows(self) -> list[dict]:
        rows = self.state.snapshot()["mapping"]
        q = self.list_search_var.get().strip().lower()
        if not q:
            return rows
        return [
            row
            for row in rows
            if q in row.get("voice", "").lower() or q in row.get("id", "").lower()
        ]

    def _on_list_search(self) -> None:
        sel_id = self._selected_list_id()
        self._refresh_mapping_list(select_id=sel_id)

    def _on_list_select(self, _event=None) -> None:
        orb_id = self._selected_list_id()
        if not orb_id:
            self.list_edit_id_var.set("")
            self.list_edit_voice.delete(0, tk.END)
            return
        values = self.tree.item(self.tree.selection()[0], "values")
        self.list_edit_id_var.set(f"선택 · {values[0]}")
        self.list_edit_voice.delete(0, tk.END)
        self.list_edit_voice.insert(0, values[1])

    def _selected_list_id(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return str(self.tree.item(sel[0], "values")[0])

    def _refresh_mapping_list(self, select_id: str | None = None) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        pick = None
        for row in self._filtered_mapping_rows():
            iid = self.tree.insert("", tk.END, values=(row["id"], row["voice"]))
            if select_id and row["id"] == select_id:
                pick = iid
        if pick:
            self.tree.selection_set(pick)
            self.tree.see(pick)
            self._on_list_select()
        elif select_id:
            self.list_edit_id_var.set("")
            self.list_edit_voice.delete(0, tk.END)

    def _refresh_all(self) -> None:
        snap = self.state.snapshot()
        self._show_pending(snap["pending"])
        self._refresh_mapping_list(select_id=self._selected_list_id())

    def _pending_id(self) -> str | None:
        pending = self.state.snapshot()["pending"]
        return pending["id"] if pending else None

    def _do_add(self) -> None:
        orb_id = self._pending_id()
        if not orb_id:
            return
        voice = self.voice_entry.get().strip()
        try:
            add_orb(orb_id, voice, self.state)
            self.voice_entry.delete(0, tk.END)
            self._refresh_all()
            self._hide_banner()
            self._switch_tab("list")
        except ValueError as e:
            self._user_message("error", str(e))

    def _do_update_voice(self) -> None:
        orb_id = self._selected_list_id()
        if not orb_id:
            messagebox.showinfo("수정", "목록에서 오브를 선택하세요.")
            return
        voice = self.list_edit_voice.get().strip()
        try:
            update_orb(orb_id, voice, self.state)
            self._refresh_all()
            self._hide_banner()
        except KeyError:
            self._user_message("error", "목록에 없는 오브입니다.")
        except ValueError as e:
            self._user_message("error", str(e))

    def _do_remove(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("삭제", "목록에서 오브를 선택하세요.")
            return
        orb_id = self.tree.item(sel[0], "values")[0]
        if not messagebox.askyesno("삭제", f"{orb_id}\n삭제할까요?"):
            return
        try:
            remove_orb(orb_id)
            self.state.push_log("ok", f"삭제 · {orb_id}")
            self.list_edit_id_var.set("")
            self.list_edit_voice.delete(0, tk.END)
            self._refresh_all()
        except KeyError:
            self._user_message("error", "목록에 없는 오브입니다.")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        self.serial_mgr.stop()
        self.root.destroy()


def run_gui(
    state: RegisterState,
    default_port: str,
    baudrate: int,
) -> None:
    mgr = SerialManager(state, baudrate, default_port)
    mgr.start()
    RegisterApp(state, mgr, default_port).run()
