"""UTF-8 runtime logs with a strict 1,000 physical lines per file."""
from __future__ import annotations

import atexit
from datetime import datetime
import logging
from pathlib import Path
import sys
import threading
import uuid

from server.app_paths import data_dir

LOG_DIR = data_dir().parent / "log"

class LineLog:
    def __init__(self, directory: Path, max_lines: int = 1000):
        self.directory = directory
        self.max_lines = max_lines
        self.session = datetime.now().strftime("%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.lock = threading.Lock()
        self.day = ""
        self.index = 0
        self.count = 0
        self.path = None

    def write(self, text: str, source: str = "LOG") -> None:
        with self.lock:
            for line in str(text).splitlines() or [""]:
                stamp = datetime.now().astimezone()
                day = stamp.strftime("%Y-%m-%d")
                if day != self.day or self.count >= self.max_lines:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    self.index += 1
                    self.day, self.count = day, 0
                    self.path = self.directory / f"orbbridge_{day}_{self.session}_{self.index:03d}.txt"
                with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(f"[{stamp.isoformat(sep=' ', timespec='milliseconds')}] [{source}] {line}\n")
                self.count += 1


_sink: LineLog | None = None
_install_lock = threading.RLock()
_installed = False
_error_reported = False
_fallback = sys.stderr


def write_log(text: str, source: str = "LOG") -> bool:
    global _error_reported
    # Library imports/tests have no file side effects; entry points install the sink.
    if _sink is None:
        return True
    try:
        _sink.write(text, source)
        _error_reported = False
        return True
    except OSError as exc:
        if not _error_reported:
            _error_reported = True
            try:
                _fallback.write(f"[log] TXT logging failed: {exc}\n")
                _fallback.flush()
            except (OSError, UnicodeError):
                pass
        return False


class Tee:
    """Preserve the console and assemble split print() writes per thread."""
    def __init__(self, console, source):
        self.console, self.source = console, source
        self.lock = threading.RLock()
        self.pending = {}

    def write(self, text):
        with self.lock:
            try:
                self.console.write(text)
            except UnicodeEncodeError:
                self.console.write(text.encode(self.encoding, errors="replace").decode(self.encoding))
            except OSError:
                pass
            thread = threading.current_thread()
            pending = self.pending.get(thread, "") + text
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                write_log(line.rstrip("\r"), self.source)
            if pending:
                self.pending[thread] = pending
            else:
                self.pending.pop(thread, None)
        return len(text)

    def flush(self):
        with self.lock:
            for text in self.pending.values():
                write_log(text, self.source)
            self.pending.clear()
            try:
                self.console.flush()
            except OSError:
                pass

    @property
    def encoding(self):
        return getattr(self.console, "encoding", None) or "utf-8"

    def isatty(self):
        return self.console.isatty()

    def fileno(self):
        return self.console.fileno()


def install_logging() -> None:
    global _sink, _installed, _fallback
    with _install_lock:
        if _installed:
            return
        _sink = LineLog(LOG_DIR)
        _fallback = sys.stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = Tee(sys.stdout, "STDOUT")
        sys.stderr = Tee(sys.stderr, "STDERR")
        root = logging.getLogger()
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is old_stderr:
                handler.setStream(sys.stderr)
            elif isinstance(handler, logging.StreamHandler) and handler.stream is old_stdout:
                handler.setStream(sys.stdout)
        if not root.handlers:
            logging.basicConfig(level=logging.INFO)
        atexit.register(sys.stdout.flush)
        atexit.register(sys.stderr.flush)
        _installed = True
        write_log("OrbBridge logging started (max 1000 lines/file)", "SYSTEM")
