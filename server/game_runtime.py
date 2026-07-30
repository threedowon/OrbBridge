"""Single-writer runtime adapter for :mod:`game_engine`.

Serial and HTTP producers may run on different threads.  This adapter funnels
every state mutation through one worker queue, advances timers, and exposes a
thread-safe cached snapshot for HTTP/UI readers.
"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
import logging
from queue import Empty, Queue
import threading
from typing import Any, Callable

try:
    from .game_engine import GameEngine
except ImportError:  # ``python server/main.py`` script context
    from game_engine import GameEngine


PhaseCallback = Callable[[str, str, dict], None]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeResult:
    """Result of one queued engine command."""

    value: Any
    previous_phase: str
    snapshot: dict

    @property
    def phase(self) -> str:
        return str(self.snapshot["phase"])

    @property
    def phase_changed(self) -> bool:
        return self.previous_phase != self.phase


@dataclass
class _Command:
    action: str
    payload: dict
    future: Future


class GameRuntime:
    """Own a ``GameEngine`` and mutate it only from one worker thread."""

    def __init__(
        self,
        engine: GameEngine,
        *,
        tick_interval_s: float = 0.05,
        on_phase_change: PhaseCallback | None = None,
        auto_reset_timeouts: bool = False,
    ) -> None:
        if not isinstance(engine, GameEngine):
            raise TypeError("engine must be a GameEngine")
        if isinstance(tick_interval_s, bool) or float(tick_interval_s) <= 0:
            raise ValueError("tick_interval_s must be positive")
        if on_phase_change is not None and not callable(on_phase_change):
            raise TypeError("on_phase_change must be callable")
        if not isinstance(auto_reset_timeouts, bool):
            raise TypeError("auto_reset_timeouts must be a boolean")

        self._engine = engine
        self._tick_interval_s = float(tick_interval_s)
        self._on_phase_change = on_phase_change
        self._auto_reset_timeouts = auto_reset_timeouts
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
        """Start the worker. Calling this repeatedly is safe."""
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
        """Stop the worker after all earlier queued commands."""
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
        """Return a detached copy of the most recently processed snapshot."""
        with self._snapshot_lock:
            return deepcopy(self._snapshot)

    def replace_engine(self, engine: GameEngine, *, timeout: float = 2.0) -> RuntimeResult:
        """Replace configuration while the current engine is READY and empty."""
        if not isinstance(engine, GameEngine):
            raise TypeError("engine must be a GameEngine")
        return self._submit("replace_engine", timeout=timeout, engine=engine)

    def start_game(self, *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("start", timeout=timeout)

    def complete_start(self, *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("complete_start", timeout=timeout)

    def finish_game(self, reason="manual", *, timeout: float = 2.0) -> RuntimeResult:
        return self._submit("finish", timeout=timeout, reason=reason)

    def press_dispensa(self, *, timeout: float = 2.0) -> RuntimeResult:
        """Queue one DFesta button press through the single engine writer."""
        return self._submit("press_dispensa", timeout=timeout)

    def record_activity(self, *, timeout: float = 2.0) -> RuntimeResult:
        """Queue a non-cell interaction that refreshes the idle deadline."""
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
        """Wait until all commands queued before this call have completed."""
        return self._submit("snapshot", timeout=timeout)

    def publish_phase_change(self, result: RuntimeResult) -> None:
        """Publish a command transition after its caller emits related output.

        Background timer transitions are published automatically.  Command
        transitions are explicit so an ``/orb/added`` message can be emitted
        before the resulting ``/game/end`` message.
        """
        if not result.phase_changed or self._on_phase_change is None:
            return
        self._on_phase_change(
            result.previous_phase,
            result.phase,
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
            should_reset = (
                self._auto_reset_timeouts
                and result.phase_changed
                and result.phase == "FINISHED"
                and result.snapshot.get("finish_reason")
                in {"total_timeout", "idle_timeout"}
            )
            try:
                self.publish_phase_change(result)
            finally:
                if should_reset:
                    # Background ticks have no HTTP/Serial caller that can
                    # request RESETTING. Do it even if a display callback fails;
                    # UI delivery must never hold the state machine in FINISHED.
                    resetting = self._execute("reset", {})
                    self.publish_phase_change(resetting)
        except Exception:
            # A clock/configuration failure must not silently kill the runtime
            # thread. A later explicit command will surface the same error.
            LOGGER.exception("game runtime tick failed")

    def _execute(self, action: str, payload: dict) -> RuntimeResult:
        previous_phase = self._engine.phase.value

        if action == "replace_engine":
            current = self._engine.snapshot()
            if current["phase"] != "READY" or current["cell_count"]:
                raise RuntimeError("engine can only be replaced while READY and empty")
            self._engine = payload["engine"]
            value = None
        elif action == "start":
            value = self._engine.start()
        elif action == "complete_start":
            value = self._engine.complete_start()
        elif action == "finish":
            value = self._engine.finish(payload["reason"])
        elif action == "press_dispensa":
            value = self._engine.press_dispensa()
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
            previous_phase=previous_phase,
            snapshot=deepcopy(snapshot),
        )
