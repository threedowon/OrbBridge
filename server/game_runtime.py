"""GameEngine을 단일 writer로 보호하는 런타임 어댑터이다.

서로 다른 스레드에서 실행되는 시리얼·HTTP 입력을 하나의 Queue로 모으고,
타이머를 갱신하며, HTTP·UI가 읽을 수 있는 thread-safe snapshot을 제공한다.
"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
import logging
from queue import Empty, Queue
import threading
from typing import Any, Callable

from server.game_engine import GameEngine


StateCallback = Callable[[str, str, dict], None]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeResult:
    """Queue에서 처리된 엔진 명령 한 건의 결과이다."""

    value: Any
    previous_state: str
    snapshot: dict

    @property
    def state(self) -> str:
        return str(self.snapshot["state"])

    @property
    def state_changed(self) -> bool:
        return self.previous_state != self.state


@dataclass
class _Command:
    action: str
    payload: dict
    future: Future


class GameRuntime:
    """GameEngine의 모든 변경을 하나의 worker 스레드에서 처리한다."""

    def __init__(
        self,
        engine: GameEngine,
        *,
        tick_interval_s: float = 0.05,
        on_state_change: StateCallback | None = None,
    ) -> None:
        if not isinstance(engine, GameEngine):
            raise TypeError("engine must be a GameEngine")
        if isinstance(tick_interval_s, bool) or float(tick_interval_s) <= 0:
            raise ValueError("tick_interval_s must be positive")
        if on_state_change is not None and not callable(on_state_change):
            raise TypeError("on_state_change must be callable")

        self._engine = engine
        self._tick_interval_s = float(tick_interval_s)
        self._on_state_change = on_state_change
        self._queue: Queue[_Command] = Queue()
        self._snapshot_lock = threading.Lock()
        self._snapshot = engine.snapshot()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._worker_ident: int | None = None
        self._closed = False

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        """worker를 시작하며 중복 호출은 무시한다."""

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("runtime is closed")
            if self.running:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="orbbridge-game-runtime",
                daemon=True,
            )
            self._thread.start()

    def close(self, timeout: float = 2.0) -> None:
        """앞서 등록된 명령을 처리한 뒤 worker를 종료한다."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is None:
            return

        future: Future = Future()
        self._queue.put(_Command("_stop", {}, future))
        try:
            future.result(timeout=timeout)
        finally:
            thread.join(timeout=max(0.0, timeout))

    def snapshot(self) -> dict:
        """마지막으로 처리된 snapshot의 독립 복사본을 반환한다."""

        with self._snapshot_lock:
            return deepcopy(self._snapshot)

    def replace_engine(
        self,
        engine: GameEngine,
        *,
        timeout: float = 2.0,
    ) -> RuntimeResult:
        """READY이면서 테이블이 비었을 때 설정이 다른 엔진으로 교체한다."""

        if not isinstance(engine, GameEngine):
            raise TypeError("engine must be a GameEngine")
        return self._submit("replace_engine", timeout=timeout, engine=engine)

    def start_game(self, *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("start", timeout=timeout)

    def end_game(self, reason="manual", *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("end", timeout=timeout, reason=reason)

    def press_dfesta(self, *, timeout: float = 2.0) -> RuntimeResult:
        """DFestaPress 한 건을 단일 writer Queue에 등록한다."""

        return self._submit("dfesta", timeout=timeout)

    def record_activity(self, *, timeout: float = 2.0) -> RuntimeResult:
        """셀 변경이 없는 입력을 Idle 타이머 활동으로 등록한다."""

        return self._submit("record_activity", timeout=timeout)

    def reset_game(self, *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("reset", timeout=timeout)

    def add_orb(
        self,
        *,
        orb_id: str,
        slave: int,
        cell: int,
        voice: str | None = None,
        known: bool = True,
        timeout: float = 2.0,
    ) -> RuntimeResult:
        return self._submit(
            "add_orb",
            timeout=timeout,
            orb_id=orb_id,
            slave=slave,
            cell=cell,
            voice=voice,
            known=known,
        )

    def remove_orb(
        self,
        *,
        slave: int,
        cell: int,
        orb_id: str | None = None,
        timeout: float = 2.0,
    ) -> RuntimeResult:
        return self._submit(
            "remove_orb",
            timeout=timeout,
            slave=slave,
            cell=cell,
            orb_id=orb_id,
        )

    def barrier(self, *, timeout: float = 2.0) -> RuntimeResult:
        """이 호출보다 먼저 등록된 모든 명령이 끝날 때까지 기다린다."""

        return self._submit("snapshot", timeout=timeout)

    def publish_state_change(self, result: RuntimeResult) -> None:
        """명령으로 발생한 상태 전이를 관련 출력 뒤에 발행한다.

        백그라운드 tick의 전이는 자동 발행한다. 명령 전이는 호출자가 명시적으로
        발행해 `/orb/added`와 `/game/state`의 순서를 결정할 수 있게 한다.
        """

        if not result.state_changed or self._on_state_change is None:
            return
        self._on_state_change(
            result.previous_state,
            result.state,
            deepcopy(result.snapshot),
        )

    def _submit(self, action: str, *, timeout: float, **payload) -> RuntimeResult:
        if threading.get_ident() == self._worker_ident:
            raise RuntimeError("runtime command cannot wait on its own worker")
        if self._closed:
            raise RuntimeError("runtime is closed")
        if not self.running:
            raise RuntimeError("runtime is not started")

        future: Future = Future()
        self._queue.put(_Command(action, payload, future))
        return future.result(timeout=timeout)

    def _run(self) -> None:
        self._worker_ident = threading.get_ident()
        try:
            while True:
                try:
                    command = self._queue.get(timeout=self._tick_interval_s)
                except Empty:
                    self._tick()
                    continue

                if command.action == "_stop":
                    command.future.set_result(True)
                    self._queue.task_done()
                    return

                try:
                    result = self._execute(command.action, command.payload)
                except Exception as exc:
                    command.future.set_exception(exc)
                else:
                    command.future.set_result(result)
                finally:
                    self._queue.task_done()
        finally:
            self._worker_ident = None

    def _tick(self) -> None:
        try:
            result = self._execute("tick", {})
            self.publish_state_change(result)
        except Exception:
            # 시계·설정 오류가 발생해도 worker가 조용히 종료되지 않게 기록한다.
            LOGGER.exception("game runtime tick failed")

    def _execute(self, action: str, payload: dict) -> RuntimeResult:
        previous_state = self._engine.state.value

        if action == "replace_engine":
            current = self._engine.snapshot()
            if current["state"] != "READY" or current["cell_count"]:
                raise RuntimeError("engine can only be replaced while READY and empty")
            self._engine = payload["engine"]
            value = None
        elif action == "start":
            value = self._engine.start()
        elif action == "end":
            value = self._engine.end(payload["reason"])
        elif action == "dfesta":
            value = self._engine.press_dfesta()
        elif action == "record_activity":
            value = self._engine.record_activity()
        elif action == "reset":
            value = self._engine.reset()
        elif action == "add_orb":
            value = self._engine.add_orb(**payload)
        elif action == "remove_orb":
            value = self._engine.remove_orb(**payload)
        elif action == "tick":
            value = self._engine.tick()
        elif action == "snapshot":
            value = None
        else:
            raise ValueError(f"unknown runtime action: {action}")

        snapshot = self._engine.snapshot()
        with self._snapshot_lock:
            self._snapshot = snapshot
        return RuntimeResult(
            value=value,
            previous_state=previous_state,
            snapshot=deepcopy(snapshot),
        )
