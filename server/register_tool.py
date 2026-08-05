"""DS2431 시리얼 감지와 데스크톱 오브 등록 도구를 실행한다."""

import argparse
import json

from mapping_store import BACKEND_DIR, MAPPING_PATH, ensure_register_data, load_mapping
from register_core import RegisterState
from register_gui import run_gui

SERVER_DIR = BACKEND_DIR


def print_orb_list() -> None:
    mapping = load_mapping()
    if not mapping:
        print("등록된 오브가 없습니다.")
        return

    rows = sorted(
        ((info.get("voice", ""), oid) for oid, info in mapping.items()),
        key=lambda r: (r[0], r[1]),
    )
    voice_w = max(len("voice"), *(len(v) for v, _ in rows))
    print(f"등록된 오브 {len(rows)}개\n")
    print(f"{'voice':<{voice_w}}  ID")
    print(f"{'-' * voice_w}  {'-' * 32}")
    for voice, orb_id in rows:
        print(f"{voice:<{voice_w}}  {orb_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DS2431 오브 등록 도구")
    parser.add_argument(
        "--list",
        action="store_true",
        help="등록된 오브 목록만 터미널에 출력하고 종료",
    )
    args = parser.parse_args()

    if args.list:
        ensure_register_data()
        print_orb_list()
        return

    ensure_register_data()

    with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
        config = json.load(f)

    serial_cfg = config.get("serial", {})
    port = serial_cfg.get("port", "COM13")
    baudrate = int(serial_cfg.get("baudrate", 115200))

    print(f"[register] mapping file: {MAPPING_PATH}")
    print(f"[register] serial port: {port} @ {baudrate}")
    print('[register] ESP JSON: {"type":"ds2431","id":"2D:E8:B1:9F:24:00:00:AC"}')

    state = RegisterState()
    run_gui(state, port, baudrate)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
