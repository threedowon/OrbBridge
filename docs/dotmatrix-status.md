# LED matrix status display

The tray cleanup flow builds on dotmatrix_revision at 887e5bf.

For the tray update, install the Python server and flash firmware/master/master.ino
together. An already updated dotmatrix_3 needs no further flash; its UP-button
diagnostics and LED bleeding settings are unchanged.

Update the Python server and flash the matching matrix sketch together:

- firmware/dotmatrix_3/dotmatrix_3.ino retains the existing 16 px running
  padding and group - member formatting.
- firmware/dotmatrix/dotmatrix.ino retains its original running layout.
- dotmatrix_1 and dotmatrix_2 remain historical sketches and do not support
  this status protocol.

Both updated sketches retain the built-in font, use 2-bit depth, and draw only
white pixels on black. Status text is uppercase and centered horizontally and
vertically. Each complete frame is drawn offscreen and swapped once. Repeated
commands do not redraw the panel. Game screens have no animation, fades,
strobing, or color effects.

On dotmatrix_3, the MatrixPortal UP button (pin 6) cycles through:
game → panel/bit-depth/refresh info → solid white → red → green → blue →
test group/name with a 123-second timer → game.
Each debounced press advances once; holding the button does not repeat.
Diagnostics stay selected while game updates and heartbeats are received in
the background. Returning to game displays the latest state immediately,
or OFFLINE if the connection has timed out. Offline detection does not
interrupt a diagnostic screen.

| Condition | Display |
| --- | --- |
| Initial READY, or farewell finished | PLACE TRAY |
| Accepted Listen, including the initial automatic Listen | HINT during playback |
| Accepted Play | PLAYING during playback |
| Accepted DFesta | COMPLETE during playback |
| RUNNING without playback | Existing latest orb name and live countdown |
| Idle or time-over exit | TIME OVER for 2 s, then PLEASE CLEAR TABLE |
| Manual reset/end | PLEASE CLEAR TABLE immediately |
| Completed game | COMPLETE through final DFesta and 2 s afterward, then PLEASE CLEAR TABLE |
| All orbs AND tray removed / READY reached | GOODBYE for n seconds, blank for n seconds, then PLACE TRAY |
| Next tray placed during GOODBYE or blank | Start immediately; initial Listen shows HINT, then the usual running screen |
| No valid host command for 5 s | OFFLINE until the next valid command |

Rejected requests do not change the display. A new game clears the remembered
orb name. While playing, newly placed names are remembered for the return to
the running display. An absent/invalid countdown produces PLACE ORB rather than
leaving a stale playback message. Unknown states clear the display.

There is no RESETTING text or its former two-second display delay. The internal
RESETTING game state still waits for cleanup. The orb-only table_is_clear
condition is unchanged; cleanup_is_clear additionally requires a confirmed absent
tray. Unknown or stale sensor state never counts as removal.

Set matrix_display.farewell_duration_s in data/config.json (default 2) to choose
n for BOTH GOODBYE and the following blank period. Restart the server to apply
the setting. Zero skips both periods. Cleanup time comes from the game engine,
so repeated polls/heartbeats do not extend it and brief state transitions are
not lost between matrix polls. A new RUNNING state always takes precedence.

## Tray input

Master uses the existing active-low hall sensors on GPIO 5 and 6. It debounces
the pair for 50 ms, sends the first stable reading after boot, reports changes
immediately after stabilization, and repeats the current stable state every
1000 ms. During continuous contact chatter it does not renew an old state.
Every report is one newline-terminated JSON object at the existing 115200 baud:

```json
{"type":"tray","present":false,"seated":false}
{"type":"tray","present":true,"seated":false}
{"type":"tray","present":true,"seated":true}
```

These mean both sensors clear, one sensor detecting, and both sensors detecting,
respectively. The master no longer also emits game/start for the same hall event.
The server stores present and seated through its existing runtime queue.
Hardware reports expire after 3 s; missing input becomes unknown, not absent.
Periodic reports restore state after reconnect without needing a serial query.

Only a known seated=false → seated=true transition starts a game, while READY
and with no orbs. Identical reports never restart playback or refresh Idle.
The first occupied report after boot/reconnect does not auto-start; lift and
replace the tray. A placement during cleanup is not queued for later: remove
everything first, then place the next tray. Lifting the tray during RUNNING does
not itself end the game.

Both /admin and /admin/software replace the momentary start button with a TRAY
ON/OFF toggle. POST /api/game/tray with {"present":true} or {"present":false}
uses the same state/start/cleanup path. In table mode or USB-direct mode this
simulates full placement/removal. With Master selected, the button shows actual
sensor state and simulation is disabled to prevent competing sources.
The legacy /api/game/start remains available for explicit administrative/debug
control; normal hall and UI starts use the tray path.

The main loop reads current runtime and scheduler snapshots every 50 ms.
It does not use the asynchronous WebSocket playback event queue. Cancelled
scheduler runs are excluded immediately, even while a MIDI worker is returning.
Matrix serial I/O never runs on the orb-placement/MIDI or game-runtime threads.

The host retries disconnected ports once per second, waits 1.6 s without
blocking for board boot, and sends only the latest state. A one-second heartbeat
restores a silently rebooted board without redrawing an unchanged healthy panel.
Partial writes and close failures are handled. Firmware discards malformed,
oversized, and incomplete serial frames and bounds each serial-read loop.

Protocol (one JSON object per newline):

```json
{"type":"status","text":"PLACE TRAY"}
{"type":"display","text":"IVE-LEESEO","seconds":120}
{"type":"clear"}
```

Double buffering follows the library's
[official example](https://github.com/mrcodetastic/ESP32-HUB75-MatrixPanel-DMA/blob/master/examples/3_DoubleBuffer/3_DoubleBuffer.ino).
Physical refresh quality, USB recovery, and final placement must still be checked
on the installed ESP32/panels. The host tests use simulated serial devices.

Run Python regressions with python -m pytest -q and the two admin toggle checks
with node tests/test_tray_ui.cjs. Physical hall sensing and the ESP32 firmware
build still require validation on the installed hardware/toolchain.
