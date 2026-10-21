from server.inout.hint_led import HintLedBridge
import pytest


def test_ready_breathing_only_correct_slots_and_playback_priority():
    sent = []
    now = [10.0]
    def send(command):
        sent.append(command)
        return True
    bridge = HintLedBridge(send, lambda: now[0])
    state = {'completion': {'waiting_for_dfesta': True}, 'cells': {
        '1:5': {'slave':1, 'cell':5, 'result':'correct'},
        '6:16': {'slave':6, 'cell':16, 'result':'correct'},
        '2:1': {'slave':2, 'cell':1, 'result':'wrong'}}}
    bridge.sync_ready(state, {})
    assert sent == ['B500000000008']
    bridge.sync_ready(state, {})
    assert len(sent) == 1
    now[0] += 0.5
    bridge.sync_ready(state, {})
    assert len(sent) == 2
    bridge.sync_ready(state, {'active': True})
    assert sent[-1] == 'B000000000000'
    bridge.sync_ready(state, {})
    assert sent[-1] == 'B500000000008'
    state['completion']['waiting_for_dfesta'] = False
    bridge.sync_ready(state, {})
    assert sent[-1] == 'B000000000000'


def test_breathing_failed_send_retries_and_inspection_disables():
    sent = []
    bridge = HintLedBridge(lambda command: sent.append(command) or False)
    bridge.sync_ready({}, {})
    bridge.sync_ready({}, {})
    assert sent == ['B000000000000'] * 2
    state = {'completion': {'waiting_for_dfesta': True}, 'cells': {'1:1': {'slave':1,'cell':1,'result':'correct'}}}
    bridge.sync_ready(state, {}, inspection=True)
    assert sent[-1] == 'B000000000000'


def test_dfesta_fills_only_below_answer_not_harmony_or_occupied_notes():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode='dfesta', hint=False))
    for step in range(12):
        for pitch in range(8):
            event = beat(step=step, pitch=7)
            event.update(mode='dfesta', answer_pitch=pitch)
            bridge.handle(event)
            assert sent[-1] == f'Q{step // 2 + 1},{step % 2 + 1},{(1 << (pitch + 1)) - 1},460'
    event.update(step=4, answer_pitch=4, duration_ms=4000)
    bridge.handle(event)
    assert sent[-1] == 'Q3,1,31,4000'
    event.update(answer_pitch=None)
    bridge.handle(event)
    assert sent[-1] == 'Q3,1,0,4000'
    bridge.handle(dict(type='end', run_id=1, cancelled=True))
    assert sent[-1] == 'H0,0,0,600'


@pytest.mark.parametrize('delay,expected', [(3, 'T2000'), (2, 'T1000'), (1.5, 'T500'), (1, None), (0, None)])
def test_tray_light_finishes_one_second_before_hint(delay, expected):
    sent = []
    bridge = HintLedBridge(sent.append)
    snapshot = {'initial_hint': {'pending': True, 'remaining_s': delay}}
    bridge.tray_transition('READY', 'RUNNING', snapshot)
    bridge.tray_transition('RUNNING', 'RUNNING', snapshot)
    assert sent == ([expected] if expected else [])
    bridge.tray_transition('RUNNING', 'RESETTING', {})
    if expected:
        assert sent[-1] == 'H0,0,0,600'


def test_initial_hint_replaces_tray_light_without_later_cancelling_it():
    sent = []
    bridge = HintLedBridge(sent.append)
    bridge.tray_transition('READY', 'RUNNING', {'initial_hint': {'pending': True, 'remaining_s': 3}})
    bridge.handle(start())
    assert sent == ['T2000', 'H0,0,1,600']
    assert not bridge.tray_active


@pytest.mark.parametrize('fade', [0, 300, 600, 1200, 3000])
def test_configured_fade_is_carried_on_notes_and_controls(fade):
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10, fade_ms=fade)
    bridge.handle(start())
    bridge.handle(beat())
    bridge.handle(dict(type='end', run_id=1))
    assert sent == [f'H0,0,1,{fade}', f'H1,1,460,{fade}', f'H0,0,3,{fade}']


