"""Run Voice Register — assign 8 note samples per voice key."""

import argparse

from mapping_store import ensure_register_data
from voice_register_core import VoiceRegisterState
from voice_store import NOTE_SLOTS, ensure_voice_catalog_file, list_catalog_voices, load_mapping, load_voice_catalog, normalize_profile


def print_voice_list() -> None:
    mapping = load_mapping()
    catalog = load_voice_catalog()
    voices = list_catalog_voices(mapping)
    if not voices:
        print("등록된 voice가 없습니다.")
        return

    print(f"voice {len(voices)}개\n")
    print(f"{'voice':<14} {'orbs':>4}  {'samples':>8}  notes")
    print(f"{'-' * 14} {'-' * 4}  {'-' * 8}  {'-' * 24}")
    for voice in voices:
        profile = normalize_profile(catalog.get("voices", {}).get(voice) or {})
        notes = profile.get("notes", {})
        filled = sum(1 for k, v in notes.items() if v)
        orbs = sum(1 for info in mapping.values() if info.get("voice") == voice)
        filled_names = [label for key, label in NOTE_SLOTS if notes.get(key)]
        note_summary = ", ".join(filled_names) if filled_names else "-"
        print(f"{voice:<14} {orbs:>4}  {filled:>2}/8     {note_summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice Register — voice별 8음 샘플 등록")
    parser.add_argument(
        "--list",
        action="store_true",
        help="voice 샘플 목록만 터미널에 출력하고 종료",
    )
    args = parser.parse_args()

    ensure_register_data()
    ensure_voice_catalog_file()

    if args.list:
        print_voice_list()
        return

    print("[voice-register] voice_catalog.json + sounds/ 폴더 사용")
    print("[voice-register] 키: voice (예: ive_1, bts_2)")

    from voice_register_gui import run_gui

    state = VoiceRegisterState()
    run_gui(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
