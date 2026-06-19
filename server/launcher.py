"""OrbBridge single-process launcher — bridge + main (PyInstaller entry point)."""

from __future__ import annotations

import sys
import time
import webbrowser

from app_paths import ensure_data_files, is_frozen


def _open_browser() -> None:
    time.sleep(1.2)
    for url in ("http://127.0.0.1:8080/screen", "http://127.0.0.1:8080/table"):
        try:
            webbrowser.open(url)
        except OSError:
            pass


def main() -> None:
    ensure_data_files()

    from bridge_service import start_bridge_thread

    start_bridge_thread()
    time.sleep(0.4)

    from main import run_main

    if is_frozen():
        import threading

        threading.Thread(target=_open_browser, daemon=True).start()

    run_main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[OrbBridge] 종료")
        sys.exit(0)
