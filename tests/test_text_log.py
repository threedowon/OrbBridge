import io
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

from server import text_log


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
