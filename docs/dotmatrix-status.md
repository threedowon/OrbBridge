# LED matrix status display

Game-event MIDI, configurable status text, and initial loading are documented in
[game-event-midi.md](game-event-midi.md). Runtime phrases now come from
`matrix_display.texts` in `data/config.json`.

The DFESTA button prompt uses `{"type":"status","text":"DFESTA!","arrow":"right"}`.
In `dotmatrix_3` and `dotmatrix_4`, the text itself is centered across the full
panel, independently of the guide on its right. The one-pixel white guide runs
horizontally, then diagonally down-right, with no arrowhead. On the 192x32 panel
it starts at (128, 15), runs horizontally to (173, 15), then diagonally to
(187, 29), matching the reference's long line and extended downward bend.
This leaves four blank columns on the right and two rows below the tip; the
guide's geometry is independent of font size. `dotmatrix_4` applies its configured
text offsets to both the text and guide. The guide replaces the countdown only
on this prompt; changing the configured phrase does not move it.
The protocol still uses `"arrow":"right"`. Flash the matrix sketch in use to
apply the centered prompt and longer guide. Older matrix firmware ignores the
extra field and continues to display centered text.

The former `COMPLETE` screen now uses the current `data/melodies/*.json` **title**
alone. The server does not prepend `artist` or derive a name from `songId` or the
filename. After an accepted DFESTA press, the title stays visible after playback
finishes while the placement remains correct. Breaking the solution restores the
normal running screen; solving it again shows the button prompt until DFESTA is
pressed again. Time-over, idle, and manual game exits still take precedence. The
semantic screen remains `complete`; a missing/blank title falls back to
`matrix_display.texts.complete`. This display timing change only requires a server
restart, not another firmware upload.

Title messages use `{"type":"status","text":"BTS - DYNAMITE","scroll":true}`.
Updated `dotmatrix_3` and `dotmatrix_4` show a single uppercase line across the full
panel width, with no countdown or arrow. Titles that fit stay centered; longer
titles scroll left at one pixel per 35 ms, pausing one second at each end before
repeating. At the current 192px width and default font, 32 characters fit. There is
no second line or automatic smaller font. Repeated host heartbeats do not restart
the scroll, and changing screens or losing the server connection stops it.

The built-in fonts support printable ASCII. The server flattens whitespace,
replaces unsupported characters with `?`, and caps titles at 128 characters to
fit the serial frame even when JSON escapes quotes/backslashes. Flash the matrix
sketch in use and restart the server for this feature. Subsequent title edits are
loaded on the next game start and do not require another firmware upload.

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
commands do not redraw the panel. Other than scrolling long song titles, game
screens have no animation, fades, strobing, or color effects.

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
| Initial tray start, before the automatic hint | LOADING... for game_rules.initial_hint_delay_s |
| Accepted Listen, including the initial automatic Listen | HINT during playback |
| Accepted Play | PLAYING during playback |
| Accepted DFesta | Melody JSON title during playback and afterward while still correct (COMPLETE fallback) |
| RUNNING without playback or a retained DFesta title | Existing latest orb name and live countdown |
| Correct complete placement after loading, before DFesta is pressed | PRESS DFESTA! |
| Idle or time-over exit | TIME OVER for 2 s, then PLEASE CLEAR TABLE |
| Manual reset/end | PLEASE CLEAR TABLE immediately |
| Completed game without a time limit | Melody title while still correct; PLEASE CLEAR TABLE once the solution breaks |
| All orbs AND tray removed / READY reached | GOODBYE for n seconds, blank for n seconds, then PLACE TRAY |
| Next tray placed during GOODBYE or blank | Start immediately; LOADING..., initial HINT, then the usual running screen |
| No valid host command for 5 s | OFFLINE until the next valid command |

Rejected requests do not change the display. A new game clears the remembered
orb name. While playing, newly placed names are remembered for the return to
the running display. An absent/invalid countdown produces PLACE ORB rather than
leaving a stale playback message. Unknown states clear the display.

There is no RESETTING text or its former two-second display delay. The internal
RESETTING game state still waits for cleanup. The orb-only table_is_clear
condition is unchanged; cleanup_is_clear additionally requires a confirmed absent
tray. Unknown or stale sensor state never counts as removal.

Set matrix_display.farewell_duration_s in data/config.json (default 4) to choose
n for BOTH GOODBYE and the following blank period. Restart the server to apply
the setting. Zero skips both periods. Cleanup time comes from the game engine,
so repeated polls/heartbeats do not extend it and brief state transitions are
not lost between matrix polls. A new RUNNING state always takes precedence.

If cleanup finishes during TIME OVER (including an already empty table), retain
TIME OVER for its two-second hold before starting the full GOODBYE/blank periods.
This display hold does not block a new tray from starting the next game.

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
The server filters physical readings before storing present and seated through
its existing runtime queue. Configure the stability windows in data/config.json:

```json
"tray_placement_delay_s": 0.5,
"tray_removal_delay_s": 1.5
```

Placement requires the configured one/two-sensor condition to remain stable for
0.5 s before LOADING starts. Both sensors must remain clear for 1.5 s before
removal is confirmed. A returning signal cancels pending removal, preserving the
current loading deadline, playback, and game. Repeated reports do not restart
these waits. Only confirmed changes produce tray MIDI events. The server polls
every 50 ms, so it does not wait for the next 1 s master heartbeat to confirm a
change. This filter is server-only; no new firmware upload is needed.

Both settings accept finite, non-negative seconds; zero disables that wait.
Missing settings use 0.5/1.5. Restart the server after editing them. Software tray
toggles remain immediate. Boot/reconnect readings establish a baseline rather
than triggering a new game, and source switches/ORB inspection clear pending
changes.

Hardware reports expire after 3 s; missing input becomes unknown, not absent.
Polling never renews that lease. Periodic reports restore state after reconnect
without needing a serial query.

Only a known seated=false → seated=true transition starts a game, while READY
and with no orbs. Identical reports never restart playback or refresh Idle.
Known tray state changes, including partial seating, refresh Idle during RUNNING.
Unknown/reconnect reports do not count as activity. An already reached Idle or
game deadline wins over a simultaneous tray change; the game deadline is unchanged.
The first occupied report after boot/reconnect does not auto-start; lift and
replace the tray. A placement during cleanup is not queued for later: remove
everything first, then place the next tray. Confirmed removal during RUNNING
ends the game only once all orbs are also removed.

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
