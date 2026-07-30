"""Direct Python → browser WebSocket event and snapshot service.

Live events use ``{"address": "/...", "args": [...]}``. A newly connected
client receives one authoritative snapshot first, so events missed while
disconnected do not leave any web interface in a stale state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
import threading
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed


SnapshotProvider = Callable[[], Mapping[str, Any]]
DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 8765


def event_message(address: str, args: Sequence[Any] | Any = ()) -> dict:
    """Build one live browser event in the shared wire format."""

    normalized_address = str(address or "").strip()
    if not normalized_address.startswith("/"):
        raise ValueError("WebSocket event address must start with '/'")

    if isinstance(args, Sequence) and not isinstance(args, (str, bytes, bytearray)):
        normalized_args = list(args)
    else:
        normalized_args = [args]
    return {
        "type": "event",
        "address": normalized_address,
        "args": normalized_args,
    }


def snapshot_message(snapshot: Mapping[str, Any]) -> dict:
    """Wrap a detached server snapshot for a reconnecting browser."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot provider must return a mapping")
    return {
        "type": "snapshot",
        "snapshot": dict(snapshot),
    }


class WebSocketService:
    """Own one WebSocket server loop on a background thread.

    Calls from HTTP, serial, Scheduler, and GameRuntime threads enqueue work
    onto this service's asyncio loop.  Every client has one writer queue, which
    guarantees its initial snapshot is sent before later live events.
    """

    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        *,
        host: str = DEFAULT_WS_HOST,
        port: int = DEFAULT_WS_PORT,
    ) -> None:
        if not callable(snapshot_provider):
            raise TypeError("snapshot_provider must be callable")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host must be a non-empty string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")

        self._snapshot_provider = snapshot_provider
        self.host = host.strip()
        self.port = port

        self._lifecycle_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._startup_error: BaseException | None = None

        # Accessed only from ``self._loop``.
        self._clients: dict[Any, asyncio.Queue[str | None]] = {}

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and self._loop is not None)

    @property
    def client_count(self) -> int:
        """Return an approximate count safe for diagnostics."""

        return len(self._clients)

    def start(self, timeout: float = 3.0) -> "WebSocketService":
        """Bind the socket and wait until the background server is ready."""

        with self._lifecycle_lock:
            if self.running:
                return self
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name="orbbridge-websocket",
                daemon=True,
            )
            self._thread.start()

        if not self._ready.wait(max(0.0, float(timeout))):
            raise TimeoutError("WebSocket server did not start in time")
        if self._startup_error is not None:
            raise RuntimeError(
                f"WebSocket server failed on {self.host}:{self.port}: "
                f"{self._startup_error}"
            ) from self._startup_error
        return self

    def broadcast(self, address: str, args: Sequence[Any] | Any = ()) -> bool:
        """Queue one live event for every currently connected browser."""

        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        payload = self._encode(event_message(address, args))
        try:
            asyncio.run_coroutine_threadsafe(self._enqueue(payload), loop)
        except RuntimeError:
            return False
        return True

    def close(self, timeout: float = 3.0) -> None:
        """Stop the server and disconnect clients. Repeated calls are safe."""

        with self._lifecycle_lock:
            thread = self._thread
            loop = self._loop
            stop_event = self._stop_event
            if thread is None:
                return
            if loop is not None and stop_event is not None and loop.is_running():
                loop.call_soon_threadsafe(stop_event.set)

        if thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))

        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    @staticmethod
    def _encode(message: Mapping[str, Any]) -> str:
        return json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        try:
            async with websockets.serve(self._handle_client, self.host, self.port) as server:
                # Port 0 is useful for isolated tests; expose the selected port.
                sockets = server.sockets or []
                if sockets:
                    self.port = int(sockets[0].getsockname()[1])
                self._ready.set()
                print(f"[WS] Listening on ws://{self.host}:{self.port}")
                await self._stop_event.wait()
        finally:
            self._clients.clear()
            self._stop_event = None
            self._loop = None

    async def _handle_client(self, websocket, _legacy_path=None) -> None:
        """Serve both modern one-argument and legacy two-argument handlers."""

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        try:
            snapshot = snapshot_message(self._snapshot_provider())
        except Exception as exc:
            snapshot = {
                "type": "snapshot",
                "snapshot": {},
                "error": str(exc),
            }

        # No await occurs between snapshot creation and registration.  A live
        # broadcast scheduled from another thread therefore queues after this
        # snapshot, never before it.
        queue.put_nowait(self._encode(snapshot))
        self._clients[websocket] = queue
        writer = asyncio.create_task(self._client_writer(websocket, queue))
        address = websocket.remote_address
        print(f"[WS] Client connected: {address}")

        try:
            await websocket.wait_closed()
        finally:
            self._clients.pop(websocket, None)
            queue.put_nowait(None)
            await writer
            print(f"[WS] Client disconnected: {address}")

    async def _client_writer(
        self,
        websocket,
        queue: asyncio.Queue[str | None],
    ) -> None:
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                await websocket.send(payload)
        except ConnectionClosed:
            return

    async def _enqueue(self, payload: str) -> None:
        for queue in tuple(self._clients.values()):
            queue.put_nowait(payload)
