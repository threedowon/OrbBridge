"""Lost DFESTA return writes must not strand the lights or interrupt a new run."""
import pytest

from server.inout.hint_led import HintLedBridge
from test_main_runtime_integration import tray_main


WRONG = {'state': 'RUNNING', 'is_correct': False}
RETURN = 'H0,0,5,600'


def make_bridge(results=None, log=None):
    now, sent, logs = [10.0], [], []
    outcomes = iter(results) if results is not None else None

    def send(command):
        sent.append((now[0], command))
        return next(outcomes) if outcomes is not None and command == RETURN else True

    bridge = HintLedBridge(send, lambda: now[0], log=log or logs.append)
    bridge.handle({'type': 'start', 'run_id': 1, 'mode': 'dfesta'})
    return bridge, now, sent, logs


def advance(bridge, now, seconds=0.31, playback=None, inspection=False):
    now[0] += seconds
    bridge.sync_ready(WRONG, playback or {}, inspection)


def returns(sent):
    return [stamp for stamp, command in sent if command == RETURN]


@pytest.mark.parametrize('finished', [False, True])
@pytest.mark.parametrize('results', [
    [True] * 4,  # USB accepts all four even if the first three are lost on RS485.
    [False, False, True, True],
    [False] * 4,
    [True, True, True, False],
])
def test_bounded_repeats_after_both_cancel_and_natural_end(finished, results):
    bridge, now, sent, logs = make_bridge(results)
    if finished:
        bridge.handle({'type': 'end', 'run_id': 1, 'cancelled': False})
        bridge.observe_snapshot({'is_correct': True}, WRONG, 'remove_orb')
    else:
        bridge.cancel_dfesta(1)
    assert len(returns(sent)) == 1
    for _ in range(20):
        bridge.observe_snapshot(WRONG, WRONG, 'tick')
        advance(bridge, now, 0.1)
    stamps = returns(sent)
    assert len(stamps) == 4
    assert all(b - a >= 0.3 - 1e-9 for a, b in zip(stamps, stamps[1:]))
    assert not bridge.dfesta_held
    assert len(logs) == (2 if False in results else 1)
    assert sum('[ERROR]' in line for line in logs) == (1 if False in results else 0)
    assert 'attempts=4/4' in logs[-1]
    assert f'usb_ok={sum(results)}' in logs[-1]
    assert '슬레이브 수신·적용 미확인' in logs[-1]
    assert '복귀 완료' not in ''.join(logs)


def test_old_active_snapshot_does_not_cancel_retries_or_restart_budget():
    bridge, now, sent, _ = make_bridge()
    bridge.cancel_dfesta(1)
    for _ in range(4):
        bridge.cancel_dfesta(1)
        advance(bridge, now, playback={'active': True, 'run_id': 1, 'mode': 'dfesta'})
    assert len(returns(sent)) == 4


@pytest.mark.parametrize('mode,hint', [('play', False), ('listen', True), ('listen', False), ('dfesta', False)])
def test_new_playback_cancels_retries_and_old_callbacks(mode, hint):
    bridge, now, sent, _ = make_bridge()
    bridge.cancel_dfesta(1)
    bridge.prepare_playback(2)
    # The new start may wait for MIDI; old callbacks must not re-arm retries.
    bridge.cancel_dfesta(1)
    bridge.handle({'type': 'start', 'run_id': 1, 'mode': 'dfesta'})
    advance(bridge, now)
    bridge.handle({'type': 'start', 'run_id': 2, 'mode': mode, 'hint_led': hint})
    bridge.handle({'type': 'beat', 'run_id': 2, 'step': 0, 'duration_ms': 400,
                   'answer_pitch': 4, 'notes': [{'pitch': 4}]})
    advance(bridge, now)
    bridge.handle({'type': 'end', 'run_id': 2, 'cancelled': False})
    advance(bridge, now)
    assert len(returns(sent)) == 1
    assert bridge.latest_run == 2


def test_start_event_alone_and_newer_playback_snapshot_both_cancel_retry():
    for direct_start in (False, True):
        bridge, now, sent, _ = make_bridge()
        bridge.cancel_dfesta(1)
        if direct_start:
            bridge.handle({'type': 'start', 'run_id': 2, 'mode': 'play'})
        advance(bridge, now, playback={'active': True, 'run_id': 2})
        advance(bridge, now)
        assert len(returns(sent)) == 1


def test_late_wrong_snapshot_cannot_release_previous_run_after_new_start_reserved():
    bridge, now, sent, _ = make_bridge()
    bridge.prepare_playback(2)
    bridge.observe_snapshot({'is_correct': True}, WRONG, 'remove_orb')
    advance(bridge, now)
    assert returns(sent) == []


def test_failed_new_start_still_allows_old_playback_to_clean_up():
    bridge, _, sent, _ = make_bridge()
    bridge.prepare_playback(2)
    # If MIDI preparation fails before the new start is delivered, the old
    # run's cancelled end still has to clear the lights it currently owns.
    bridge.handle({'type': 'end', 'run_id': 1, 'cancelled': True})
    assert sent[-1][1] == 'H0,0,0,600'
    assert not bridge.dfesta_held and bridge.run_id is None


