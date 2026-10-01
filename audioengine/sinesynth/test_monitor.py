import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
import numpy as np
from monitor_core import MonitorEngine, mapped_pitch, match_port, clean_settings, PITCHES
from sinesynth import App


class MonitorTests(unittest.TestCase):
    def engine(self, count=1, velocity=127):
        engine = MonitorEngine()
        for i in range(count):
            engine.send('on', ('group', i, 36), 60, velocity)
        engine.render(4800)
        return engine

    def test_eight_selector_keys_are_diatonic(self):
        self.assertEqual(tuple(mapped_pitch(n) for n in range(36, 44)), PITCHES)
        self.assertEqual(mapped_pitch(43, 1), 84)
        self.assertIsNone(mapped_pitch(44))

    def test_port_matching_is_not_substring(self):
        self.assertTrue(match_port('BTS 1', 'bts'))
        self.assertTrue(match_port('listen', 'listen'))
        self.assertFalse(match_port('my_bts_backup', 'bts'))

    def test_legacy_pitch_settings_reset_to_safe_mapping(self):
        settings = clean_settings({'master_pitch': 31, 'volume': .6986})
        self.assertEqual(settings['octave'], 0)
        self.assertEqual(settings['volume_db'], -18)
        self.assertEqual(clean_settings({'version': 2, 'volume_db': float('nan')})['volume_db'], -18)

    def test_velocity_remains_linear(self):
        quiet = self.engine(velocity=40).render(4800)
        loud = self.engine(velocity=100).render(4800)
        np.testing.assert_allclose(quiet, loud * .4, atol=1e-7)

    def test_64_member_unison_does_not_multiply_volume(self):
        solo = self.engine().render(4800)
        choir = self.engine(count=64).render(4800)
        np.testing.assert_allclose(solo, choir, atol=1e-7)

    def test_max_volume_chord_never_hard_clips(self):
        engine = MonitorEngine()
        engine.volume_db = 0
        engine.tone = 'Soft'
        for i in range(128):
            engine.send('on', ('group', i, 36 + i % 8), PITCHES[i % 8], 127)
        signal = engine.render(48000)
        self.assertTrue(np.all(np.isfinite(signal)))
        self.assertLessEqual(np.max(np.abs(signal)), .851)

    def test_sample_envelope_starts_and_stops_smoothly(self):
        engine = MonitorEngine()
        engine.send('on', ('g', 1, 36), 60, 127)
        attack = engine.render(960)
        self.assertLess(np.max(np.abs(attack[:24])), .01)
        engine.send('off', ('g', 1, 36))
        release = engine.render(2400)
        self.assertEqual(len(engine.voices), 0)
        self.assertEqual(float(release[-1]), 0)
        self.assertLess(np.max(np.abs(np.diff(release))), .02)

    def test_long_note_is_not_cut_after_four_seconds(self):
        engine = self.engine()
        engine.render(48000 * 5)
        self.assertEqual(len(engine.voices), 1)
        self.assertFalse(next(iter(engine.voices.values()))['release'])

    def test_channel_off_is_scoped_and_panic_releases_all(self):
        engine = self.engine()
        engine.send('on', ('other', 0, 36), 60, 100)
        engine.render(512)
        engine.send('channel_off', ('group', 0))
        engine.render(2400)
        self.assertEqual(set(engine.voices), {('other', 0, 36)})
        engine.send('panic')
        engine.render(2400)
        self.assertFalse(engine.voices)

    def test_note_on_zero_releases_and_mute_fades(self):
        engine = self.engine()
        engine.muted = True
        self.assertLess(np.max(np.abs(engine.render(10000)[-100:])), 1e-6)
        engine.send('on', ('group', 0, 36), 60, 0)
        engine.render(2400)
        self.assertFalse(engine.voices)

    def test_command_overflow_cannot_leave_stuck_notes(self):
        engine = self.engine()
        for _ in range(4200):
            engine.send('on', ('g', 1, 36), 60, 127)
        self.assertGreater(engine.overflows, 0)
        engine.render(2400)
        self.assertFalse(engine.voices)

    def test_midi_callback_reaches_audio_without_gui_poll(self):
        app = SimpleNamespace(closed=False, received=0, routing={}, octave=0,
                              engine=MonitorEngine(), _event=lambda *args: None)
        App._receive(app, 'bts', SimpleNamespace(type='note_on', channel=2, note=38, velocity=80))
        app.engine.render(512)
        self.assertEqual(app.engine.voices[('bts', 3, 38)]['pitch'], 64)
        App._receive(app, 'bts', SimpleNamespace(type='control_change', channel=2, control=123))
        app.engine.render(2400)
        self.assertFalse(app.engine.voices)

    def test_audio_restart_discards_stale_notes(self):
        engine = self.engine()
        engine.send('on', ('stale', 1, 36), 60, 127)
        engine.reset()
        self.assertFalse(np.any(engine.render(512)))

    def test_first_device_choice_prefers_wasapi_without_system_changes(self):
        backend = SimpleNamespace(
            query_hostapis=lambda: [{'name': 'MME', 'default_output_device': 0},
                                    {'name': 'Windows WASAPI', 'default_output_device': 1}],
            query_devices=lambda: [{'name': 'Speaker', 'max_output_channels': 2, 'hostapi': 0},
                                   {'name': 'Speaker', 'max_output_channels': 2, 'hostapi': 1}])
        app = SimpleNamespace(device_var=SimpleNamespace(get=lambda: ''), device_box=Mock(), audio_status=Mock())
        with patch('sinesynth.sd', backend):
            App._devices(app)
        app.device_box.current.assert_called_once_with(2)
        self.assertEqual(app.output_devices[2], 1)

    def test_local_retrigger_ignores_older_note_off(self):
        engine = self.engine()
        key = ('group', 0, 36)
        old, current = object(), object()
        app = SimpleNamespace(engine=engine, test_tokens={key: current})
        App._test_off(app, key, old)
        engine.render(2400)
        self.assertIn(key, engine.voices)
        App._test_off(app, key, current)
        engine.render(2400)
        self.assertFalse(engine.voices)


if __name__ == '__main__':
    unittest.main()
