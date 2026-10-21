"""Run the actual slave LED state machine with an in-memory NeoPixel strip."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_slave_hint_to_ready_transition(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++") or shutil.which("cl")
    if not compiler:
        pytest.skip("A native C++ compiler is required for the firmware LED test")
    firmware = (ROOT / "firmware/slave/slave.ino").read_text(encoding="utf-8")
    # Keep the production globals and complete LED functions; only replace the
    # Arduino hardware boundary. No copy of the transition logic lives here.
    globals_ = firmware[:firmware.index("void updateServerLease() {")]
    for header in ("OneWire.h", "Adafruit_NeoPixel.h", "HardwareSerial.h"):
        globals_ = globals_.replace(f"#include <{header}>", "")
    reset = firmware[firmware.index("void updateServerLease() {"):
                     firmware.index("// Debug PC renews U1")]
    lighting = firmware[firmware.index("void setRing("):
                        firmware.index("bool sameAddr(")]
    harness = (ROOT / "tests/slave_hint_restore_test.cpp").read_text(encoding="utf-8")
    code = harness.replace("// INSERT_SLAVE_LED_FIRMWARE", globals_ + reset + lighting)
    source = tmp_path / "slave_led_test.cpp"
    source.write_text(code, encoding="utf-8")
    executable = tmp_path / "slave_led_test.exe"
    include = str(ROOT / "firmware/slave")
    if Path(compiler).stem.lower() == "cl":
        command = [compiler, "/nologo", "/EHsc", "/std:c++14", "/utf-8",
                   f"/I{include}", str(source), f"/Fe:{executable}"]
    else:
        command = [compiler, "-std=c++14", "-I", include, str(source), "-o", str(executable)]
    built = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(executable)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
