from server.inout.hint_led import HintLedBridge


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
    assert sent == ["H0,0,1", "H1,1,460"]
    bridge.handle(dict(type="end", run_id=2))
    assert sent[-1] == "H0,0,0"


def test_all_96_slot_addresses():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start())
    for step in range(12):
        for pitch in range(8):
            bridge.handle(beat(step, pitch))
            assert sent[-1] == f"H{step // 2 + 1},{step % 2 * 8 + pitch + 1},460"
    assert len(set(sent)) == 97


def test_rest_delay_and_cancel_restore():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10.1)
    bridge.handle(start())
    bridge.handle(beat())
    assert sent[-1] in ("H1,1,360", "H1,1,359")
    bridge.handle(beat(pitch=None))
    assert sent[-1] == "H0,0,1"
    bridge.handle(beat(emitted=9))
    assert sent[-1] == "H0,0,1"
    bridge.handle(dict(type="end", run_id=1, cancelled=True))
    assert bridge.run_id is None


def test_disabled_playback_cancels_hint_and_ignores_old_events():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start())
    bridge.handle(beat())
    bridge.handle(start(run=2, hint=False))
    assert sent[-1] == "H0,0,0"
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
    assert sent == ["H0,0,1", "H1,1,460", "H0,0,1", "H0,0,1", "H2,1,460"]
    bridge.handle(dict(type="end", run_id=2))
    assert sent[-1] == "H0,0,0"


def test_play_cursor_includes_silent_beats_and_all_sounding_pitches():
    sent = []
    bridge = HintLedBridge(sent.append, lambda: 10)
    bridge.handle(start(mode="play", hint=False))
    for step in range(12):
        event = beat(step=step, pitch=None)
        event['mode'] = 'play'
        bridge.handle(event)
        assert sent[-1] == f"P{step // 2 + 1},{step % 2 + 1},0,460"
    event['notes'] = [{'pitch': 0}, {'pitch': 7}, {'pitch': 0}]
    bridge.handle(event)
    assert sent[-1] == 'P6,2,129,460'
    bridge.handle(dict(type='end', run_id=1))
    assert sent[-1] == 'H0,0,0'


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
    assert sent[-1] == 'H1,1,460'