@pytest.mark.parametrize('fade', [-1, 3001, True, '600', 0.6])
def test_invalid_fade_rejected(fade):
    with pytest.raises(ValueError, match='hint_led_fade_ms'):
        HintLedBridge(lambda _: None, fade_ms=fade)


def start(run=1, hint=True, mode="listen"):
    return dict(type="start", run_id=run, mode=mode, hint_led=hint)


def beat(step=0, pitch=0, run=1, emitted=10, duration=460):
    return dict(type="beat", run_id=run, mode="listen", step=step,
                notes=[] if pitch is None else [dict(pitch=pitch)],
                duration_ms=duration, emitted_at=emitted)


def test_only_enabled_hint_lights_rings():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(hint=False))
    bridge.handle(beat())
    assert sent == []
    bridge.handle(start(run=2))
    bridge.handle(beat(run=2))
    assert sent == ["H0,0,1,600", "H1,1,460,600"]
    bridge.handle(dict(type="end", run_id=2))
    assert sent[-1] == "H0,0,3,600"


def test_all_96_slot_addresses():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start())
    for step in range(12):
        for pitch in range(8):
            bridge.handle(beat(step, pitch))
            assert sent[-1] == f"H{step // 2 + 1},{step % 2 * 8 + pitch + 1},460,600"
    assert len(set(sent)) == 97


def test_rest_delay_and_cancel_restore():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10.1)
    bridge.handle(start())
    bridge.handle(beat())
    assert sent[-1] in ("H1,1,360,600", "H1,1,359,600")
    count = len(sent)
    bridge.handle(beat(pitch=None))
    assert len(sent) == count  # Rests leave the previous note fading independently.
    bridge.handle(beat(emitted=9))
    assert len(sent) == count
    bridge.handle(dict(type="end", run_id=1, cancelled=True))
    assert sent[-1] == "H0,0,0,600"
    assert bridge.run_id is None


def test_disabled_playback_cancels_hint_and_ignores_old_events():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start())
    bridge.handle(beat())
    bridge.handle(start(run=2, hint=False))
    assert sent[-1] == "H0,0,0,600"
    count = len(sent)
    bridge.handle(beat(run=1))
    bridge.handle(dict(type="end", run_id=1))
    bridge.handle(beat(run=2))
    assert len(sent) == count


def test_hint_stays_exclusive_through_rests_and_replacement():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start())
    bridge.handle(beat())
    bridge.handle(beat(pitch=None))
    bridge.handle(start(run=2))
    bridge.handle(dict(type="end", run_id=1))
    bridge.handle(beat(step=2, run=2))
    assert sent == ["H0,0,1,600", "H1,1,460,600", "H0,0,1,600", "H2,1,460,600"]
    bridge.handle(dict(type="end", run_id=2))
    assert sent[-1] == "H0,0,3,600"


def test_play_cursor_includes_silent_beats_and_all_sounding_pitches():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode="play", hint=False))
    assert sent == []  # Do not blank occupied rings when PLAY begins.
    for step in range(12):
        event = beat(step=step, pitch=None)
        event['mode'] = 'play'
        bridge.handle(event)
        assert sent[-1] == f"P{step // 2 + 1},{step % 2 + 1},0,460"
    event['notes'] = [{'pitch': 0}, {'pitch': 7}, {'pitch': 0}]
    bridge.handle(event)
    assert sent[-1] == 'P6,2,129,460'
    bridge.handle(dict(type='end', run_id=1))
    assert sent[-1] == 'H0,0,0,600'


def test_play_replaced_by_hint_ignores_stale_beats_and_end():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode='play'))
    bridge.handle(start(run=2))
    before = list(sent)
    bridge.handle(beat(run=1))
    bridge.handle(dict(type='end', run_id=1))
    assert sent == before
    bridge.handle(beat(run=2))
    assert sent[-1] == 'H1,1,460,600'