@pytest.mark.parametrize('action', ['reset', 'tray_removed', 'new_tray', 'inspection', 'manual_effect', 'offline'])
def test_new_owner_cancels_release_even_when_first_write_failed(action):
    bridge, now, sent, logs = make_bridge([False])
    bridge.cancel_dfesta(1)
    if action == 'reset':
        bridge.observe_snapshot(WRONG, {'state': 'RESETTING'}, 'reset')
        assert sent[-1][1] == 'H0,0,4,600'
    elif action == 'tray_removed':
        bridge.observe_snapshot(WRONG, {'state': 'READY', 'cleanup_end_reason': 'tray_removed'}, 'remove_orb')
        assert sent[-1][1] == 'H0,0,4,600'
    elif action == 'new_tray':
        bridge.tray_transition('READY', 'RUNNING', {'initial_hint': {'pending': True, 'remaining_s': 3}})
        assert sent[-1][1] == 'T2000'
    elif action == 'inspection':
        advance(bridge, now, inspection=True)
    else:
        bridge.cancel_release(action)
    for _ in range(5):
        advance(bridge, now)
    assert len(returns(sent)) == 1
    assert len(logs) == 2
    assert 'attempts=1/4' in logs[-1]


def test_delayed_poll_does_not_burst_or_keep_retrying_after_deadline():
    bridge, now, sent, logs = make_bridge()
    bridge.cancel_dfesta(1)
    advance(bridge, now, 1.0)
    for _ in range(20):
        bridge.sync_ready(WRONG, {})
    assert len(returns(sent)) == 2
    advance(bridge, now, 1.1)
    advance(bridge, now)
    assert len(returns(sent)) == 2
    assert '재전송 기한 만료' in logs[-1]


def test_correct_condition_returning_without_playback_keeps_retries_and_breathing():
    bridge, now, sent, _ = make_bridge()
    bridge.cancel_dfesta(1)
    ready = {'state': 'RUNNING', 'is_correct': True,
             'completion': {'waiting_for_dfesta': True},
             'cells': {'1:1': {'slave': 1, 'cell': 1, 'result': 'correct'}}}
    for _ in range(3):
        bridge.observe_snapshot(WRONG, ready, 'add_orb')
        now[0] += 0.31
        bridge.sync_ready(ready, {})
    assert len(returns(sent)) == 4
    assert any(command == 'B100000000000' for _, command in sent)


def test_failed_release_is_saved_and_counted_once_as_important_issue(monkeypatch, tmp_path):
    from server import text_log, important_issues
    issues = important_issues.ImportantIssues()
    monkeypatch.setattr(important_issues, 'important_issues', issues)
    monkeypatch.setattr(text_log, '_sink', text_log.LineLog(tmp_path))
    bridge, now, _, _ = make_bridge([False, False, True, True], log=text_log.write_log)
    bridge.cancel_dfesta(1)
    for _ in range(3):
        advance(bridge, now)
    text = next(tmp_path.glob('*.txt')).read_text(encoding='utf-8')
    assert text.count('[LED] [ERROR]') == 1
    assert 'usb_ok=2' in text and 'usb_failed=2' in text
    assert issues.snapshot()[0]['count'] == 1


@pytest.mark.parametrize('finished', [False, True])
def test_server_maintenance_retries_without_browser_or_matrix(tray_main, monkeypatch, finished):
    main, _, _ = tray_main
    main.set_tray({'present': True})
    runtime = main.get_game_runtime()
    runtime.add_orb(orb_id='a', slave=1, cell=1)
    bridge, now, sent, _ = make_bridge([False, True, True, True])
    monkeypatch.setattr(main, '_hint_led', bridge)
    monkeypatch.setattr(main, '_matrix_display', None)
    if finished:
        bridge.handle({'type': 'end', 'run_id': 1, 'cancelled': False})
    runtime.remove_orb(slave=1, cell=1)
    assert returns(sent) == [10.0]
    for _ in range(3):
        now[0] += 0.31
        main._update_matrix_display()
    assert len(returns(sent)) == 4
    assert runtime.snapshot()['state'] == 'RUNNING'


def test_scheduler_reserves_new_run_before_slow_midi_gate(tray_main, monkeypatch):
    from types import SimpleNamespace
    main, _, _ = tray_main
    bridge, now, sent, _ = make_bridge()
    monkeypatch.setattr(main, '_hint_led', bridge)
    bridge.cancel_dfesta(1)

    def flush():
        bridge.cancel_dfesta(1)
        advance(bridge, now)
        assert len(returns(sent)) == 1

    monkeypatch.setattr(main, '_game_events', SimpleNamespace(playback_started=lambda run: None, flush=flush))
    main._scheduler_before_start({'run_id': 2, 'mode': 'play'})


@pytest.mark.parametrize('action', ['reset', 'inspection', 'blackout', 'effect', 'disconnect', 'stop_serial'])
def test_server_controls_cancel_remaining_retries(tray_main, monkeypatch, action):
    from server.inout import midi_out
    main, _, _ = tray_main
    main.set_tray({'present': True})
    bridge, now, sent, _ = make_bridge()
    monkeypatch.setattr(main, '_hint_led', bridge)
    monkeypatch.setattr(main.hardware_io, 'send_line', lambda command: True)
    monkeypatch.setattr(main.hardware_io, 'stop_serial', lambda: None)
    monkeypatch.setattr(midi_out, 'shutdown', lambda: None)
    bridge.cancel_dfesta(1)
    if action == 'reset':
        main.reset_game()
    elif action == 'inspection':
        main.orb_action({'action': 'start'})
    elif action == 'blackout':
        main.blackout_leds()
    elif action == 'effect':
        main.send_led_effect(3)
    elif action == 'disconnect':
        main.disconnect_hardware()
    else:
        main.stop_serial_input()
    for _ in range(3):
        now[0] += 0.31
        main._update_matrix_display()
    assert len(returns(sent)) == 1

