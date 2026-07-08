"""OSC → WebSocket bridge (embedded in OrbBridge.exe)."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
import websockets

OSC_HOST = "0.0.0.0"
OSC_PORT = 9000
WS_HOST = "127.0.0.1"
WS_PORT = 8765

ALLOWED_ADDRESSES = (
    "/game/start",
    "/game/timer",
    "/game/end",
    "/game/ready",
    "/game/listen",
    "/game/play",
    "/game/dispensa",
    "/orb/added",
    "/orb/removed",
)

clients: set = set()
loop = None
_started = False


def broadcast(message: dict) -> None:
    if not clients or loop is None:
        return
    data = json.dumps(message)
    asyncio.run_coroutine_threadsafe(_broadcast(data), loop)


async def _broadcast(data: str) -> None:
    dead: set = set()
    for ws in clients.copy():
        try:
            await ws.send(data)
        except websockets.ConnectionClosed:
            dead.add(ws)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


def osc_handler(address: str, *args) -> None:
    message = {"address": address, "args": list(args)}
    print(f"[OSC] {address} {list(args)}")
    broadcast(message)


def _port_in_use(host: str, port: int, udp: bool = False) -> bool:
    sock_type = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, sock_type) as s:
        try:
            s.bind((host if host != "0.0.0.0" else "127.0.0.1", port))
            return False
        except OSError:
            return True


def check_ports() -> None:
    busy = []
    if _port_in_use("0.0.0.0", OSC_PORT, udp=True):
        busy.append(f"OSC UDP {OSC_PORT}")
    if _port_in_use(WS_HOST, WS_PORT):
        busy.append(f"WebSocket {WS_PORT}")
    if busy:
        print("[ERROR] Port already in use:", ", ".join(busy))
        print("  Another OrbBridge instance may still be running.")
        sys.exit(1)


def run_osc() -> None:
    dispatcher = Dispatcher()
    for addr in ALLOWED_ADDRESSES:
        dispatcher.map(addr, osc_handler)
    server = BlockingOSCUDPServer((OSC_HOST, OSC_PORT), dispatcher)
    print(f"[OSC] Listening on {OSC_HOST}:{OSC_PORT}")
    server.serve_forever()


async def _ws_main() -> None:
    global loop
    loop = asyncio.get_running_loop()
    threading.Thread(target=run_osc, daemon=True).start()
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        print(f"[WS] Listening on ws://{WS_HOST}:{WS_PORT}")
        await asyncio.Future()


async def ws_handler(websocket) -> None:
    clients.add(websocket)
    addr = websocket.remote_address
    print(f"[WS] Client connected: {addr}")
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)
        print(f"[WS] Client disconnected: {addr}")


def start_bridge_thread() -> None:
    global _started
    if _started:
        return
    check_ports()

    def _run() -> None:
        asyncio.run(_ws_main())

    threading.Thread(target=_run, daemon=True, name="orb-bridge").start()
    _started = True
