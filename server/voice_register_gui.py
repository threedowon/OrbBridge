"""voice별 8개 샘플 파일을 등록하는 Tkinter 데스크톱 화면이다."""

from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from voice_preview import play_preview, stop_preview
from voice_register_core import VoiceRegisterState, save_voice
from voice_store import (
    NOTE_KEYS,
    NOTE_SLOTS,
    ensure_voice_profile,
    list_catalog_voices,
    load_mapping,
    load_voice_catalog,
    normalize_profile,
    orb_count_for_voice,
    resolve_media_path,
)

MONO_CANDIDATES = ("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "D2Coding", "Consolas", "Courier New")
UI_CANDIDATES = (
    "Pretendard",
    "Pretendard Variable",
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "Noto Sans KR",
    "Segoe UI Variable",
    "Segoe UI",
    "Arial",
)
AUDIO_EXTS = (
    ("미디어", "*.mp4 *.m4a *.mov *.webm *.mkv *.wav *.mp3 *.ogg *.flac *.aac"),
    ("모든 파일", "*.*"),
)

C = {
    "canvas": "#ffffff",        # 카드/입력 표면
    "canvas_soft": "#f4f5f6",   # 페이지 배경 · 보조 버튼 · 내부 패널
    "border": "#dcdee1",        # 카드·입력 테두리 (연회색)
    "primary": "#2a2d31",       # 주요 버튼 (charcoal)
    "primary_active": "#43474c",
    "primary_neutral": "#e6e7e9",  # 보조 버튼 hover
    "primary_pale": "#ececee",     # Treeview 선택 배경
    "ink": "#1a1c1f",           # 본문 텍스트
    "body": "#55595f",
    "mute": "#9a9ea5",
    "on_primary": "#ffffff",    # charcoal 버튼 위 텍스트
    "positive": "#3f4247",      # 로그 ok (중간 회색)
    "warning_deep": "#6c7075",  # 로그 warn (밝은 회색)
    "negative": "#111316",      # 로그 error (거의 검정)
}


def _pick_font(root: tk.Tk, candidates: tuple[str, ...], fallback: str) -> str:
    families = set(tkfont.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


def _natural_key(name: str) -> list:
    """숫자를 숫자로 비교하는 자연 정렬 키 ('2' < '10')."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _infer_note_key(stem: str) -> str | None:
    """파일명에서 음이름(C4..C5) 또는 번호(1~8)를 추론. 못 찾으면 None."""
    up = stem.upper()
    for nk in NOTE_KEYS:  # 'C4','D4',...,'C5'
        if re.search(rf"(?<![A-Z0-9]){nk}(?![A-Z0-9])", up):
            return nk
    m = re.search(r"(?<!\d)([1-8])(?!\d)", stem)
    if m:
        return NOTE_KEYS[int(m.group(1)) - 1]
    return None


def order_files_to_slots(paths: list[str]) -> tuple[dict[str, str], str]:
    """선택한 파일들을 8음(C4~C5) 슬롯에 배정.

    1) 모든 파일이 파일명으로 음이름/번호가 유일하게 추론되면 그대로 매핑
    2) 아니면 파일명 자연 정렬 순서대로 C4→C5 배정
    반환: (note_key → path, 방식 설명)
    """
    inferred: dict[str, str] = {}
    used: set[str] = set()
    ok = bool(paths)
    for p in paths:
        nk = _infer_note_key(Path(p).stem)
        if nk is None or nk in used:
            ok = False
            break
        inferred[nk] = p
        used.add(nk)
    if ok and len(inferred) == len(paths):
        return inferred, "파일명 음이름/번호 인식"

    ordered = sorted(paths, key=lambda p: _natural_key(Path(p).stem))
    mapping = {NOTE_KEYS[i]: p for i, p in enumerate(ordered[: len(NOTE_KEYS)])}
    return mapping, "파일명 순서대로 배정"


class VoiceRegisterApp:
    def __init__(self, state: VoiceRegisterState) -> None:
        self.state = state
        self._closing = False
        self._active_voice = ""
        self._slot_vars: dict[str, tk.StringVar] = {}

        self.root = tk.Tk()
        self.search_var = tk.StringVar(master=self.root)
        ui = _pick_font(self.root, UI_CANDIDATES, "TkDefaultFont")
        mono = _pick_font(self.root, MONO_CANDIDATES, "TkFixedFont")
        self.root.title("Voice Register")
        self.root.configure(bg=C["canvas_soft"])
        self.root.minsize(960, 840)
        self.root.geometry("1060x935")

        self._fonts = {
            "title": tkfont.Font(family=ui, size=26, weight="bold"),
            "section": tkfont.Font(family=ui, size=15, weight="bold"),
            "body": tkfont.Font(family=ui, size=12),
            "label": tkfont.Font(family=ui, size=12, weight="bold"),
            "mono": tkfont.Font(family=mono, size=11),
            "btn": tkfont.Font(family=ui, size=12, weight="bold"),
            "btn_sm": tkfont.Font(family=ui, size=11, weight="bold"),
            "tab": tkfont.Font(family=ui, size=10),
            "tab_sel": tkfont.Font(family=ui, size=10, weight="bold"),
        }

        self._setup_styles()
        self._build()
        state.add_log_listener(self._on_log)
        self._refresh_voice_list()
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
            rowheight=38,
            font=self._fonts["body"],
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=self._fonts["label"],
            background=C["canvas_soft"],
            foreground=C["body"],
            relief="flat",
            padding=(8, 8),
        )
        style.map(
            "Treeview",
            background=[("selected", C["primary_pale"])],
            foreground=[("selected", C["ink"])],
        )

    def _entry(self, parent, **kwargs) -> tk.Entry:
        opts = dict(
            font=self._fonts["mono"],
            bg=C["canvas"],
            fg=C["ink"],
            insertbackground=C["ink"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["ink"],
        )
        opts.update(kwargs)
        return tk.Entry(parent, **opts)

    def _pill_btn(self, parent, text, command, *, primary=False, small=False) -> tk.Button:
        bg = C["primary"] if primary else C["canvas_soft"]
        fg = C["on_primary"] if primary else C["ink"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=self._fonts["btn_sm"] if small else self._fonts["btn"],
            bg=bg,
            fg=fg,
            activebackground=C["primary_active"] if primary else C["primary_neutral"],
            activeforeground=C["on_primary"] if primary else C["ink"],
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=14 if small else 20,
            pady=7 if small else 10,
            cursor="hand2",
        )

    def _card(self, parent, title: str) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(
            parent,
            bg=C["canvas"],
            padx=24,
            pady=16,
            highlightbackground=C["border"],
            highlightcolor=C["border"],
            highlightthickness=1,
        )
        if title:
            tk.Label(
                outer,
                text=title,
                font=self._fonts["section"],
                bg=C["canvas"],
                fg=C["ink"],
            ).pack(anchor=tk.W, pady=(0, 10))
        inner = tk.Frame(outer, bg=C["canvas"])
        inner.pack(fill=tk.BOTH, expand=True)
        return outer, inner

    def _build(self) -> None:
        shell = tk.Frame(self.root, bg=C["canvas_soft"], padx=28, pady=18)
        shell.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(shell, bg=C["canvas_soft"])
        header.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            header,
            text="Voice Register",
            font=self._fonts["title"],
            bg=C["canvas_soft"],
            fg=C["ink"],
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            text="voice 키 · 8음 샘플 · sounds/ 로컬 저장",
            font=self._fonts["body"],
            bg=C["canvas_soft"],
            fg=C["mute"],
        ).pack(side=tk.LEFT, padx=(12, 0))

        body = tk.Frame(shell, bg=C["canvas_soft"])
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=2, minsize=300)
        body.columnconfigure(1, weight=5, minsize=480)
        body.rowconfigure(0, weight=1)

        left_wrap = tk.Frame(body, bg=C["canvas_soft"])
        left_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left_card, left = self._card(left_wrap, "")
        left_card.pack(fill=tk.BOTH, expand=True)

        search_row = tk.Frame(left, bg=C["canvas"])
        search_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(search_row, text="검색", font=self._fonts["body"], bg=C["canvas"], fg=C["mute"]).pack(
            side=tk.LEFT
        )
        ent = self._entry(search_row, textvariable=self.search_var, width=16)
        ent.pack(side=tk.LEFT, padx=(8, 0), ipady=2)
        self.search_var.trace_add("write", lambda *_: self._refresh_voice_list())

        tree_wrap = tk.Frame(left, bg=C["canvas"])
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        cols = ("voice", "orbs", "samples")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=10)
        self.tree.heading("voice", text="Voice")
        self.tree.heading("orbs", text="Orbs")
        self.tree.heading("samples", text="Samples")
        self.tree.column("voice", width=150, minwidth=100, stretch=True, anchor=tk.W)
        self.tree.column("orbs", width=62, minwidth=52, stretch=False, anchor=tk.CENTER)
        self.tree.column("samples", width=88, minwidth=72, stretch=False, anchor=tk.CENTER)
        self.tree.bind("<<TreeviewSelect>>", self._on_voice_select)
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        btn_row = tk.Frame(left, bg=C["canvas"])
        btn_row.pack(fill=tk.X, pady=(10, 0))
        self._pill_btn(btn_row, "새로고침", self._refresh_voice_list, small=True).pack(side=tk.LEFT)

        right_wrap = tk.Frame(body, bg=C["canvas_soft"])
        right_wrap.grid(row=0, column=1, sticky="nsew")
        right_card, right = self._card(right_wrap, "")
        right_card.pack(fill=tk.BOTH, expand=True)

        label_card = tk.Frame(right, bg=C["canvas_soft"], padx=16, pady=12)
        label_card.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            label_card,
            text="표시 이름 (screen)",
            font=self._fonts["label"],
            bg=C["canvas_soft"],
            fg=C["ink"],
        ).pack(anchor=tk.W, pady=(0, 6))
        label_row = tk.Frame(label_card, bg=C["canvas_soft"])
        label_row.pack(fill=tk.X)
        tk.Label(label_row, text="KR", font=self._fonts["body"], bg=C["canvas_soft"], fg=C["body"], width=4, anchor=tk.W).pack(side=tk.LEFT)
        self.label_kr_var = tk.StringVar(master=self.root)
        self._entry(label_row, textvariable=self.label_kr_var, width=14).pack(side=tk.LEFT, padx=(0, 12), ipady=2)
        tk.Label(label_row, text="EN", font=self._fonts["body"], bg=C["canvas_soft"], fg=C["body"], width=4, anchor=tk.W).pack(side=tk.LEFT)
        self.label_en_var = tk.StringVar(master=self.root)
        self._entry(label_row, textvariable=self.label_en_var, width=14).pack(side=tk.LEFT, ipady=2)

        batch_row = tk.Frame(right, bg=C["canvas"])
        batch_row.pack(fill=tk.X, pady=(0, 2))
        self._pill_btn(
            batch_row, "폴더에서 8개 한번에 선택", self._batch_assign, primary=True, small=True
        ).pack(side=tk.LEFT)
        tk.Label(
            right,
            text="선택한 파일을 C4~C5에 자동 배정 · 음이름/번호 인식, 없으면 이름순",
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["mute"],
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, 10))

        slots = tk.Frame(right, bg=C["canvas"])
        slots.pack(fill=tk.BOTH, expand=True)
        for note_key, label_ko in NOTE_SLOTS:
            self._slot_vars[note_key] = tk.StringVar(master=self.root)
            row = tk.Frame(slots, bg=C["canvas"], pady=3)
            row.pack(fill=tk.X)
            tk.Label(
                row,
                text=f"{label_ko}  {note_key}",
                font=self._fonts["label"],
                bg=C["canvas"],
                fg=C["ink"],
                width=10,
                anchor=tk.W,
            ).pack(side=tk.LEFT)
            path_entry = self._entry(row, textvariable=self._slot_vars[note_key])
            path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 8), ipady=2)
            self._pill_btn(
                row,
                "…",
                lambda nk=note_key: self._browse(nk),
                small=True,
            ).pack(side=tk.LEFT, padx=(0, 4))
            self._pill_btn(
                row,
                "▶",
                lambda nk=note_key: self._preview(nk),
                small=True,
            ).pack(side=tk.LEFT)
            self._pill_btn(
                row,
                "×",
                lambda nk=note_key: self._clear_slot(nk),
                small=True,
            ).pack(side=tk.LEFT, padx=(4, 0))

        action_row = tk.Frame(right, bg=C["canvas"])
        action_row.pack(fill=tk.X, pady=(10, 0))
        self._pill_btn(action_row, "저장", self._save_current, primary=True, small=True).pack(side=tk.LEFT)
        tk.Label(
            action_row,
            text="저장 시 sounds/{voice}/ 폴더로 복사됩니다",
            font=self._fonts["body"],
            bg=C["canvas"],
            fg=C["mute"],
        ).pack(side=tk.LEFT, padx=(12, 0))

        log_card, log_inner = self._card(shell, "로그")
        log_card.pack(fill=tk.BOTH, expand=False, pady=(12, 0))
        self.log_text = tk.Text(
            log_inner,
            height=3,
            font=self._fonts["mono"],
            bg=C["canvas"],
            fg=C["body"],
            relief=tk.FLAT,
            wrap=tk.WORD,
            state=tk.DISABLED,
            cursor="arrow",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground=C["negative"])
        self.log_text.tag_configure("warn", foreground=C["warning_deep"])
        self.log_text.tag_configure("info", foreground=C["mute"])
        self.log_text.tag_configure("ok", foreground=C["positive"])

    def _on_log(self, entry: dict) -> None:
        if self._closing:
            return
        self.root.after(0, lambda: self._append_log(entry))

    def _append_log(self, entry: dict) -> None:
        ts = datetime.fromtimestamp(entry["at"]).strftime("%H:%M:%S")
        level = entry.get("level", "info")
        line = f"{ts}  {entry['message']}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line, level)
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _load_logs(self) -> None:
        for entry in reversed(self.state.logs):
            self._append_log(entry)

    def _refresh_voice_list(self) -> None:
        mapping = load_mapping()
        catalog = load_voice_catalog()
        q = self.search_var.get().strip().lower()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for voice in list_catalog_voices(mapping):
            if q and q not in voice.lower():
                continue
            profile = normalize_profile(catalog.get("voices", {}).get(voice) or {})
            notes = profile.get("notes", {})
            filled = sum(1 for k, v in notes.items() if v)
            orbs = orb_count_for_voice(mapping, voice)
            self.tree.insert("", tk.END, iid=voice, values=(voice, orbs, f"{filled}/8"))

    def _on_voice_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        voice = sel[0]
        self._load_voice(voice)

    def _load_voice(self, voice: str) -> None:
        self._active_voice = voice
        ensure_voice_profile(voice)
        catalog = load_voice_catalog()
        profile = normalize_profile(catalog["voices"][voice])
        mapping = load_mapping()
        orbs = orb_count_for_voice(mapping, voice)
        self.root.title(f"Voice Register  ·  {voice}  ·  orb {orbs}개")
        for note_key, var in self._slot_vars.items():
            stored = profile["notes"].get(note_key, "")
            var.set(stored)
        self.label_kr_var.set(profile.get("labels", {}).get("kr", ""))
        self.label_en_var.set(profile.get("labels", {}).get("en", ""))

    def _browse(self, note_key: str) -> None:
        if not self._active_voice:
            messagebox.showinfo("Voice Register", "먼저 voice를 선택하세요.")
            return
        path = filedialog.askopenfilename(
            title=f"{self._active_voice} · {note_key}",
            filetypes=AUDIO_EXTS,
        )
        if path:
            self._slot_vars[note_key].set(path)

    def _batch_assign(self) -> None:
        """폴더에서 여러 파일을 한 번에 골라 8음(C4~C5) 슬롯에 채운다."""
        if not self._active_voice:
            messagebox.showinfo("Voice Register", "먼저 voice를 선택하세요.")
            return
        paths = filedialog.askopenfilenames(
            title=f"{self._active_voice} · 8음 파일 한번에 선택 (C4~C5)",
            filetypes=AUDIO_EXTS,
        )
        if not paths:
            return
        paths = list(paths)
        if len(paths) > len(NOTE_KEYS):
            messagebox.showwarning(
                "Voice Register",
                f"{len(paths)}개 선택됨 — 앞 8개만 사용합니다.",
            )

        mapping, how = order_files_to_slots(paths)
        for note_key in NOTE_KEYS:
            if note_key in mapping:
                self._slot_vars[note_key].set(mapping[note_key])

        lines = [
            f"{label} ({nk}) ← {Path(mapping[nk]).name}"
            for nk, label in NOTE_SLOTS
            if nk in mapping
        ]
        assigned = len(mapping)
        self.state.push_log("info", f"배치 배정 {assigned}/8 · {how}")
        msg = f"{how}\n\n" + "\n".join(lines)
        if assigned < len(NOTE_KEYS):
            msg += f"\n\n※ {assigned}/8만 배정됨 — 나머지는 개별 지정하세요."
        msg += "\n\n각 음의 ▶로 확인·개별 수정 후 [저장]을 누르세요."
        messagebox.showinfo("Voice Register — 배치 배정", msg)

    def _preview(self, note_key: str) -> None:
        raw = self._slot_vars[note_key].get().strip()
        if not raw:
            messagebox.showinfo("Voice Register", "파일이 없습니다.")
            return
        path = resolve_media_path(raw)
        if not path and Path(raw).is_file():
            path = Path(raw)
        if not path:
            messagebox.showerror("Voice Register", "파일을 찾을 수 없습니다.")
            return
        err = play_preview(path)
        if err:
            messagebox.showerror("Voice Register", err)

    def _clear_slot(self, note_key: str) -> None:
        self._slot_vars[note_key].set("")

    def _save_current(self) -> None:
        if not self._active_voice:
            messagebox.showinfo("Voice Register", "voice를 선택하세요.")
            return
        notes = {k: v.get().strip() for k, v in self._slot_vars.items()}
        has_notes = any(notes.values())
        has_labels = bool(self.label_kr_var.get().strip() or self.label_en_var.get().strip())
        if not has_notes and not has_labels:
            if not messagebox.askyesno("Voice Register", "샘플·이름이 모두 비어 있습니다. 저장할까요?"):
                return
        try:
            save_voice(
                self._active_voice,
                notes,
                self.state,
                label_kr=self.label_kr_var.get(),
                label_en=self.label_en_var.get(),
                preserve_notes=has_labels and not has_notes,
            )
        except FileNotFoundError as exc:
            messagebox.showerror("Voice Register", str(exc))
            self.state.push_log("error", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("Voice Register", str(exc))
            self.state.push_log("error", str(exc))
            return
        self._load_voice(self._active_voice)
        self._refresh_voice_list()
        messagebox.showinfo("Voice Register", f"{self._active_voice} 저장됨")

    def _on_close(self) -> None:
        self._closing = True
        stop_preview()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_close()


def run_gui(state: VoiceRegisterState) -> None:
    VoiceRegisterApp(state).run()
