import io
import json
import pytest
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
from collections import deque

from server import text_log


@pytest.mark.parametrize("source,line,category,level", [
    ("SYSTEM", "logging started", "SERVER", ""),
    ("STDERR", "INFO:websockets.server:connection open", "ADMIN", ""),
    ("STDOUT", "[dotmatrix] connection failed (COM6)", "LED", "ERROR"),
    ("STDOUT", "[midi] WARN: Some ports missing", "MIDI", "WARN"),
    ("STDOUT", "[midi] skip 'listen': not found", "MIDI", "WARN"),
    ("STDOUT", "[led] [master] [송신] effect=1", "LED", ""),
    ("STDOUT", "[stage 1] 정답 시퀀스", "GAME", ""),
    ("STDOUT", "[WARN] 동기화 실패", "SERVER", "WARN"),
    ("SERIAL/json", '{"type":"heartbeat"}', "HARDWARE", ""),
    ("SLAVE", "통신 끊김", "HARDWARE", "ERROR"),
    ("EVENT", '{"type":"added"}', "GAME", ""),
])
def test_readable_log_categories_and_severity(source, line, category, level):
    row = text_log.describe_log(line, source)
    assert row["category"] == category
    assert row["level"] == level


def test_file_and_admin_share_readable_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(tmp_path))
    monkeypatch.setattr(text_log, "_recent_console", deque(maxlen=500))
    message = "[dotmatrix] connection failed (COM6): missing port"
    text_log.write_log(message, "STDOUT")
    row = text_log.recent_console()[-1]
    assert row["line"] == message
    assert row["display"] == "[LED] [ERROR] connection failed (COM6): missing port"
    saved = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert row["display"] in saved and "STDOUT" not in saved


def test_web_console_keeps_bounded_output_without_serial_duplicates(monkeypatch):
    monkeypatch.setattr(text_log, "_recent_console", deque(maxlen=500))
    monkeypatch.setattr(text_log, "_sink", None)
    for i in range(510):
        text_log.write_log(f"[led] [master] [송신] {i}", "STDOUT")
    text_log.write_log("heartbeat", "SERIAL/json")
    text_log.write_log("event", "EVENT")
    rows = text_log.recent_console()
    assert len(rows) == 500
    assert rows[0]["line"].endswith(" 10")
    assert rows[-1]["line"].endswith(" 509")
    rows[0]["line"] = "modified"
    assert text_log.recent_console()[0]["line"] != "modified"


def test_rotation_counts_physical_lines_and_preserves_sessions(tmp_path):
    sink = text_log.LineLog(tmp_path)
    sink.write("\n".join(f"로그 {i}" for i in range(2001)), "TEST")
    paths = sorted(tmp_path.glob("*.txt"))
    assert [len(p.read_text(encoding="utf-8").splitlines()) for p in paths] == [1000, 1000, 1]
    assert "로그 2000" in paths[-1].read_text(encoding="utf-8")
    text_log.LineLog(tmp_path).write("restart")
    assert len(list(tmp_path.glob("*.txt"))) == 4


def test_concurrent_writes_are_complete_and_bounded(tmp_path):
    sink = text_log.LineLog(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: sink.write(f"message {i}"), range(2100)))
    lines = [p.read_text(encoding="utf-8").splitlines() for p in tmp_path.glob("*.txt")]
    assert sorted(map(len, lines)) == [100, 1000, 1000]
    assert len({line.split("] ")[-1] for group in lines for line in group}) == 2100


def test_tee_preserves_console_and_flushes_partial_line(monkeypatch, tmp_path):
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(tmp_path))
    console = io.StringIO()
    tee = text_log.Tee(console, "STDOUT")
    tee.write("한글")
    tee.write(" 로그\n끝")
    tee.flush()
    assert console.getvalue() == "한글 로그\n끝"
    lines = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0].endswith("한글 로그") and lines[1].endswith("끝")


def test_directory_failure_can_recover(monkeypatch, tmp_path):
    directory = tmp_path / "log"
    directory.write_text("blocked")
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(directory))
    monkeypatch.setattr(text_log, "_fallback", io.StringIO())
    assert not text_log.write_log("first")
    directory.unlink()
    assert text_log.write_log("recovered")
    assert "recovered" in next(directory.glob("*.txt")).read_text(encoding="utf-8")


def test_install_captures_print_logging_and_unhandled_exception(tmp_path):
    code = '''
import sys, logging
from pathlib import Path
from server import text_log
text_log.LOG_DIR = Path(sys.argv[1])
text_log.SETTINGS_PATH = Path(sys.argv[1]) / "settings.json"
text_log.install_logging()
text_log.install_logging()
print("console output")
logging.warning("library warning")
raise RuntimeError("fatal error")
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    text = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    for value in ("console output", "library warning", "Traceback", "RuntimeError: fatal error"):
        assert value in text
    assert text.count("logging started") == 1
    assert "console output" in result.stdout and "fatal error" in result.stderr


def test_log_directory_switch_and_restart(monkeypatch, tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "새 로그"
    settings = tmp_path / "data" / "log_settings.json"
    monkeypatch.setattr(text_log, "SETTINGS_PATH", settings)
    monkeypatch.setattr(text_log, "_sink", text_log.LineLog(old))
    text_log.write_log("before switch")
    result = text_log.update_log_settings({"directory": str(new)})
    assert result["directory"] == str(new.resolve())
    text_log.write_log("after switch", "EVENT")
    old_text = next(old.glob("*.txt")).read_text(encoding="utf-8")
    assert "before switch" in old_text and "after switch" not in old_text
    assert "after switch" in next(new.glob("*.txt")).read_text(encoding="utf-8")
    code = '''
import sys
from pathlib import Path
from server import text_log
text_log.SETTINGS_PATH = Path(sys.argv[1])
text_log.install_logging()
print("persisted after restart")
'''
    subprocess.run([sys.executable, "-c", code, str(settings)], check=True, capture_output=True)
    assert any("persisted after restart" in p.read_text(encoding="utf-8") for p in new.glob("*.txt"))


def test_invalid_or_unwritable_directory_preserves_settings(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(text_log, "SETTINGS_PATH", settings)
    sink = text_log.LineLog(tmp_path / "old")
    monkeypatch.setattr(text_log, "_sink", sink)
    settings.write_text(json.dumps({"directory": str(sink.directory)}))
    original = settings.read_bytes()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    for value in ("", "relative/path", None, str(blocked)):
        with pytest.raises(ValueError):
            text_log.update_log_settings({"directory": value})
        assert settings.read_bytes() == original
        assert sink.directory == tmp_path / "old"
    def denied(directory):
        raise PermissionError("access denied")
    monkeypatch.setattr(text_log, "_check_directory", denied)
    with pytest.raises(ValueError):
        text_log.update_log_settings({"directory": str(tmp_path / "denied")})
    assert settings.read_bytes() == original
    assert sink.directory == tmp_path / "old"
