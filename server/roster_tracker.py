"""보유 현황 트래커 (Roster Tracker).

Orb Register 실행 시 함께 뜨는 별도 창. 기대 로스터(있어야 할 voice 전체)를
격자로 보여주고, 스캔해서 감지된 orb 의 voice 를 초록색으로 활성화한다.
→ 지금 물리적으로 가진 오브 / 아직 없는 오브를 한눈에 파악.

시작  : 새 세션 시작 (초록 전부 초기화, 이후 스캔한 voice 초록)
멈춤  : 스캔 반영 일시정지 (초록 유지)
이어서: 멈춤 해제, 기존 초록에 이어서 계속 활성화
종료  : 초록 전부 원래대로(회색) 초기화, 세션 종료

시리얼은 Orb Register 와 공유(RegisterState.add_listener) → 포트 충돌 없음.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from mapping_store import load_mapping, normalize_id

# ── 기대 로스터 (있어야 할 voice) ─ 필요하면 여기만 수정 ────────────
EXPECTED_ROSTER: dict[str, int] = {
    "aespa": 4,
    "bts": 7,
    "cortis": 5,
    "enhypen": 7,
    "ive": 6,
    "riize": 6,
    "skz": 8,
    "txt": 5,
}

UI_CANDIDATES = ("Pretendard", "Malgun Gothic", "Noto Sans KR", "Segoe UI", "Arial")

COL = {
    "bg": "#f4f5f6",
    "card": "#ffffff",
    "border": "#dcdee1",
    "ink": "#1a1c1f",
    "body": "#55595f",
    "mute": "#9a9ea5",
    "idle_bg": "#edeef0",
    "idle_fg": "#9a9ea5",
    "green_bg": "#16a34a",
    "on_green": "#ffffff",
    "btn": "#2a2d31",
    "on_btn": "#ffffff",
    "btn2": "#e6e7e9",
    "btn2_dis": "#f0f1f2",
}


def _pick_font(root, candidates, fallback):
    fams = set(tkfont.families(root))
    for name in candidates:
        if name in fams:
            return name
    return fallback


class RosterTracker:
    def __init__(self, master: tk.Misc, state) -> None:
        self.state = state
        self._closed = False
        self.session_active = False
        self.paused = False
        self.collected: set[str] = set()
        self.unknown_ids: set[str] = set()

        self.voices: list[str] = []
        for grp, n in EXPECTED_ROSTER.items():
            self.voices += [f"{grp}_{i}" for i in range(1, n + 1)]
        self.roster_set = set(self.voices)
        self.total = len(self.voices)
        self.cells: dict[str, tk.Label] = {}

        self.win = tk.Toplevel(master)
        self.win.title("보유 현황 · Roster")
        self.win.configure(bg=COL["bg"])
        self.win.geometry("880x560+120+80")
        self.win.minsize(760, 480)

        ui = _pick_font(self.win, UI_CANDIDATES, "TkDefaultFont")
        self.f_title = tkfont.Font(family=ui, size=18, weight="bold")
        self.f_count = tkfont.Font(family=ui, size=22, weight="bold")
        self.f_state = tkfont.Font(family=ui, size=11)
        self.f_group = tkfont.Font(family=ui, size=11, weight="bold")
        self.f_cell = tkfont.Font(family=ui, size=11, weight="bold")
        self.f_btn = tkfont.Font(family=ui, size=11, weight="bold")

        self._build()
        self._repaint_all()
        self._update_buttons()

        state.add_listener(self._on_detection)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ────────────────────────────────────────────
    def _btn(self, parent, text, cmd, *, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self.f_btn,
            bg=COL["btn"] if primary else COL["btn2"],
            fg=COL["on_btn"] if primary else COL["ink"],
            activebackground="#43474c" if primary else "#d9dbde",
            activeforeground=COL["on_btn"] if primary else COL["ink"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=18,
            pady=8,
            cursor="hand2",
        )

    def _build(self) -> None:
        shell = tk.Frame(self.win, bg=COL["bg"], padx=20, pady=18)
        shell.pack(fill=tk.BOTH, expand=True)

        # 헤더: 제목 + 카운트 + 상태
        header = tk.Frame(shell, bg=COL["bg"])
        header.pack(fill=tk.X, pady=(0, 14))
        tk.Label(header, text="보유 현황", font=self.f_title, bg=COL["bg"], fg=COL["ink"]).pack(side=tk.LEFT)
        self.count_lbl = tk.Label(header, text=f"0 / {self.total}", font=self.f_count, bg=COL["bg"], fg=COL["ink"])
        self.count_lbl.pack(side=tk.RIGHT)

        # 컨트롤
        ctrl = tk.Frame(shell, bg=COL["bg"])
        ctrl.pack(fill=tk.X, pady=(0, 6))
        self.btn_start = self._btn(ctrl, "시작", self.start, primary=True)
        self.btn_pause = self._btn(ctrl, "멈춤", self.pause)
        self.btn_resume = self._btn(ctrl, "이어서", self.resume)
        self.btn_stop = self._btn(ctrl, "종료", self.stop)
        for b in (self.btn_start, self.btn_pause, self.btn_resume, self.btn_stop):
            b.pack(side=tk.LEFT, padx=(0, 8))
        self.state_lbl = tk.Label(ctrl, text="대기 중 — 시작을 누르세요", font=self.f_state, bg=COL["bg"], fg=COL["mute"])
        self.state_lbl.pack(side=tk.LEFT, padx=(8, 0))

        # 로스터 격자 (그룹별 세로 열)
        card = tk.Frame(
            shell, bg=COL["card"], padx=16, pady=14,
            highlightbackground=COL["border"], highlightcolor=COL["border"], highlightthickness=1,
        )
        card.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        grid = tk.Frame(card, bg=COL["card"])
        grid.pack(fill=tk.BOTH, expand=True)
        for col, (grp, n) in enumerate(EXPECTED_ROSTER.items()):
            grid.columnconfigure(col, weight=1)
            gcol = tk.Frame(grid, bg=COL["card"])
            gcol.grid(row=0, column=col, sticky="n", padx=6)
            tk.Label(gcol, text=grp, font=self.f_group, bg=COL["card"], fg=COL["body"]).pack(pady=(0, 6))
            for i in range(1, n + 1):
                voice = f"{grp}_{i}"
                cell = tk.Label(
                    gcol, text=str(i), font=self.f_cell,
                    bg=COL["idle_bg"], fg=COL["idle_fg"],
                    width=5, pady=6,
                )
                cell.pack(fill=tk.X, pady=2)
                self.cells[voice] = cell

        # 하단: 최근/미등록 안내
        self.note_lbl = tk.Label(
            shell, text="", font=self.f_state, bg=COL["bg"], fg=COL["mute"], anchor=tk.W, justify=tk.LEFT
        )
        self.note_lbl.pack(fill=tk.X, pady=(10, 0))

    # ── 페인팅 ────────────────────────────────────────
    def _paint(self, voice: str) -> None:
        cell = self.cells.get(voice)
        if cell:
            cell.configure(bg=COL["green_bg"], fg=COL["on_green"])

    def _repaint_all(self) -> None:
        for voice, cell in self.cells.items():
            if voice in self.collected:
                cell.configure(bg=COL["green_bg"], fg=COL["on_green"])
            else:
                cell.configure(bg=COL["idle_bg"], fg=COL["idle_fg"])
        self._update_count()

    def _update_count(self) -> None:
        self.count_lbl.configure(text=f"{len(self.collected)} / {self.total}")

    def _set_state(self, text: str, color: str | None = None) -> None:
        self.state_lbl.configure(text=text, fg=color or COL["mute"])

    def _update_note(self) -> None:
        if self.unknown_ids:
            ids = ", ".join(sorted(self.unknown_ids)[:6])
            more = f" 외 {len(self.unknown_ids) - 6}개" if len(self.unknown_ids) > 6 else ""
            self.note_lbl.configure(text=f"미등록 id 감지 {len(self.unknown_ids)}개: {ids}{more}")
        else:
            self.note_lbl.configure(text="")

    def _update_buttons(self) -> None:
        active = self.session_active
        paused = self.paused

        def en(b, on):
            b.configure(
                state=tk.NORMAL if on else tk.DISABLED,
                cursor="hand2" if on else "arrow",
            )

        en(self.btn_start, True)                 # 시작/재시작 항상 가능
        en(self.btn_pause, active and not paused)
        en(self.btn_resume, active and paused)
        en(self.btn_stop, active)

    # ── 컨트롤 동작 ───────────────────────────────────
    def start(self) -> None:
        self.session_active = True
        self.paused = False
        self.collected.clear()
        self.unknown_ids.clear()
        self._repaint_all()
        self._update_note()
        self._update_buttons()
        self._set_state("진행 중 — 오브를 스캔하세요", COL["ink"])

    def pause(self) -> None:
        if not self.session_active:
            return
        self.paused = True
        self._update_buttons()
        self._set_state("멈춤 — 초록 유지, 스캔 무시", COL["body"])

    def resume(self) -> None:
        if not self.session_active:
            return
        self.paused = False
        self._update_buttons()
        self._set_state("진행 중 — 이어서 스캔", COL["ink"])

    def stop(self) -> None:
        self.session_active = False
        self.paused = False
        self.collected.clear()
        self.unknown_ids.clear()
        self._repaint_all()
        self._update_note()
        self._update_buttons()
        self._set_state("종료 — 전부 초기화됨", COL["mute"])

    # ── 감지 수신 (시리얼 스레드 → after 로 UI 스레드) ─
    def _on_detection(self, entry: dict) -> None:
        if self._closed:
            return
        try:
            self.win.after(0, lambda e=entry: self._handle(e))
        except (tk.TclError, RuntimeError):
            pass

    def _handle(self, entry: dict) -> None:
        if self._closed or not self.session_active or self.paused:
            return
        raw = entry.get("id") or entry.get("raw")
        if not raw:
            return
        try:
            oid = normalize_id(str(raw))
        except Exception:
            return
        mapping = load_mapping()
        voice = (mapping.get(oid) or {}).get("voice")

        if voice and voice in self.roster_set:
            if voice not in self.collected:
                self.collected.add(voice)
                self._paint(voice)
                self._update_count()
            self._set_state(f"수집 · {voice}", COL["green_bg"])
        elif voice:
            self._set_state(f"로스터 외 voice · {voice}", COL["body"])
        else:
            self.unknown_ids.add(oid)
            self._update_note()
            self._set_state(f"미등록 id · {oid}", COL["body"])

    def _on_close(self) -> None:
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open_roster_tracker(master: tk.Misc, state) -> RosterTracker:
    return RosterTracker(master, state)
