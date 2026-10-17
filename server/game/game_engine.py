"""OrbBridge의 순수 게임 상태 모델이다.

시리얼, MIDI, HTTP, WebSocket, 파일, 스레드 의존성은 이 모듈 밖의
런타임 어댑터가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import monotonic
from typing import Callable, Mapping


REST = "R"
ROW_NOTES = ("C", "D", "E", "F", "G", "A", "B", "C")


class GameState(str, Enum):
    """다이어그램에서 사용하는 세 가지 게임 상태이다."""

    READY = "READY"
    RUNNING = "RUNNING"
    RESETTING = "RESETTING"


class EndReason(str, Enum):
    """RUNNING에서 RESETTING으로 전이된 이유이다."""

    COMPLETE = "complete"
    IDLE = "idle"
    TIME_OVER = "time_over"
    MANUAL = "manual"


class PlacementResult(str, Enum):
    CORRECT = "correct"
    WRONG = "wrong"
    UNKNOWN = "unknown"
    INACTIVE = "inactive"


class InvalidTransition(RuntimeError):
    """현재 상태에서 허용되지 않는 이벤트가 들어오면 발생한다."""


class TableNotEmpty(InvalidTransition):
    """오브가 남은 상태에서 start 이벤트가 들어오면 발생한다."""


@dataclass(frozen=True, order=True)
class CellAddress:
    slave: int
    cell: int

    def __post_init__(self) -> None:
        if isinstance(self.slave, bool) or not isinstance(self.slave, int):
            raise ValueError("slave must be an integer")
        if isinstance(self.cell, bool) or not isinstance(self.cell, int):
            raise ValueError("cell must be an integer")
        if self.slave < 1:
            raise ValueError("slave must be at least 1")
        if not 1 <= self.cell <= 16:
            raise ValueError("cell must be between 1 and 16")

    @property
    def position(self) -> int:
        """슬레이브당 두 열을 반영한 전체 멜로디 위치를 반환한다."""

        local_column = 1 if self.cell <= 8 else 2
        return local_column + (self.slave - 1) * 2

    @property
    def note(self) -> str:
        row = (self.cell - 1) % 8
        return ROW_NOTES[row]

    @property
    def key(self) -> str:
        return f"{self.slave}:{self.cell}"


@dataclass(frozen=True)
class Placement:
    orb_id: str
    voice: str | None
    known: bool


class GameEngine:
    """외부에서 주입한 시계를 사용하는 결정적 상태 머신이다."""

    def __init__(
        self,
        answers: Mapping[int | str, str],
        *,
        time_over_s: float | None,
        idle_s: float | None,
        dfesta_presses_required: int = 1,
        initial_timer_delay_s: float = 0,
        initial_hint_delay_s: float = 0,
        tray_present: bool | None = False,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._answers = self._normalize_answers(answers)
        self._required_positions = frozenset(
            position for position, note in self._answers.items() if note != REST
        )
        if not self._required_positions:
            raise ValueError("answers must contain at least one non-REST position")

        self.time_over_s = self._normalize_timeout(time_over_s, "time_over_s")
        self.idle_s = self._normalize_timeout(idle_s, "idle_s")
        if isinstance(initial_timer_delay_s, bool) or not isfinite(initial_timer_delay_s) or initial_timer_delay_s < 0:
            raise ValueError("initial_timer_delay_s must be a finite non-negative number")
        self.initial_timer_delay_s = float(initial_timer_delay_s)
        if isinstance(initial_hint_delay_s, bool) or not isfinite(initial_hint_delay_s) or initial_hint_delay_s < 0:
            raise ValueError("initial_hint_delay_s must be a finite non-negative number")
        self.initial_hint_delay_s = float(initial_hint_delay_s)
        self._initial_hint_pending = False
        self._initial_hint_at = 0.0
        self._timers_start_at: float | None = None
        self.dfesta_presses_required = self._normalize_press_count(
            dfesta_presses_required
        )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._last_clock_value: float | None = None

        self.state = GameState.READY
        self.end_reason: EndReason | None = None
        self._cells: dict[CellAddress, Placement] = {}
        self._orb_locations: dict[str, CellAddress] = {}
        self._started_at: float | None = None
        self._last_activity_at: float | None = None
        self._ended_at: float | None = None
        self._dfesta_presses = 0
        self._revision = 0
        self.tray_present = tray_present
        self.tray_seated = tray_present
        self._tray_expires_at: float | None = None
        self._table_cleared_at: float | None = None
        self._cleanup_end_reason: str | None = None

    def _tray_state(self, now: float) -> tuple[bool | None, bool | None]:
        if self._tray_expires_at is not None and now >= self._tray_expires_at:
            return None, None
        return self.tray_present, self.tray_seated

    def set_tray(self, present: bool | None, seated: bool | None, *,
                 ttl_s: float | None = None) -> bool:
        """Remember tray state; return a known unseated -> seated edge only."""
        if present is None or seated is None:
            if present is not None or seated is not None:
                raise ValueError("unknown tray state must use two null values")
        elif type(present) is not bool or type(seated) is not bool or (seated and not present):
            raise ValueError("invalid tray state")
        ttl_s = self._normalize_timeout(ttl_s, "tray ttl")
        now = self._now()
        previous = self._tray_state(now)
        self.tray_present, self.tray_seated = present, seated
        self._tray_expires_at = None if ttl_s is None else now + ttl_s
        if previous != (present, seated):
            self._revision += 1
        self._end_if_needed(now)
        # Expiry wins at the deadline. Only known physical changes are activity;
        # reconnect/unknown reports and periodic copies must not postpone Idle.
        if (self.state is GameState.RUNNING and previous != (present, seated)
                and previous[0] is not None and present is not None):
            self._last_activity_at = now
        if self.state is GameState.RESETTING and self._cleanup_is_clear():
            self._enter_ready()
        return previous[1] is False and seated is True

    def _cleanup_is_clear(self) -> bool:
        return self._table_is_clear() and self._tray_state(self._now())[0] is False

    @staticmethod
    def _normalize_answers(
        answers: Mapping[int | str, str],
    ) -> dict[int, str]:
        if not isinstance(answers, Mapping) or not answers:
            raise ValueError("answers must be a non-empty mapping")

        normalized: dict[int, str] = {}
        for raw_position, raw_note in answers.items():
            if isinstance(raw_position, bool):
                raise ValueError("answer positions must be positive integers")
            if isinstance(raw_position, int):
                position = raw_position
            elif isinstance(raw_position, str) and raw_position.strip().isdigit():
                position = int(raw_position.strip())
            else:
                raise ValueError("answer positions must be positive integers")
            if position < 1:
                raise ValueError("answer positions must be positive integers")
            if position in normalized:
                raise ValueError(f"duplicate answer position: {position}")

            if raw_note is None:
                raise ValueError(f"answer {position} must not be empty")
            note = str(raw_note).strip().upper()
            if note == "REST":
                note = REST
            if not note:
                raise ValueError(f"answer {position} must not be empty")
            normalized[position] = note
        return normalized

    @staticmethod
    def _normalize_timeout(
        value: float | None,
        name: str,
    ) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a positive number or None")
        try:
            duration = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a positive number or None") from exc
        if not isfinite(duration) or duration <= 0:
            raise ValueError(f"{name} must be a positive number or None")
        return duration

    @staticmethod
    def _normalize_press_count(value: int) -> int:
        """DFesta 완료 기준이 양의 정수인지 검증한다."""

        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("dfesta_presses_required must be a positive integer")
        return value

    def _now(self) -> float:
        try:
            now = float(self._clock())
        except (TypeError, ValueError) as exc:
            raise RuntimeError("clock must return a finite number") from exc
        if not isfinite(now):
            raise RuntimeError("clock must return a finite number")
        if self._last_clock_value is not None and now < self._last_clock_value:
            raise RuntimeError("clock moved backwards")
        self._last_clock_value = now
        return now

    @property
    def answers(self) -> dict[int, str]:
        return dict(self._answers)

    @property
    def cell_count(self) -> int:
        return len(self._cells)

    def start(self, *, allow_occupied: bool = False) -> GameState:
        """start 이벤트를 처리하고 READY에서 RUNNING으로 전이한다."""

        now = self._now()
        self._check_start(allow_occupied=allow_occupied)
        self.end_reason = None
        self._started_at = now
        self._initial_hint_pending = True
        self._initial_hint_at = now + self.initial_hint_delay_s
        self._timers_start_at = now + self.initial_timer_delay_s
        self._last_activity_at = now
        self._ended_at = None
        self._dfesta_presses = 0
        self._set_state(GameState.RUNNING)
        return self.state

    def claim_initial_hint(self) -> bool:
        """Claim the first hint once, after its loading deadline."""
        now = self._now()
        self._end_if_needed(now)
        if (self.state is not GameState.RUNNING or not self._initial_hint_pending
                or now < self._initial_hint_at):
            return False
        self._initial_hint_pending = False
        self._revision += 1
        return True

    def end(self, reason: EndReason | str = EndReason.MANUAL) -> GameState:
        """end 이벤트를 처리하고 RUNNING에서 RESETTING으로 전이한다."""

        now = self._now()
        if self.state is not GameState.RUNNING:
            raise InvalidTransition(f"END is invalid from {self.state.value}")
        if self._end_if_needed(now):
            return self.state
        try:
            normalized_reason = EndReason(reason)
        except ValueError as exc:
            raise ValueError(f"unknown end reason: {reason}") from exc
        self._enter_resetting(normalized_reason, now)
        return self.state

    def reset(self) -> GameState:
        """reset 이벤트를 처리하고 필요한 경우 RESETTING으로 전이한다."""

        now = self._now()
        if self.state is GameState.READY and self._cleanup_is_clear():
            return self.state
        if self.state is GameState.RUNNING and self._end_if_needed(now):
            return self.state
        reason = EndReason.MANUAL if self.state is GameState.RUNNING else None
        self._enter_resetting(reason, now)
        return self.state

    def tick(self) -> GameState:
        """타이머와 TableIsClear 조건을 현재 시각 기준으로 검사한다."""

        now = self._now()
        if self.state is GameState.RUNNING:
            self._end_if_needed(now)
        elif self.state is GameState.RESETTING and self._cleanup_is_clear():
            # 빈 테이블에서도 RESETTING을 한 런타임 tick 동안 노출한 뒤 READY로 간다.
            self._enter_ready()
        return self.state

    def record_activity(self) -> bool:
        """Listen·Play 입력을 실제 활동으로 기록해 Idle 타이머를 초기화한다."""

        now = self._now()
        if self.state is not GameState.RUNNING:
            raise InvalidTransition(f"ACTIVITY is invalid from {self.state.value}")
        if self._end_if_needed(now):
            return False
        self._last_activity_at = now
        self._revision += 1
        return True

    def press_dfesta(self) -> bool:
        """정답 상태에서 들어온 DFestaPress를 완료 횟수에 반영한다."""

        now = self._now()
        if self.state is not GameState.RUNNING:
            raise InvalidTransition(f"DFESTA is invalid from {self.state.value}")
        if self._end_if_needed(now):
            return False

        if self._initial_hint_pending and self.initial_hint_delay_s > 0:
            return False

        # 정답 여부와 관계없이 실제 버튼 입력이므로 Idle 타이머를 초기화한다.
        self._last_activity_at = now
        if not self._is_correct():
            if self._dfesta_presses:
                self._dfesta_presses = 0
            self._revision += 1
            return False

        self._dfesta_presses += 1
        self._revision += 1
        if self._dfesta_presses >= self.dfesta_presses_required:
            self._enter_resetting(EndReason.COMPLETE, now)
        return True

    def add_orb(
        self,
        *,
        orb_id: str,
        slave: int,
        cell: int,
        voice: str | None = None,
        known: bool = True,
    ) -> PlacementResult:
        """모든 상태에서 오브를 추적하고 RUNNING에서만 정답을 판정한다."""

        address = CellAddress(slave, cell)
        normalized_orb_id = self._normalize_orb_id(orb_id)
        if not isinstance(known, bool):
            raise ValueError("known must be a boolean")
        normalized_voice = None if voice is None else str(voice)
        placement = Placement(normalized_orb_id, normalized_voice, known)

        now = self._now()
        self._end_if_needed(now)
        running = self.state is GameState.RUNNING

        previous_address = self._orb_locations.get(normalized_orb_id)
        current_at_target = self._cells.get(address)
        unchanged = previous_address == address and current_at_target == placement
        if unchanged:
            return (
                self._result_for(address, placement)
                if running
                else PlacementResult.INACTIVE
            )

        if previous_address is not None and previous_address != address:
            self._cells.pop(previous_address, None)

        replaced = self._cells.get(address)
        if replaced is not None and replaced.orb_id != normalized_orb_id:
            self._orb_locations.pop(replaced.orb_id, None)

        self._cells[address] = placement
        self._orb_locations[normalized_orb_id] = address
        self._revision += 1

        if running:
            self._last_activity_at = now
            result = self._result_for(address, placement)
            self._reset_completion_progress_if_incorrect()
            return result
        return PlacementResult.INACTIVE

    def remove_orb(
        self,
        *,
        slave: int,
        cell: int,
        orb_id: str | None = None,
    ) -> bool:
        """현재 오브를 제거하고 오래된 orb_id 입력은 무시한다."""

        address = CellAddress(slave, cell)
        normalized_orb_id = (
            None if orb_id is None else self._normalize_orb_id(orb_id)
        )

        now = self._now()
        self._end_if_needed(now)
        placement = self._cells.get(address)
        if placement is None:
            return False
        if normalized_orb_id is not None and placement.orb_id != normalized_orb_id:
            return False

        del self._cells[address]
        if self._orb_locations.get(placement.orb_id) == address:
            del self._orb_locations[placement.orb_id]
        self._revision += 1

        if self.state is GameState.RUNNING:
            self._last_activity_at = now
            self._reset_completion_progress_if_incorrect()
        elif self.state is GameState.RESETTING and self._cleanup_is_clear():
            self._enter_ready()
        return True

    def result_for_cell(
        self,
        *,
        slave: int,
        cell: int,
    ) -> PlacementResult | None:
        address = CellAddress(slave, cell)
        placement = self._cells.get(address)
        if placement is None:
            return None
        return self._result_for(address, placement)

    def snapshot(self) -> dict:
        """현재 상태를 JSON 직렬화 가능한 독립 snapshot으로 반환한다."""

        now = self._now()
        if self.state is GameState.RUNNING:
            self._end_if_needed(now)

        correct_positions, wrong_cells = self._solution_state()
        is_correct = (
            not wrong_cells
            and self._required_positions.issubset(correct_positions)
        )
        table_is_clear = self._table_is_clear()
        cells = {}
        for address in sorted(self._cells):
            placement = self._cells[address]
            cells[address.key] = {
                "slave": address.slave,
                "cell": address.cell,
                "position": address.position,
                "note": address.note,
                "orb_id": placement.orb_id,
                "voice": placement.voice,
                "known": placement.known,
                "result": self._result_for(address, placement).value,
            }

        elapsed_s = 0.0
        if self._started_at is not None:
            end = self._ended_at if self._ended_at is not None else now
            elapsed_s = max(0.0, end - self._started_at)

        remaining_time_over_s = None
        remaining_idle_s = None
        if self.state is GameState.RUNNING:
            if self.time_over_s is not None and self._started_at is not None:
                remaining_time_over_s = min(self.time_over_s, max(
                    0.0,
                    self._timers_start_at + self.time_over_s - now,
                ))
            if self.idle_s is not None and self._last_activity_at is not None:
                remaining_idle_s = min(self.idle_s, max(
                    0.0,
                    max(self._last_activity_at, self._timers_start_at) + self.idle_s - now,
                ))

        presses_remaining = max(
            0,
            self.dfesta_presses_required - self._dfesta_presses,
        )

        return {
            "state": self.state.value,
            "initial_hint": {
                "pending": self.state is GameState.RUNNING and self._initial_hint_pending,
                "delay_s": self.initial_hint_delay_s,
                "remaining_s": max(0.0, self._initial_hint_at - now)
                if self.state is GameState.RUNNING and self._initial_hint_pending else 0.0,
            },
            "tray": dict(zip(("present", "seated"), self._tray_state(now))),
            "cleanup_end_reason": self._cleanup_end_reason,
            "end_reason": (
                self.end_reason.value if self.end_reason is not None else None
            ),
            "revision": self._revision,
            "answers": {
                str(position): note
                for position, note in sorted(self._answers.items())
            },
            "required_positions": sorted(self._required_positions),
            "correct_positions": sorted(correct_positions),
            "wrong_cells": sorted(wrong_cells),
            "is_correct": is_correct,
            "conditions": {
                "check_start": (
                    self.state is GameState.READY and table_is_clear
                ),
                "is_correct": is_correct,
                "is_idle": self.end_reason is EndReason.IDLE,
                "is_time_over": self.end_reason is EndReason.TIME_OVER,
                "table_is_clear": table_is_clear,
                "cleanup_is_clear": self._cleanup_is_clear(),
            },
            "completion": {
                "waiting_for_dfesta": (
                    self.state is GameState.RUNNING and is_correct
                ),
                "dfesta_presses": self._dfesta_presses,
                "dfesta_presses_required": self.dfesta_presses_required,
                "dfesta_presses_remaining": presses_remaining,
            },
            "cell_count": len(self._cells),
            "cells": cells,
            "timers": {
                "since_table_cleared_s": (
                    max(0.0, now - self._table_cleared_at)
                    if self.state is GameState.READY and self._table_cleared_at is not None
                    else None
                ),
                "elapsed_s": elapsed_s,
                "time_over_limit_s": self.time_over_s,
                "idle_limit_s": self.idle_s,
                "remaining_time_over_s": remaining_time_over_s,
                "remaining_idle_s": remaining_idle_s,
            },
        }

    @staticmethod
    def _normalize_orb_id(orb_id: str) -> str:
        if orb_id is None:
            raise ValueError("orb_id must not be empty")
        normalized = str(orb_id).strip()
        if not normalized:
            raise ValueError("orb_id must not be empty")
        return normalized

    def _check_start(self, *, allow_occupied: bool = False) -> None:
        """CheckStart 조건을 검사한다."""

        if self.state is not GameState.READY:
            raise InvalidTransition(f"START is invalid from {self.state.value}")
        if not allow_occupied and not self._table_is_clear():
            raise TableNotEmpty("START requires an empty table")

    def _set_state(self, state: GameState) -> None:
        if self.state is state:
            return
        self.state = state
        self._revision += 1

    def _enter_resetting(
        self,
        reason: EndReason | None,
        now: float,
    ) -> None:
        if reason is not None:
            self.end_reason = reason
        if self._started_at is not None and self._ended_at is None:
            self._ended_at = now
        self._set_state(GameState.RESETTING)

    def _enter_ready(self) -> None:
        if not self._cleanup_is_clear():
            raise RuntimeError("cannot enter READY while the table is occupied")
        self._table_cleared_at = self._now()
        self._cleanup_end_reason = self.end_reason.value if self.end_reason is not None else None
        self.end_reason = None
        self._started_at = None
        self._timers_start_at = None
        self._last_activity_at = None
        self._ended_at = None
        self._dfesta_presses = 0
        self._set_state(GameState.READY)

    def _end_if_needed(self, now: float) -> bool:
        """IsTimeOver와 IsIdle을 검사하고 RESETTING으로 직접 전이한다."""

        if self.state is not GameState.RUNNING:
            return False

        deadlines: list[tuple[float, int, EndReason]] = []
        if self.time_over_s is not None and self._started_at is not None:
            deadlines.append(
                (
                    self._timers_start_at + self.time_over_s,
                    0,
                    EndReason.TIME_OVER,
                )
            )
        if self.idle_s is not None and self._last_activity_at is not None:
            deadlines.append(
                (
                    max(self._last_activity_at, self._timers_start_at) + self.idle_s,
                    1,
                    EndReason.IDLE,
                )
            )
        expired = [deadline for deadline in deadlines if now >= deadline[0]]
        if not expired:
            return False

        _, _, reason = min(expired)
        self._enter_resetting(reason, now)
        return True

    def _reset_completion_progress_if_incorrect(self) -> None:
        """정답 상태가 깨지면 누적된 DFestaPress 횟수를 초기화한다."""

        if not self._is_correct():
            self._dfesta_presses = 0

    def _result_for(
        self,
        address: CellAddress,
        placement: Placement,
    ) -> PlacementResult:
        if not placement.known:
            return PlacementResult.UNKNOWN
        expected = self._answers.get(address.position)
        if expected is None or expected == REST:
            return PlacementResult.WRONG
        if address.note == expected:
            return PlacementResult.CORRECT
        return PlacementResult.WRONG

    def _solution_state(self) -> tuple[set[int], list[str]]:
        correct_positions: set[int] = set()
        wrong_cells: list[str] = []
        for address, placement in self._cells.items():
            result = self._result_for(address, placement)
            if result is PlacementResult.CORRECT:
                correct_positions.add(address.position)
            else:
                wrong_cells.append(address.key)
        return correct_positions, wrong_cells

    def _is_correct(self) -> bool:
        correct_positions, wrong_cells = self._solution_state()
        return (
            not wrong_cells
            and self._required_positions.issubset(correct_positions)
        )

    def _table_is_clear(self) -> bool:
        return not self._cells