def test_dfesta_natural_end_holds_until_tray_and_orbs_are_both_removed():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode='dfesta', hint=False))
    event = beat(step=0, pitch=4)
    event['answer_pitch'] = 4
    bridge.handle(event)
    bridge.handle(dict(type='end', run_id=1, cancelled=False))
    assert sent[-1] == 'Q1,1,31,460'
    bridge.sync_ready({'completion': {'waiting_for_dfesta': True}, 'cells': {
        '1:5': {'slave': 1, 'cell': 5, 'result': 'correct'}}}, {})
    assert sent[-1] == 'B000000000000'
    bridge.observe_snapshot({'state': 'RUNNING', 'tray': {'present': True}},
                            {'state': 'RUNNING', 'is_correct': True, 'tray': {'present': False}}, 'tray')
    assert sent[-1] == 'B000000000000'
    bridge.observe_snapshot({'state': 'RUNNING'},
                            {'state': 'READY', 'cleanup_end_reason': 'tray_removed'}, 'remove_orb')
    assert sent[-1] == 'H0,0,4,600'
    count = len(sent)
    bridge.handle(event)
    assert len(sent) == count


@pytest.mark.parametrize('finished', [False, True])
@pytest.mark.parametrize('action', ['remove_orb', 'add_orb'])
def test_broken_solution_fades_dfesta_and_ignores_remaining_cues(finished, action):
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode='dfesta', hint=False))
    cue = dict(beat(step=0, pitch=4), answer_pitch=4)
    bridge.handle(cue)
    if finished:
        bridge.handle(dict(type='end', run_id=1, cancelled=False))
    previous = {'state': 'RUNNING', 'is_correct': True}
    current = {'state': 'RUNNING', 'is_correct': False}
    bridge.observe_snapshot(previous, current, action)
    assert sent[-1] == 'H0,0,5,600'
    count = len(sent)
    bridge.observe_snapshot(current, current, 'tick')
    bridge.handle(cue)
    bridge.handle(dict(type='end', run_id=1, cancelled=False))
    assert len(sent) == count
    assert not bridge.dfesta_held
    # Normal PLAY and a newly completed DFESTA can start after the fade request.
    bridge.handle(start(run=2, mode='play'))
    bridge.handle(beat(run=2))
    assert sent[-1].startswith('P')
    bridge.handle(start(run=3, mode='dfesta'))
    assert sent[-1] == 'H0,0,1,600'
    bridge.handle(dict(cue, run_id=3))
    assert sent[-1].startswith('Q')


def test_reset_clears_dfesta_during_playback_and_ignores_end():
    sent = []
    bridge = HintLedBridge(sent.append)
    bridge.handle(start(mode='dfesta', hint=False))
    bridge.observe_snapshot({}, {}, 'reset')
    assert sent[-1] == 'H0,0,4,600'
    bridge.handle(dict(type='end', run_id=1, cancelled=True))
    assert sent[-1] == 'H0,0,4,600'


def test_natural_completion_state_does_not_clear_dfesta():
    sent = []
    bridge = HintLedBridge(sent.append)
    bridge.handle(start(mode='dfesta', hint=False))
    bridge.observe_snapshot({'tray': {'present': True}},
                            {'tray': {'present': True}, 'state': 'RESETTING'}, 'dfesta')
    assert sent == ['H0,0,1,600']

@pytest.mark.parametrize('mode', ['play', 'listen', 'dfesta'])
def test_game_tray_removal_blacks_out_and_ignores_cancel(mode):
    sent = []
    bridge = HintLedBridge(sent.append)
    bridge.handle(start(mode=mode))
    bridge.observe_snapshot({'state': 'RUNNING', 'tray': {'present': True}},
        {'state': 'RESETTING', 'end_reason': 'tray_removed', 'tray': {'present': False}}, 'tray')
    assert sent[-1] == 'H0,0,4,600'
    count = len(sent)
    bridge.handle(dict(type='end', run_id=1, cancelled=True))
    assert len(sent) == count

@pytest.mark.parametrize('easing,command', [('none', 'T2000'), ('easein', 'T2000,1'), ('easeout', 'T2000,2')])
def test_tray_easing_config(easing, command):
    sent = []
    bridge = HintLedBridge(sent.append, tray_easing=easing)
    bridge.tray_transition('READY', 'RUNNING', {'initial_hint': {'pending': True, 'remaining_s': 3}})
    assert sent == [command]


def test_invalid_tray_easing_rejected():
    with pytest.raises(ValueError, match='tray_led_easing'):
        HintLedBridge(lambda _: None, tray_easing='typo')
