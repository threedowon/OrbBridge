"""Run Melody Register — edit screen/melodies/*.json (BPM, note, duration)."""

import argparse

from melody_register_core import MelodyRegisterState, list_melody_ids, load_melody, melody_duration_sec


def print_melody_list() -> None:
    ids = list_melody_ids()
    if not ids:
        print("멜로디가 없습니다.")
        return
    print(f"멜로디 {len(ids)}개\n")
    print(f"{'id':<14} {'title':<18} {'bpm':>4}  {'steps':>5}  {'sec':>6}")
    print(f"{'-' * 14} {'-' * 18} {'-' * 4}  {'-' * 5}  {'-' * 6}")
    for file_id in ids:
        try:
            data = load_melody(file_id)
        except Exception as exc:
            print(f"{file_id:<14} (읽기 실패: {exc})")
            continue
        print(
            f"{file_id:<14} {data.get('title', ''):<18} "
            f"{int(data.get('bpm', 0)):>4}  {len(data.get('steps') or []):>5}  "
            f"{melody_duration_sec(data):>6.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Melody Register — 멜로디 JSON (BPM · note · duration) 편집"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="멜로디 목록만 터미널에 출력하고 종료",
    )
    args = parser.parse_args()

    if args.list:
        print_melody_list()
        return

    print("[melody-register] screen/melodies/*.json 편집")
    print("[melody-register] 저장 시 stage_data.json 동기화 옵션 사용 가능")

    from melody_register_gui import run_gui

    run_gui(MelodyRegisterState())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
