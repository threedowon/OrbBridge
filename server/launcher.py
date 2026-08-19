"""OrbBridge 단일 프로세스 실행기이며 PyInstaller 진입점이다."""

from __future__ import annotations

import sys
import time
import webbrowser

from server.app_paths import is_frozen


def _open_browser() -> None:
    time.sleep(1.2)
    for url in ("http://127.0.0.1:8080/screen", "http://127.0.0.1:8080/admin"):
        try:
            webbrowser.open(url)
        except OSError:
            pass


def main() -> None:
    from server.main import run_main

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
