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

# Wise fintech theme — lime green CTA on sage canvas
MONO_CANDIDATES = ("JetBrains Mono", "Consolas", "Cascadia Mono", "Courier New")
UI_CANDIDATES = ("Inter", "Segoe UI", "Helvetica Neue", "Manrope", "Arial")


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


def _ui_font_family(root: tk.Tk) -> str:
    return _pick_font(root, UI_CANDIDATES, "TkDefaultFont")


def _mono_font_family(root: tk.Tk) -> str:
    return _pick_font(root, MONO_CANDIDATES, "TkFixedFont")


C = {
  # Surface
    "canvas": "#ffffff",
    "canvas_soft": "#e8ebe6",
    # Brand
    "primary": "#9fe870",
    "primary_active": "#cdffad",
    "primary_neutral": "#c5edab",
    "primary_pale": "#e2f6d5",
    # Text
    "ink": "#0e0f0c",
    "ink_deep": "#163300",
    "body": "#454745",
    "mute": "#868685",
    "on_primary": "#0e0f0c",
    "on_dark": "#e8ebe6",
    # Semantic
    "positive": "#2ead4b",
    "positive_deep": "#054d28",
    "warning": "#ffd11a",
    "warning_deep": "#b86700",
    "warning_content": "#4a3b1c",
    "negative": "#d03238",
    "negative_deep": "#a72027",
    "negative_darkest": "#a7000d",
    "negative_bg": "#320707",
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
        self._closing = False

        self.root = tk.Tk()
        self.list_search_var = tk.StringVar(master=self.root)
        self.list_edit_id_var = tk.StringVar(master=self.root, value="목록에서 항목을 선택하세요")
        ui = _ui_font_family(self.root)
        mono = _mono_font_family(self.root)
        self.root.title("Orb Register")
        self.root.configure(bg=C["canvas_soft"])
        self.root.minsize(700, 680)
        self.root.geometry("760x720")

        # Wise Sans 900 → Inter bold display; Inter 600 labels; Inter 400 body
        self._fonts = {
            "title": tkfont.Font(family=ui, size=32, weight="bold"),
            "section": tkfont.Font(family=ui, size=18, weight="bold"),
            "body": tkfont.Font(family=ui, size=12),
            "label": tkfont.Font(family=ui, size=12, weight="bold"),
            "mono": tkfont.Font(family=mono, size=11),
            "badge": tkfont.Font(family=ui, size=10, weight="bold"),
            "chip": tkfont.Font(family=ui, size=11),
            "tab": tkfont.Font(family=ui, size=10),
            "tab_sel": tkfont.Font(family=ui, size=10, weight="bold"),
            "btn": tkfont.Font(family=ui, size=11, weight="bold"),
            "btn_sm": tkfont.Font(family=ui, size=10, weight="bold"),
        }

        self._setup_styles()
        self._build()
        state.add_listener(self._on_detection)
        state.add_log_listener(self._on_log)

        self._refresh_ports(select=default_port)
        self._load_logs()
        self._schedule(300, self._poll_serial)
        self._refresh_all()

    def _schedule(self, ms: int, fn) -> None:
        if self._closing:
            return

        def job() -> None:
            if self._closing:
                return
            try:
                fn()
            except tk.TclError:
                pass

        try:
            self.root.after(ms, job)
        except tk.TclError:
            pass

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Card.TLabelframe",
            background=C["canvas"],
            bordercolor=C["ink"],
            relief="flat",
            borderwidth=0,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=C["canvas"],
            foreground=C["ink"],
            font=self._fonts["section"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=C["canvas"],
            background=C["canvas"],
            foreground=C["ink"],
            arrowcolor=C["body"],
            bordercolor=C["ink"],
            font=self._fonts["body"],
            padding=(4, 2),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["canvas"])],
            foreground=[("readonly", C["ink"])],
        )
        style.configure(
            "Treeview",
            background=C["canvas"],
            fieldbackground=C["canvas"],
            foreground=C["ink"],
            rowheight=36,
            font=self._fonts["body"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            font=self._fonts["label"],
            background=C["canvas_soft"],
            foreground=C["ink"],
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Treeview",
            background=[("selected", C["primary_pale"])],
            foreground=[("selected", C["ink"])],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=C["canvas_soft"],
            troughcolor=C["canvas"],
            bordercolor=C["canvas"],
            arrowcolor=C["body"],
        )

    def _entry(self, parent: tk.Widget, *, mono: bool = False, **kwargs) -> tk.Entry:
        opts = dict(
            font=self._fonts["mono"] if mono else self._fonts["body"],
            bg=C["canvas"],
            fg=C["ink"],
            insertbackground=C["ink"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=C["ink"],
            highlightcolor=C["ink"],
        )
        opts.update(kwargs)
        return tk.Entry(parent, **opts)

    def _style_tab_btn(self, btn: tk.Button, *, selected: bool) -> None:
        if selected:
            btn.configure(
                font=self._fonts["tab_sel"],
                bg=C["canvas"],
                fg=C["ink"],
                activebackground=C["canvas"],
                activeforeground=C["ink"],
                padx=10,
                pady=4,
            )
        else:
            btn.configure(
                font=self._fonts["tab"],
                bg=C["canvas_soft"],
                fg=C["mute"],
                activebackground=C["canvas_soft"],
                activeforeground=C["mute"],
                padx=10,
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

    def _card(
        self,
        parent,
        title: str,
        *,
        bordered: bool = False,
        compact: bool = False,
    ) -> tuple[tk.Frame, tk.Frame]:
        """card-content (white on sage) or currency-converter-card (ink border)."""
        bg = C["canvas"]
        pad = (16, 12) if compact else (24, 24)
        outer_kw: dict = dict(bg=bg, padx=pad[0], pady=pad[1])
        if bordered:
            outer = tk.Frame(
                parent,
                highlightthickness=1,
                highlightbackground=C["ink"],
                **outer_kw,
            )
        else:
            outer = tk.Frame(parent, **outer_kw)
        tk.Label(
            outer,
            text=title,
            font=self._fonts["label"] if compact else self._fonts["section"],
            bg=bg,
            fg=C["ink"],
        ).pack(anchor=tk.W, pady=(0, 6 if compact else 10))
        inner = tk.Frame(outer, bg=bg)
        inner.pack(fill=tk.BOTH, expand=True)
        return outer, inner

    def _pill_btn(
        self,
        parent,
        text: str,
        command,
        *,
        primary: bool = False,
        small: bool = False,
        outline: bool = False,
        on_dark: bool = False,
    ) -> tk.Button:
        if primary:
            bg, fg, pressed = C["primary"], C["on_primary"], C["primary_active"]
        elif on_dark:
            bg, fg, pressed = C["primary"], C["on_primary"], C["primary_active"]
        elif outline:
            bg, fg, pressed = C["canvas"], C["ink"], C["canvas"]
        else:
            bg, fg, pressed = C["canvas_soft"], C["ink"], C["primary_neutral"]

        return tk.Button(
            parent,
            text=text,
            command=command,
            font=self._fonts["btn_sm"] if small else self._fonts["btn"],
            bg=bg,
            fg=fg,
            activebackground=pressed if primary or on_dark else bg,
            activeforeground=fg,
            relief=tk.FLAT,
            padx=14 if not small else 10,
            pady=5 if not small else 3,
            cursor="hand2",
            borderwidth=1 if outline else 0,
            highlightthickness=1 if outline else 0,
            highlightbackground=C["ink"] if outline else bg,
            highlightcolor=C["ink"] if outline else bg,
        )

    def _build(self) -> None:
        wrap = tk.Frame(self.root, bg=C["canvas_soft"], padx=24, pady=20)
        wrap.pack(fill=tk.BOTH, expand=True)

        main = tk.Frame(wrap, bg=C["canvas_soft"])
        main.pack(fill=tk.BOTH, expand=True)

        # Header — hero-band on sage canvas
        header = tk.Frame(main, bg=C["canvas_soft"])
        header.pack(fill=tk.X, pady=(0, 16))

        title_col = tk.Frame(header, bg=C["canvas_soft"])
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            title_col,
            text="Orb Register",
            font=self._fonts["title"],
            bg=C["canvas_soft"],
            fg=C["ink"],
        ).pack(anchor=tk.W)
        self.count_var = tk.StringVar()
        tk.Label(
            title_col,
            textvariable=self.count_var,
            font=self._fonts["body"],
            bg=C["canvas_soft"],
            fg=C["body"],
        ).pack(anchor=tk.W, pady=(4, 0))

        self.banner = tk.Frame(main, bg=C["negative_bg"], padx=16, pady=12)
        self.banner_msg = tk.Label(
            self.banner,
            font=self._fonts["body"],
            bg=C["negative_bg"],
            fg=C["canvas"],
            wraplength=640,
            justify=tk.LEFT,
        )
        self.banner_msg.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            self.banner,
            text="✕",
            command=self._hide_banner,
            font=self._fonts["body"],
            bg=C["negative_bg"],
            fg=C["canvas"],
            relief=tk.FLAT,
            borderwidth=0,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        status_row = tk.Frame(header, bg=C["canvas_soft"])
        status_row.pack(side=tk.RIGHT)
        self.status_dot = tk.Canvas(
            status_row, width=12, height=12, bg=C["canvas_soft"], highlightthickness=0
        )
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))
        self._draw_dot(False)
        self.serial_var = tk.StringVar(value="연결 대기")
        tk.Label(
            status_row,
            textvariable=self.serial_var,
            font=self._fonts["body"],
            bg=C["canvas_soft"],
            fg=C["body"],
        ).pack(side=tk.LEFT)

        # Serial — compact card
        port_outer, port_inner = self._card(
            main, "Serial 포트", bordered=True, compact=True
        )
        port_outer.pack(fill=tk.X, pady=(0, 8))

        port_row = tk.Frame(port_inner, bg=C["canvas"])
        port_row.pack(fill=tk.X)

        self.port_combo = ttk.Combobox(port_row, state="readonly", width=36)
        self.port_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.port_combo.bind("<<ComboboxSelected>>", self._on_port_selected)

        self._pill_btn(port_row, "새로고침", self._refresh_ports, small=True).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        # Detection — card-content (white on sage)
        det_outer = tk.Frame(main, bg=C["canvas"], padx=16, pady=12)
        det_outer.pack(fill=tk.X, pady=(0, 8))

        badge_row = tk.Frame(det_outer, bg=C["canvas"])
        badge_row.pack(fill=tk.X)

        self.badge_label = tk.Label(
            badge_row,
            text="대기",
            font=self._fonts["badge"],
            bg=C["primary_pale"],
            fg=C["positive_deep"],
            padx=10,
            pady=2,
        )
        self.badge_label.pack(side=tk.LEFT)

        self.id_var = tk.StringVar(value="오브를 리더에 태그하세요")
        tk.Label(
            badge_row,
            textvariable=self.id_var,
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["ink"],
            padx=10,
            pady=4,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        self.known_var = tk.StringVar()
        self.known_label = tk.Label(
            det_outer,
            textvariable=self.known_var,
            font=self._fonts["chip"],
            bg=C["canvas"],
            fg=C["ink"],
        )

        self.form = tk.Frame(det_outer, bg=C["canvas"])
        self.form.pack(fill=tk.X, pady=(4, 0))

        tk.Label(
            self.form,
            text="Voice",
            font=self._fonts["label"],
            bg=C["canvas"],
            fg=C["body"],
        ).pack(anchor=tk.W)

        voice_row = tk.Frame(self.form, bg=C["canvas"])
        voice_row.pack(fill=tk.X, pady=(4, 8))
        self.voice_entry = self._entry(voice_row)
        self.voice_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        self.chip_frame = tk.Frame(self.form, bg=C["canvas"])
        self.chip_frame.pack(fill=tk.X, pady=(0, 8))

        btn_row = tk.Frame(self.form, bg=C["canvas"])
        btn_row.pack(fill=tk.X)
        self.btn_add = self._pill_btn(btn_row, "등록", self._do_add, primary=True, small=True)
        self.btn_add.pack(side=tk.LEFT)

        # Log + list — sage canvas; white card panels
        panel_outer = tk.Frame(main, bg=C["canvas_soft"], padx=0, pady=0)
        panel_outer.pack(fill=tk.BOTH, expand=True)

        tab_bar = tk.Frame(panel_outer, bg=C["canvas_soft"])
        tab_bar.pack(fill=tk.X, pady=(0, 6))

        tab_track = tk.Frame(tab_bar, bg=C["canvas_soft"], padx=0, pady=0)
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
        self.tab_log_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.tab_list_btn = tk.Button(
            tab_track,
            text="등록된 ID",
            command=lambda: self._switch_tab("list"),
            **tab_kw,
        )
        self.tab_list_btn.pack(side=tk.LEFT)

        content_area = tk.Frame(panel_outer, bg=C["canvas_soft"])
        content_area.pack(fill=tk.BOTH, expand=True)

        self.log_tab = tk.Frame(
            content_area,
            bg=C["canvas"],
            highlightthickness=1,
            highlightbackground=C["ink"],
            padx=16,
            pady=16,
        )
        self.list_tab = tk.Frame(
            content_area,
            bg=C["canvas"],
            highlightthickness=1,
            highlightbackground=C["ink"],
            padx=16,
            pady=16,
        )

        self.log_box = tk.Frame(self.log_tab, bg=C["canvas"])
        self.log_box.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            self.log_box,
            height=8,
            font=self._fonts["mono"],
            bg=C["canvas"],
            fg=C["body"],
            relief=tk.FLAT,
            padx=4,
            pady=8,
            wrap=tk.WORD,
            state=tk.DISABLED,
            cursor="arrow",
            insertbackground=C["ink"],
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground=C["negative"])
        self.log_text.tag_configure("warn", foreground=C["warning_deep"])
        self.log_text.tag_configure("info", foreground=C["mute"])
        self.log_text.tag_configure("ok", foreground=C["positive"])

        # --- 등록된 ID 탭: 검색 → 목록 → 하단 편집 ---
        list_toolbar = tk.Frame(self.list_tab, bg=C["canvas"])
        list_toolbar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            list_toolbar,
            text="검색",
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["mute"],
        ).pack(side=tk.LEFT)
        self.list_search_entry = self._entry(
            list_toolbar, textvariable=self.list_search_var, width=20
        )
        self.list_search_entry.pack(side=tk.LEFT, padx=(8, 0), ipady=2)
        self.list_search_var.trace_add("write", lambda *_: self._on_list_search())

        self.tree_wrap = tk.Frame(self.list_tab, bg=C["canvas"])
        self.tree_wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "voice")
        self.tree = ttk.Treeview(self.tree_wrap, columns=cols, show="headings", height=10)
        self.tree.heading("id", text="ID")
        self.tree.heading("voice", text="Voice")
        self.tree.column("id", width=400, stretch=True)
        self.tree.column("voice", width=100, stretch=False)
        self.tree.bind("<<TreeviewSelect>>", self._on_list_select)
        self.tree_scroll = ttk.Scrollbar(
            self.tree_wrap, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        edit_panel = tk.Frame(self.list_tab, bg=C["canvas_soft"], padx=12, pady=10)
        edit_panel.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            edit_panel,
            textvariable=self.list_edit_id_var,
            font=self._fonts["mono"],
            bg=C["canvas_soft"],
            fg=C["mute"],
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 6))

        edit_row = tk.Frame(edit_panel, bg=C["canvas_soft"])
        edit_row.pack(fill=tk.X)

        tk.Label(
            edit_row,
            text="Voice",
            font=self._fonts["body"],
            bg=C["canvas_soft"],
            fg=C["body"],
        ).pack(side=tk.LEFT)
        self.list_edit_voice = self._entry(edit_row, width=18)
        self.list_edit_voice.pack(side=tk.LEFT, padx=(8, 8), ipady=2)
        self._pill_btn(
            edit_row, "저장", self._do_update_voice, primary=True, small=True
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._pill_btn(edit_row, "삭제", self._do_remove, small=True, outline=True).pack(
            side=tk.LEFT
        )

        self._switch_tab("log")

        self._set_form_visible(False)

    def _hide_banner(self) -> None:
        self.banner.pack_forget()

    def _show_banner(self, kind: str, message: str) -> None:
        styles = {
            "error": (C["negative_bg"], C["canvas"]),
            "warn": (C["warning"], C["warning_content"]),
            "ok": (C["primary_pale"], C["positive_deep"]),
        }
        bg, fg = styles.get(kind, (C["ink"], C["primary"]))
        self.banner.configure(bg=bg)
        self.banner_msg.configure(text=message, bg=bg, fg=fg)
        for child in self.banner.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(bg=bg, fg=fg)
        self.banner.pack(fill=tk.X, pady=(0, 12))

    def _on_log(self, entry: dict) -> None:
        if self._closing:
            return
        self._schedule(0, lambda: self._append_log(entry))

    def _format_log_line(self, entry: dict) -> tuple[str, str]:
        ts = datetime.fromtimestamp(entry["at"]).strftime("%H:%M:%S")
        level = entry.get("level", "info")
        return f"{ts}  {entry['message']}\n", level

    def _append_log(self, entry: dict) -> None:
        if self._closing:
            return
        line, level = self._format_log_line(entry)
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line, level)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

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
            self._schedule(2500, self._hide_banner)

    def _draw_dot(self, on: bool) -> None:
        self.status_dot.delete("all")
        color = C["positive"] if on else C["mute"]
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
        if self._closing:
            return
        self._schedule(0, lambda: self._show_pending(entry))

    def _poll_serial(self) -> None:
        if self._closing:
            return
        try:
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
        except tk.TclError:
            return
        self._schedule(500, self._poll_serial)

    def _set_badge(self, kind: str, text: str) -> None:
        # badge-positive / badge-negative — Wise green reserved for CTAs only
        styles = {
            "wait": (C["canvas_soft"], C["mute"]),
            "new": (C["primary_pale"], C["positive_deep"]),
            "known": (C["primary_pale"], C["positive_deep"]),
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

    def _update_known_label(self) -> None:
        if self.known_var.get():
            self.known_label.pack(anchor=tk.W, pady=(4, 0), before=self.form)
        else:
            self.known_label.pack_forget()

    def _show_pending(self, pending: dict | None) -> None:
        if not pending:
            self._set_badge("wait", "대기")
            self.id_var.set("오브를 리더에 태그하세요")
            self.known_var.set("")
            self._update_known_label()
            self._set_form_visible(False)
            return

        self.id_var.set(pending["id"])
        if pending["known"]:
            self._set_badge("known", "등록됨")
            self.known_var.set(f"voice · {pending['voice']}")
            self._update_known_label()
            self._set_form_visible(False)
        else:
            self._set_badge("new", "신규")
            self.known_var.set("")
            self._update_known_label()
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
            self.btn_add.configure(fg=C["on_primary"], bg=C["primary"])
        else:
            self.btn_add.configure(fg=C["mute"], bg=C["primary_pale"])
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

    def _clear_list_selection(self) -> None:
        self.list_edit_id_var.set("목록에서 항목을 선택하세요")
        self.list_edit_voice.delete(0, tk.END)

    def _on_list_select(self, _event=None) -> None:
        orb_id = self._selected_list_id()
        if not orb_id:
            self._clear_list_selection()
            return
        values = self.tree.item(self.tree.selection()[0], "values")
        self.list_edit_id_var.set(values[0])
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
            self._clear_list_selection()

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
            self._clear_list_selection()
            self._refresh_all()
        except KeyError:
            self._user_message("error", "목록에 없는 오브입니다.")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.serial_mgr.stop()
        try:
            self.root.quit()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run_gui(
    state: RegisterState,
    default_port: str,
    baudrate: int,
) -> None:
    mgr = SerialManager(state, baudrate, default_port)
    mgr.start()
    RegisterApp(state, mgr, default_port).run()
