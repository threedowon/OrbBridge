# LED matrix status display

Based on dotmatrix_revision at 8ff41708825a2e6df7207767d90960206e33faa0.

Update the Python server and flash the matching matrix sketch together:

- firmware/dotmatrix_3/dotmatrix_3.ino retains the existing 16 px running
  padding and group - member formatting.
- firmware/dotmatrix/dotmatrix.ino retains its original running layout.
- dotmatrix_1 and dotmatrix_2 remain historical sketches and do not support
  this status protocol.

Both updated sketches retain the built-in font, use 2-bit depth, and draw only
white pixels on black. Status text is uppercase and centered horizontally and
vertically. Each complete frame is drawn offscreen and swapped once. Repeated
commands do not redraw the panel. No animation, fades, strobing, color screens,
or button-controlled test-screen cycle remain in the updated sketches.

| Condition | Display |
| --- | --- |
| READY | PLACE TRAY |
| Accepted Listen, including the initial automatic Listen | HINT during playback |
| Accepted Play | LISTENING during playback |
| Accepted DFesta | COMPLETE during playback |
| RUNNING without playback | Existing latest orb name and live countdown |
| Idle or time-over exit | TIME OVER for 2 s, RESETTING for 2 s, then CLEAR TABLE |
| Manual reset/end with occupied table | RESETTING for 2 s, then CLEAR TABLE |
| Completed game | COMPLETE through final DFesta and 2 s afterward, RESETTING for 2 s, then CLEAR TABLE |
| Table cleared / READY reached | PLACE TRAY immediately, interrupting any reset message |
| No valid host command for 5 s | OFFLINE until the next valid command |

Rejected requests do not change the display. A new game clears the remembered
orb name. While playing, newly placed names are remembered for the return to
the running display. An absent/invalid countdown produces PLACE ORB rather than
leaving a stale playback message. Unknown states clear the display.

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
