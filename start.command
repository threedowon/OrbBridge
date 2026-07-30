#!/bin/bash
# Voice Orb — Mac 개발 실행 (Python 3 필요)
cd "$(dirname "$0")"
python3 -m pip install -q websockets pyserial 2>/dev/null
cd server
exec python3 launcher.py
