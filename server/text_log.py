"""UTF-8 runtime logs with a strict 1,000 physical lines per file."""
from __future__ import annotations

import atexit
import json
import re
import tempfile
from datetime import datetime
import logging
from pathlib import Path
import sys
import threading
import uuid
from collections import deque
import time

from server.app_paths import data_dir

LOG_DIR = data_dir().parent / "log"
SETTINGS_PATH = data_dir() / "log_settings.json"
_recent_console = deque(maxlen=500)
_recent_console_lock = threading.Lock()


def recent_console() -> list[dict]:
    with _recent_console_lock:
        return [dict(row) for row in _recent_console]


def describe_log(line: str, source: str = "LOG") -> dict:
    """Add readable context without altering the original diagnostic message."""
    lower = line.lower()
    tag = re.match(r"\[([^\]]+)\]", line)
    tag = tag.group(1).lower() if tag else ""
    if source == "SYSTEM" or tag in ("input", "log", "orbbridge"):
        category = "SERVER"
    elif source == "SLAVE" or source.startswith("SERIAL/") or tag in ("serial", "slave", "master"):
        category = "HARDWARE"
    elif tag in ("dotmatrix", "led"):
        category = "LED"
    elif tag in ("midi", "audio") or (tag == "scheduler" and "midi" in lower):
        category = "MIDI"
    elif tag in ("http", "ws", "admin") or re.match(r"\w+:websockets\.", line):
        category = "ADMIN"
    elif source == "EVENT" or tag in ("game", "state", "added", "removed", "recover", "scheduler") or tag.startswith(("stage ", "melody")) or re.match(r"\w+:server\.game\.", line):
        category = "GAME"
    else:
        category = "SERVER"

    # Honour explicit severity first; stderr also carries normal INFO messages.
    explicit = re.match(r"(?:\[[^\]]+\]\s*)?\[?(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]?(?::|\s|$)", line, re.I)
    if explicit:
        level = {"WARN": "WARN", "WARNING": "WARN", "ERROR": "ERROR", "CRITICAL": "ERROR"}.get(explicit.group(1).upper(), "")
    elif source in ("SERIAL/error", "SERIAL/badjson", "SLAVE") or re.search(r"\bfailed\b|\bTraceback\b|\b\w*(?:Error|Exception):|오류|실패|통신 끊김", line):
        level = "ERROR"
    elif re.search(r"\bWARN(?:ING)?\b|경고|\[midi\] skip|\[dotmatrix\] disconnected", line, re.I):
        level = "WARN"
    else:
        level = ""
    prefix = f"[{category}]" + (f" [{level}]" if level else "")
    # Drop redundant subsystem tags, but keep detailed tags (e.g. stage number).
    message = re.sub(r"^\[(?:midi|audio|dotmatrix|led|http|ws|game|serial|admin|log|orbbridge)\]\s*", "", line, flags=re.I)
    return {"category": category, "level": level, "display": f"{prefix} {message}"}

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
                    stream.write(f"[{stamp.isoformat(sep=' ', timespec='milliseconds')}] {describe_log(line, source)['display']}\n")
                self.count += 1


_sink: LineLog | None = None
_install_lock = threading.RLock()
_installed = False
_error_reported = False
_fallback = sys.stderr


def _saved_directory() -> Path:
    if not SETTINGS_PATH.exists():
        return LOG_DIR
    value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))["directory"]
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("로그 폴더는 절대 경로여야 합니다.")
    return Path(value)


def _check_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b"log write check")
        probe.flush()


def get_log_settings() -> dict:
    with _install_lock:
        directory = _sink.directory if _sink is not None else _saved_directory()
        return {"directory": str(directory), "default_directory": str(LOG_DIR)}


def update_log_settings(data: dict) -> dict:
    value = data.get("directory")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("로그를 저장할 폴더 경로를 입력해 주세요.")
    directory = Path(value.strip()).expanduser()
    if not directory.is_absolute():
        raise ValueError("서버 PC의 전체 폴더 경로를 입력해 주세요.")
    with _install_lock:
        try:
            directory = directory.resolve()
            _check_directory(directory)
            from server.data_store import save_config
            # A separate local preference avoids overwriting game configuration.
            save_config({"directory": str(directory)}, SETTINGS_PATH)
            if _sink is not None:
                with _sink.lock:
                    _sink.directory = directory
                    _sink.day = ""
                    _sink.path = None
                    _sink.count = 0
        except OSError as exc:
            raise ValueError(f"로그 저장 폴더를 변경할 수 없습니다: {exc}") from exc
        write_log(f"로그 저장 폴더 변경: {directory}", "SYSTEM")
        return {"ok": True, **get_log_settings()}


def write_log(text: str, source: str = "LOG") -> bool:
    global _error_reported
    if source in ("STDOUT", "STDERR", "SYSTEM", "LOG"):
        with _recent_console_lock:
            for line in str(text).splitlines() or [""]:
                _recent_console.append({"ts": time.time() * 1000, "source": source,
                                        "line": line[:4000], **describe_log(line[:4000], source)})
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
        startup_warning = None
        try:
            directory = _saved_directory()
            _check_directory(directory)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            directory = LOG_DIR
            startup_warning = f"설정된 로그 폴더를 사용할 수 없어 기본 폴더에 저장합니다: {exc}"
        _sink = LineLog(directory)
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
        if startup_warning:
            print(f"[log] {startup_warning}")
