"""Run Melody Editor — BPM · note · duration for the active melody."""

from app_paths import ensure_data_files

from melody_editor_core import active_melody_id, load_active_melody, timing_summary


def main() -> None:
    ensure_data_files()
    file_id, data = load_active_melody()
    print(f"[melody-editor] active: {file_id}.json")
    print(f"[melody-editor] {timing_summary(data)}")

    from melody_editor_gui import run_gui

    run_gui()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
