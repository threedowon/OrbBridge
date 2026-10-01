"""OrbBridge local MIDI sound monitor. Run this file or !run_sinesynth.bat."""
import json
import math
import queue
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

try:
    import mido
except ImportError:
    mido = None
try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from .monitor_core import MonitorEngine, KEYS, NAMES, mapped_pitch, match_port, clean_settings
except ImportError:
    from monitor_core import MonitorEngine, KEYS, NAMES, mapped_pitch, match_port, clean_settings

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / 'sinesynth_settings.json'
BG, PANEL, INK, MUTED, ACCENT = '#14171b', '#20252b', '#e9edf1', '#9aa5b1', '#75d4be'


def default_mapping():
    for parent in HERE.parents:
        path = parent / 'data' / 'portmapping.json'
        if path.is_file():
            return path
    return HERE.parent.parent / 'data' / 'portmapping.json'


class App(tk.Tk):
    def __init__(self, mapping_path):
        super().__init__()
        self.title('OrbBridge · Sound Monitor')
        self.geometry('1080x730')
        self.minsize(920, 650)
        self.configure(bg=BG)
        self.engine = MonitorEngine()
        self.events = queue.Queue(maxsize=2000)
        self.ports = []
        self.stream = None
        self.output_devices = [None]
        self.octave = 0
        self.closed = False
        self.received = 0
        self.dropped_logs = 0
        self.underruns = 0
        self.test_tokens = {}
        self.routing = {}
        self.mapping_path = mapping_path
        self.settings = clean_settings({})
        self.settings_warning = ''
        try:
            if SETTINGS.exists():
                raw = json.loads(SETTINGS.read_text(encoding='utf-8'))
                self.settings = clean_settings(raw)
                if raw.get('version') != 2:
                    self.settings_warning = '기존 피치 설정은 새 8음 매핑으로 초기화했다.'
        except (OSError, ValueError, AttributeError) as exc:
            self.settings_warning = f'설정 대신 기본값 적용: {exc}'
        self._build()
        self._devices()
        self._start_audio()
        self._connect()
        if self.settings_warning:
            self._event('설정', '', '', '', '', self.settings_warning)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.bind('<Escape>', lambda _: self._panic())
        self.after(50, self._poll)

    def _build(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=PANEL, foreground=INK, font=('맑은 고딕', 10))
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG)
        style.configure('TButton', padding=(10, 7), background=PANEL)
        style.map('TButton', background=[('active', '#35424a')])
        style.configure('TCheckbutton', background=BG)
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=27, borderwidth=0)
        style.configure('Treeview.Heading', background='#2c333b', foreground=MUTED, padding=6)
        style.map('Treeview', background=[('selected', '#365851')])
        style.configure('TCombobox', fieldbackground=PANEL, foreground=INK, arrowcolor=INK)
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', INK)], selectbackground=[('readonly', PANEL)], selectforeground=[('readonly', INK)])
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(12, 8))
        style.map('TNotebook.Tab', background=[('selected', '#35424a')], foreground=[('selected', INK)])
        style.configure('Horizontal.TScale', troughcolor=PANEL, background=ACCENT, borderwidth=0)
        style.configure('Horizontal.TProgressbar', troughcolor=PANEL, background=ACCENT, borderwidth=0)
        self.option_add('*TCombobox*Listbox.background', PANEL)
        self.option_add('*TCombobox*Listbox.foreground', INK)

        top = ttk.Frame(self, padding=(20, 16))
        top.pack(fill='x')
        ttk.Label(top, text='SOUND MONITOR', font=('Segoe UI', 18, 'bold')).pack(side='left')
        ttk.Label(top, text='loopMIDI → 8음 모니터', foreground=MUTED).pack(side='left', padx=20)
        ttk.Button(top, text='전체 음 끄기  Esc', command=self._panic).pack(side='right')

        audio = ttk.Frame(self, padding=(20, 0, 20, 12))
        audio.pack(fill='x')
        ttk.Label(audio, text='출력 장치').grid(row=0, column=0, sticky='w')
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(audio, textvariable=self.device_var, state='readonly', width=40)
        self.device_box.grid(row=1, column=0, sticky='ew', pady=6)
        self.device_box.bind('<<ComboboxSelected>>', lambda _: self._start_audio())
        ttk.Button(audio, text='장치 새로고침', command=self._devices).grid(row=1, column=1, padx=8)
        ttk.Button(audio, text='출력 다시 연결', command=self._start_audio).grid(row=1, column=2)
        audio.columnconfigure(0, weight=1)
        self.audio_status = tk.StringVar(value='오디오 준비 중')
        ttk.Label(audio, textvariable=self.audio_status, foreground=MUTED).grid(row=2, column=0, columnspan=3, sticky='w')

        controls = ttk.Frame(self, padding=(20, 0, 20, 15))
        controls.pack(fill='x')
        self.volume = tk.DoubleVar(value=self.settings['volume_db'])
        self.volume_label = tk.StringVar()
        ttk.Label(controls, textvariable=self.volume_label, width=17).pack(side='left')
        ttk.Scale(controls, from_=-48, to=0, variable=self.volume, command=self._volume).pack(side='left', fill='x', expand=True, padx=(0, 14))
        self.mute = tk.BooleanVar(value=self.settings['muted'])
        ttk.Checkbutton(controls, text='음소거', variable=self.mute, command=self._volume).pack(side='left')
        self.tone = tk.StringVar(value=self.settings['tone'])
        tone = ttk.Combobox(controls, textvariable=self.tone, values=('Sine', 'Soft'), state='readonly', width=7)
        tone.pack(side='left', padx=12)
        tone.bind('<<ComboboxSelected>>', lambda _: self._tone())
        self.octave_var = tk.StringVar(value=str(self.settings['octave']))
        octave = ttk.Combobox(controls, textvariable=self.octave_var, values=('-2', '-1', '0', '1', '2'), state='readonly', width=3)
        ttk.Label(controls, text='옥타브').pack(side='left')
        octave.pack(side='left', padx=(6, 0))
        octave.bind('<<ComboboxSelected>>', lambda _: self._octave())
        self._volume()
        self._tone()
        self.octave = self.settings['octave']

        keys = ttk.Frame(self, padding=(20, 0, 20, 14))
        keys.pack(fill='x')
        ttk.Label(keys, text='로컬 소리 테스트', font=('맑은 고딕', 11, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        ttk.Button(keys, text='3화음 듣기', command=lambda: self._test((36, 38, 40))).grid(row=0, column=6, columnspan=2, sticky='e', pady=(0, 8))
        self.key_buttons = {}
        for i, (key, name) in enumerate(zip(KEYS, NAMES)):
            button = tk.Button(keys, text=f'{name}\nMIDI {key}', command=lambda k=key: self._test((k,)),
                               bg=PANEL, fg=INK, activebackground=ACCENT, relief='flat',
                               font=('Segoe UI', 12, 'bold'), height=3, cursor='hand2')
            button.grid(row=1, column=i, sticky='ew', padx=(0, 5))
            keys.columnconfigure(i, weight=1)
            self.key_buttons[key] = button

        meter = ttk.Frame(self, padding=(20, 0, 20, 12))
        meter.pack(fill='x')
        self.stats = tk.StringVar(value='수신 0 · 활성음 0')
        ttk.Label(meter, textvariable=self.stats).pack(side='left')
        self.peak_label = tk.StringVar(value='출력 −∞ dBFS')
        ttk.Label(meter, textvariable=self.peak_label, width=20, anchor='e').pack(side='right')
        self.meter = ttk.Progressbar(meter, maximum=60, length=140)
        self.meter.pack(side='right', padx=8)

        self.footer = tk.StringVar(value='로컬 버튼은 MIDI를 송신하지 않는다. 실제 음성 샘플을 재현하는 도구는 아니다.')
        ttk.Label(self, textvariable=self.footer, foreground=MUTED, wraplength=1000).pack(side='bottom', fill='x', padx=20, pady=(0, 14))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True, padx=20, pady=(0, 12))
        log_tab, port_tab = ttk.Frame(tabs, padding=8), ttk.Frame(tabs, padding=8)
        tabs.add(log_tab, text='  MIDI 수신  ')
        tabs.add(port_tab, text='  포트 연결  ')
        toolbar = ttk.Frame(log_tab)
        toolbar.pack(fill='x', pady=(0, 8))
        ttk.Label(toolbar, text='최근 300건 · 표시 지연은 소리 재생에 영향 없음', foreground=MUTED).pack(side='left')
        ttk.Button(toolbar, text='로그 지우기', command=self._clear).pack(side='right')
        self.log = self._table(log_tab, [('time', '시간', 82), ('group', '그룹 / 포트', 110),
                    ('channel', 'CH', 40), ('member', '멤버', 100), ('note', '음 / Velocity', 125), ('event', '이벤트', 300)])
        ttk.Button(port_tab, text='MIDI 포트 다시 연결', command=self._connect).pack(anchor='e', pady=(0, 8))
        self.port_table = self._table(port_tab, [('group', '그룹', 120), ('port', '입력 포트', 250), ('state', '상태', 350)])

    def _table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[x[0] for x in columns], show='headings', height=8)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=35, stretch=True)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        return tree

    def _devices(self):
        previous = self.device_var.get()
        names, self.output_devices = ['시스템 기본 출력'], [None]
        preferred = None
        try:
            if sd:
                apis = sd.query_hostapis()
                preferred = next((a['default_output_device'] for a in apis
                                  if 'WASAPI' in a['name'] and a['default_output_device'] >= 0), None)
                for i, device in enumerate(sd.query_devices()):
                    if device['max_output_channels'] > 0:
                        names.append(f"{device['name']} · {apis[device['hostapi']]['name']}")
                        self.output_devices.append(i)
        except Exception as exc:
            self.audio_status.set(f'출력 장치 조회 실패: {exc}')
        self.device_box.configure(values=names)
        selected = names.index(previous) if previous in names else (
            self.output_devices.index(preferred) if preferred in self.output_devices else 0)
        self.device_box.current(selected)
        # Device inventory alone does not interrupt a running stream.

    def _start_audio(self):
        self._stop_audio()
        self.engine.reset()
        if sd is None:
            self.audio_status.set('오디오 없음 · pip install -r requirements.txt 필요')
            return
        try:
            index = max(0, self.device_box.current())
            device_id = self.output_devices[index]
            device = sd.query_devices(device_id, 'output')
            rate = int(device['default_samplerate'])
            self.engine.sample_rate = rate
            self.stream = sd.OutputStream(device=device_id, samplerate=rate, blocksize=256,
                                          channels=min(2, device['max_output_channels']), dtype='float32',
                                          latency='low', callback=self._audio)
            self.stream.start()
            self.audio_status.set(f"{device['name']} · {rate} Hz · 출력 지연 {self.stream.latency * 1000:.1f} ms")
        except Exception as exc:
            if self.stream:
                self.stream.close()
                self.stream = None
            self.audio_status.set(f'오디오 연결 실패: {exc}')

    def _audio(self, outdata, frames, timing, status):
        if status:
            self.underruns += 1
        outdata[:] = self.engine.render(frames)[:, None]

    def _stop_audio(self):
        stream, self.stream = self.stream, None
        if stream:
            try:
                stream.stop()
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

    def _disconnect_midi(self):
        for port in self.ports:
            try:
                port.close()
            except Exception as exc:
                self._event('MIDI', '', '', '', '', f'포트 닫기 오류: {exc}')
        self.ports.clear()

    def _volume(self, *_):
        self.engine.volume_db = float(self.volume.get())
        self.engine.muted = self.mute.get()
        self.volume_label.set(f'음량 {self.engine.volume_db:.0f} dB')

    def _tone(self):
        self.engine.tone = self.tone.get()

    def _octave(self):
        self.engine.send('panic')
        self.octave = int(self.octave_var.get())

    def _panic(self):
        self.engine.send('panic')

    def _test(self, keys):
        if self.stream is None:
            self.footer.set('소리 테스트 전에 출력 장치를 연결해야 한다.')
            return
        for key in keys:
            identity = ('local', 1, key)
            self.engine.send('on', identity, mapped_pitch(key, self.octave), 100)
            self._event('로컬', 1, '테스트', NAMES[key - 36], 100, 'ON · 450 ms')
            token = object()
            self.test_tokens[identity] = token
            self.after(450, lambda k=identity, t=token: self._test_off(k, t))

    def _test_off(self, key, token):
        if self.test_tokens.get(key) is token:
            self.test_tokens.pop(key)
            self.engine.send('off', key)

    def _event(self, group, channel, member, note, velocity, event):
        stamp = time.strftime('%H:%M:%S')
        try:
            self.events.put_nowait((stamp, group, channel, member,
                                   f'{note} / {velocity}' if note else '', event))
        except queue.Full:
            self.dropped_logs += 1

    def _receive(self, group, message):
        # Called by the MIDI backend, never by Tk's polling timer.
        if self.closed:
            return
        self.received += 1
        channel = getattr(message, 'channel', 0) + 1
        entry = self.routing.get(group, {})
        member = next((name for name, ch in entry.get('members', {}).items() if ch == channel), f'ch{channel}')
        if message.type in ('note_on', 'note_off'):
            key = message.note
            pitch = mapped_pitch(key, self.octave)
            if pitch is None:
                self._event(group, channel, member, str(key), message.velocity, '범위 밖 · 재생 제외')
                return
            on = message.type == 'note_on' and message.velocity > 0
            self.engine.send('on' if on else 'off', (group, channel, key), pitch, message.velocity)
            self._event(group, channel, member, NAMES[key - 36], message.velocity, 'ON' if on else 'OFF')
        elif message.type == 'control_change' and message.control in (120, 123):
            self.engine.send('channel_off', (group, channel))
            self._event(group, channel, member, '', '', '채널 전체 음 끄기')

    def _connect(self):
        self._disconnect_midi()
        self._panic()
        self.port_table.delete(*self.port_table.get_children())
        try:
            raw = json.loads(self.mapping_path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or any(not isinstance(v, dict) for v in raw.values()):
                raise ValueError('포트 매핑은 그룹별 객체여야 한다')
            self.routing = raw
            available = mido.get_input_names() if mido else []
        except Exception as exc:
            self.footer.set(f'MIDI 연결 실패: {exc}')
            return
        used = set()
        for group, entry in self.routing.items():
            configured = str(entry.get('port', group))
            matches = [name for name in available if match_port(name, configured)]
            actual = next((name for name in matches if name.lower() == configured.lower()), matches[0] if matches else None)
            state = '포트 없음 · loopMIDI 확인' if mido else 'mido / python-rtmidi 설치 필요'
            if actual and actual not in used:
                try:
                    self.ports.append(mido.open_input(actual, callback=lambda msg, g=group: self._receive(g, msg)))
                    used.add(actual)
                    state = '연결됨'
                except Exception as exc:
                    state = f'열기 실패: {exc}'
            elif actual:
                state = '중복 포트 설정 · 제외'
            self.port_table.insert('', 'end', values=(group, actual or configured, state))
        self.footer.set(f'MIDI {len(self.ports)}/{len(self.routing)} 포트 연결 · 로컬 버튼은 MIDI를 송신하지 않는다.')

    def _clear(self):
        children = self.log.get_children()
        if children:
            self.log.delete(*children)

    def _poll(self):
        if self.closed:
            return
        for _ in range(80):
            try:
                row = self.events.get_nowait()
            except queue.Empty:
                break
            self.log.insert('', 'end', values=row)
        children = self.log.get_children()
        if len(children) > 300:
            self.log.delete(*children[:-300])
        if children:
            self.log.see(children[-1])
        meter = self.engine.meter
        db = 20 * math.log10(max(1e-9, meter['peak']))
        self.meter['value'] = max(0, min(60, db + 60))
        self.peak_label.set(f'출력 {db:.1f} dBFS' if db > -80 else '출력 −∞ dBFS')
        self.stats.set(f"수신 {self.received} · 활성음 {meter['voices']} · 출력 경고 {self.underruns} · 누락 로그 {self.dropped_logs}")
        for key, button in self.key_buttons.items():
            button.configure(bg=ACCENT if key in meter['keys'] else PANEL, fg=BG if key in meter['keys'] else INK)
        self.after(50, self._poll)

    def _close(self):
        self.closed = True
        self._disconnect_midi()
        self._stop_audio()
        try:
            settings = clean_settings({'version': 2, 'volume_db': self.engine.volume_db,
                        'muted': self.engine.muted, 'octave': self.octave, 'tone': self.engine.tone})
            if SETTINGS.exists():
                raw = json.loads(SETTINGS.read_text(encoding='utf-8'))
                backup = SETTINGS.with_suffix('.legacy.json')
                if raw.get('version') != 2 and not backup.exists():
                    backup.write_bytes(SETTINGS.read_bytes())
            temp = SETTINGS.with_suffix('.tmp')
            temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temp.replace(SETTINGS)
        except (OSError, ValueError, AttributeError) as exc:
            print(f'설정 저장 실패: {exc}')
        self.destroy()


def main():
    App(Path(sys.argv[1]) if len(sys.argv) > 1 else default_mapping()).mainloop()


if __name__ == '__main__':
    main()
