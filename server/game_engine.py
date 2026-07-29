"""Pure game-state model for the OrbBridge Python-engine refactor.

This module intentionally has no Serial, MIDI, HTTP, OSC, WebSocket, file, or
thread dependencies. Runtime adapters will be connected in a later milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import monotonic
from typing import Callable, Mapping


REST = "R"
ROW_NOTES = ("C", "D", "E", "F", "G", "A", "B", "C")


class GamePhase(str, Enum):
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    RESETTING = "RESETTING"


class FinishReason(str, Enum):
    COMPLETE = "complete"
    TOTAL_TIMEOUT = "total_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    MANUAL = "manual"


class PlacementResult(str, Enum):
    CORRECT = "correct"
    WRONG = "wrong"
    UNKNOWN = "unknown"
    INACTIVE = "inactive"


class InvalidTransition(RuntimeError):
    """Raised when an event is not valid for the current game phase."""


class TableNotEmpty(InvalidTransition):
    """Raised when START is requested while at least one orb is present."""


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
        """Global melody position: two 8-row columns per slave."""
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
    """Deterministic OrbBridge state machine with an injectable clock."""

    def __init__(
        self,
        answers: Mapping[int | str, str],
        *,
        total_timeout_s: float | None,
        idle_timeout_s: float | None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._answers = self._normalize_answers(answers)
        self._required_positions = frozenset(
            position for position, note in self._answers.items() if note != REST
        )
        if not self._required_positions:
            raise ValueError("answers must contain at least one non-REST position")

        self.total_timeout_s = self._normalize_timeout(
            total_timeout_s, "total_timeout_s"
        )
        self.idle_timeout_s = self._normalize_timeout(
            idle_timeout_s, "idle_timeout_s"
        )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._last_clock_value: float | None = None

        self.phase = GamePhase.READY
        self.finish_reason: FinishReason | None = None
        self._cells: dict[CellAddress, Placement] = {}
        self._orb_locations: dict[str, CellAddress] = {}
        self._started_at: float | None = None
        self._last_activity_at: float | None = None
        self._finished_at: float | None = None
        self._revision = 0

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

    def start(self) -> GamePhase:
        """Handle START: READY -> STARTING. Timers do not start yet."""
        self._now()
        if self.phase is not GamePhase.READY:
            raise InvalidTransition(f"START is invalid from {self.phase.value}")
        if self._cells:
            raise TableNotEmpty("START requires an empty table")
        self.finish_reason = None
        self._started_at = None
        self._last_activity_at = None
        self._finished_at = None
        self._set_phase(GamePhase.STARTING)
        return self.phase

    def complete_start(self) -> GamePhase:
        """Handle START_COMPLETE: STARTING -> RUNNING and start both timers."""
        now = self._now()
        if self.phase is not GamePhase.STARTING:
            raise InvalidTransition(
                f"START_COMPLETE is invalid from {self.phase.value}"
            )
        self._started_at = now
        self._last_activity_at = now
        self._finished_at = None
        self.finish_reason = None
        self._set_phase(GamePhase.RUNNING)
        self._finish_if_complete(now)
        return self.phase

    def finish(self, reason: FinishReason | str = FinishReason.MANUAL) -> GamePhase:
        """Finish a running game explicitly, unless a timer already expired."""
        now = self._now()
        if self.phase is not GamePhase.RUNNING:
            raise InvalidTransition(f"FINISH is invalid from {self.phase.value}")
        if self._expire_if_needed(now):
            return self.phase
        try:
            normalized_reason = FinishReason(reason)
        except ValueError as exc:
            raise ValueError(f"unknown finish reason: {reason}") from exc
        self._enter_finished(normalized_reason, now)
        return self.phase

    def reset(self) -> GamePhase:
        """Enter RESETTING and return to READY only after the table is empty."""
        now = self._now()
        if self.phase is GamePhase.READY and not self._cells:
            return self.phase

        if self._started_at is not None and self._finished_at is None:
            self._finished_at = now
        self._set_phase(GamePhase.RESETTING)
        if not self._cells:
            self._enter_ready()
        return self.phase

    def tick(self) -> GamePhase:
        """Advance timeout/completion checks using the injected clock."""
        now = self._now()
        if self.phase is GamePhase.RUNNING:
            if not self._expire_if_needed(now):
                self._finish_if_complete(now)
        return self.phase

    def add_orb(
        self,
        *,
        orb_id: str,
        slave: int,
        cell: int,
        voice: str | None = None,
        known: bool = True,
    ) -> PlacementResult:
        """Track an orb in every phase; judge it only while RUNNING."""
        address = CellAddress(slave, cell)
        normalized_orb_id = self._normalize_orb_id(orb_id)
        if not isinstance(known, bool):
            raise ValueError("known must be a boolean")
        normalized_voice = None if voice is None else str(voice)
        placement = Placement(normalized_orb_id, normalized_voice, known)

        now = self._now()
        self._expire_if_needed(now)
        running = self.phase is GamePhase.RUNNING

        previous_address = self._orb_locations.get(normalized_orb_id)
        current_at_target = self._cells.get(address)
        unchanged = (
            previous_address == address
            and current_at_target == placement
        )
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
            self._finish_if_complete(now)
            return result
        return PlacementResult.INACTIVE

    def remove_orb(
        self,
        *,
        slave: int,
        cell: int,
        orb_id: str | None = None,
    ) -> bool:
        """Remove the current occupant. A mismatched orb_id is a stale no-op."""
        address = CellAddress(slave, cell)
        normalized_orb_id = (
            None if orb_id is None else self._normalize_orb_id(orb_id)
        )

        now = self._now()
        self._expire_if_needed(now)
        placement = self._cells.get(address)
        if placement is None:
            return False
        if normalized_orb_id is not None and placement.orb_id != normalized_orb_id:
            return False

        del self._cells[address]
        if self._orb_locations.get(placement.orb_id) == address:
            del self._orb_locations[placement.orb_id]
        self._revision += 1

        if self.phase is GamePhase.RUNNING:
            self._last_activity_at = now
            self._finish_if_complete(now)
        elif self.phase is GamePhase.RESETTING and not self._cells:
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
        """Return a JSON-serializable, current-state snapshot."""
        now = self._now()
        if self.phase is GamePhase.RUNNING:
            if not self._expire_if_needed(now):
                self._finish_if_complete(now)

        correct_positions, wrong_cells = self._solution_state()
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
            end = self._finished_at if self._finished_at is not None else now
            elapsed_s = max(0.0, end - self._started_at)

        remaining_total_s = None
        remaining_idle_s = None
        if self.phase is GamePhase.RUNNING:
            if self.total_timeout_s is not None and self._started_at is not None:
                remaining_total_s = max(
                    0.0, self._started_at + self.total_timeout_s - now
                )
            if self.idle_timeout_s is not None and self._last_activity_at is not None:
                remaining_idle_s = max(
                    0.0, self._last_activity_at + self.idle_timeout_s - now
                )

        return {
            "phase": self.phase.value,
            "finish_reason": (
                self.finish_reason.value if self.finish_reason is not None else None
            ),
            "revision": self._revision,
            "answers": {
                str(position): note
                for position, note in sorted(self._answers.items())
            },
            "required_positions": sorted(self._required_positions),
            "correct_positions": sorted(correct_positions),
            "wrong_cells": sorted(wrong_cells),
            "solved": self._is_solved(),
            "cell_count": len(self._cells),
            "cells": cells,
            "timers": {
                "elapsed_s": elapsed_s,
                "remaining_total_s": remaining_total_s,
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

    def _set_phase(self, phase: GamePhase) -> None:
        if self.phase is phase:
            return
        self.phase = phase
        self._revision += 1

    def _enter_finished(self, reason: FinishReason, now: float) -> None:
        self.finish_reason = reason
        self._finished_at = now
        self._set_phase(GamePhase.FINISHED)

    def _enter_ready(self) -> None:
        if self._cells:
            raise RuntimeError("cannot enter READY while the table is occupied")
        self.finish_reason = None
        self._started_at = None
        self._last_activity_at = None
        self._finished_at = None
        self._set_phase(GamePhase.READY)

    def _expire_if_needed(self, now: float) -> bool:
        if self.phase is not GamePhase.RUNNING:
            return False

        deadlines: list[tuple[float, int, FinishReason]] = []
        if self.total_timeout_s is not None and self._started_at is not None:
            deadlines.append(
                (
                    self._started_at + self.total_timeout_s,
                    0,
                    FinishReason.TOTAL_TIMEOUT,
                )
            )
        if self.idle_timeout_s is not None and self._last_activity_at is not None:
            deadlines.append(
                (
                    self._last_activity_at + self.idle_timeout_s,
                    1,
                    FinishReason.IDLE_TIMEOUT,
                )
            )
        expired = [deadline for deadline in deadlines if now >= deadline[0]]
        if not expired:
            return False

        _, _, reason = min(expired)
        self._enter_finished(reason, now)
        return True

    def _finish_if_complete(self, now: float) -> bool:
        if self.phase is GamePhase.RUNNING and self._is_solved():
            self._enter_finished(FinishReason.COMPLETE, now)
            return True
        return False

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

    def _is_solved(self) -> bool:
        correct_positions, wrong_cells = self._solution_state()
        return (
            not wrong_cells
            and self._required_positions.issubset(correct_positions)
        )
