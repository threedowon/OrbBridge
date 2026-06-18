"""Preview audio/video samples from Voice Register."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_ffplay_proc: subprocess.Popen | None = None


def stop_preview() -> None:
    global _ffplay_proc
    with _lock:
        if _ffplay_proc and _ffplay_proc.poll() is None:
            try:
                _ffplay_proc.terminate()
            except Exception:
                pass
        _ffplay_proc = None


def _play_wav_windows(path: Path) -> str | None:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return None
    except Exception as exc:
        return str(exc)


def play_preview(path: Path | str) -> str | None:
    """Play a local media file. Returns an error message, or None on success."""
    global _ffplay_proc
    path = Path(path)
    if not path.is_file():
        return "파일을 찾을 수 없습니다."

    stop_preview()
    ext = path.suffix.lower()

    if ext == ".wav" and sys.platform == "win32":
        err = _play_wav_windows(path)
        if err is None:
            return None

    ffplay = shutil.which("ffplay")
    if ffplay and ext in (
        ".wav",
        ".ogg",
        ".mp3",
        ".flac",
        ".mp4",
        ".m4a",
        ".aac",
        ".mov",
        ".webm",
        ".mkv",
    ):
        _ffplay_proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return None

    if sys.platform == "win32":
        os.startfile(os.fspath(path))
        return None

    if ffplay:
        _ffplay_proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return None

    return "재생하려면 Windows 기본 앱 또는 ffplay(ffmpeg)가 필요합니다."
