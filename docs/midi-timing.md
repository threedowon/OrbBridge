# MIDI onset timing investigation — 2026-09-28

## Report and history

Chrome, `input_mode=table`, loopMIDI and Ableton, with `/admin/software` and
`/admin` open in two tabs. Repeated root-melody recordings have different internal
note onsets; DFesta is also affected. The user reports that Ableton's MIDI indicator
itself arrives late. The screenshot has no readable time scale, so it does not
establish a numerical latency or by itself isolate a single cause.

The inspected `main` HEAD was `7836891b3495dc4013920a7abdbef73b5f1b4224`.
`audiorevision` at `5cc94115614b1577a761e2a17bf1db9eb17d885b` contained all of main,
plus the weighted-choir commit. That commit changed plan construction and song
data, not the scheduler execution loop or MIDI transport. Play has no choir-part
randomization. The small I AM duration correction is deterministic, not random jitter.

During this investigation `be833bce375855379dc9e65745dede19dc04618a` ("ableton edit")
was pushed to `audiorevision`. The timing fix is based on that newer commit and
preserves its Ableton projects, recordings and `input_mode=table` configuration.

Relevant commits already present on both branches:

- [`e86608e`](https://github.com/threedowon/OrbBridge/commit/e86608e06339e8f2e85ce83e79afcc78e326f197):
  “midi 씹히는거”; combined same-beat browser requests and MIDI sends into batches.
- [`645aac0`](https://github.com/threedowon/OrbBridge/commit/645aac0768349214ceafe07ac21fb099dc917cf0):
  Python scheduler with absolute deadlines; UI callback ran before each MIDI send.
- [`dd2d703`](https://github.com/threedowon/OrbBridge/commit/dd2d70369a9ba618c56a54a14df00a32526dfa4b):
  consolidated MIDI tooling, including status device enumeration and creation of
  a thread pool for every multiport batch. Catalog reads per note also remained.
- [`4fc908e`](https://github.com/threedowon/OrbBridge/commit/4fc908ee808c9864b8ba60f7023a6acd51157769):
  hid polling HTTP logs; it did not stop the every-second MIDI device scan.

The browser now requests a complete plan once; it does not time individual notes.
Changing browser cannot fix these Python scheduling paths. Both tabs are opened
by the normal launcher, so that is a supported operating setup.

## Confirmed defects and changes

1. On the diagnostic Windows 11 / CPython 3.12.14 runtime,
   `get_clock_info('monotonic')` reports **GetTickCount64, 15.625ms resolution**.
   `perf_counter` reports **QueryPerformanceCounter, 0.0001ms resolution**.
   Playback deadlines, note-off deadlines and MIDI timestamps now use
   `perf_counter`. The actual show machine's Python version must be read from
   its exported timing report; the diagnostic runtime is not proof of its version.
2. An Event wait may wake on coarse OS ticks. A long cancellable wait now ends
   20ms before the deadline, followed by sleeps of at most 2ms and repeated
   deadline/cancel checks. No busy spin or global timer-resolution setting is used.
   Python 3.11+ on Windows uses a high-resolution waitable timer for `sleep`.
3. Blocking either the start or beat display callback used to block MIDI itself.
   Display callbacks now run through a separate FIFO publisher; MIDI goes first.
   Absolute offsets and existing playback event payloads are preserved.
4. `runtime_status()` returns the last connection's device list without invoking
   WinMM enumeration on every poll. Listing/opening/closing ports is serialized.
   Failed initialization is also cached, so failed notes do not repeatedly rescan.
5. The member-label catalog is loaded at connection time and routes are cached.
   Sending a note no longer opens/parses `voice_catalog.json`.
6. Multiport workers are created and warmed at connection time and reused.
   Port sends remain parallel. Shutdown/reconnect waits for the in-flight batch
   before closing ports; note-off generation protection is retained.

Port names, channels, pitch keys 36..43, songs, harmonies, BPM and loopMIDI/Ableton
configuration are unchanged. After device/route/voice-label edits or failed
initialization, use **포트 재연결** while playback is stopped. Device status is
a connection snapshot, not an automatic hot-plug scan.

References: [Python time documentation](https://docs.python.org/3/library/time.html),
[Mido port thread safety](https://mido.readthedocs.io/en/latest/ports/),
[Mido RtMidi enumeration implementation](https://github.com/mido/mido/blob/main/mido/backends/rtmidi.py).

## Validation and limits

Five new regressions failed against the previous code: blocked start/beat display
callbacks, repeated status scans, per-note catalog reads, and new beat threads.
They pass after the change. Additional tests cover early wakeups, cancellation,
separate dispatch/driver timing, fresh-run diagnostic reset, failed connection
retry, and shutdown during a parallel batch. Full suite: **216 passed**.

A controlled Windows comparison used 12 dummy notes spaced 100ms apart and display
callbacks sleeping 0/40/90/15ms repeatedly. Note callbacks were timestamped with
`perf_counter`; no MIDI output was opened. A before/after run measured:

| Metric | Previous code | Patched code |
|---|---:|---:|
| Mean onset lateness | 35.548ms | 0.450ms |
| Maximum onset lateness | 94.278ms | 1.079ms |

This demonstrates isolation from the imposed display stalls, not a measured
improvement in the user's Ableton session and not an upper latency guarantee.
Read-only device enumeration on the diagnostic machine took 45.781ms initially
(including lazy imports) and 0.688–1.225ms on subsequent calls. Thus enumeration
alone has **not** been shown to explain the large reported drift.

Python threads can still be delayed by CPU contention, native code holding the
GIL, driver calls or OS scheduling. The every-3-second serial-port discovery on
`/admin` has not been established as a cause and is unchanged. The precise cause
of the recorded incident remains to be confirmed with live timing data.

## Recheck on the installation

1. Update `audiorevision`, restart the Python server, and refresh both browser tabs.
   For a packaged executable, rebuild it; updating source alone does not update it.
2. Keep the same table configuration and melody. Record Play several times, then
   DFesta several times, with the two original tabs open.
3. After each completed replay, use **MIDI 진단 → 타이밍 기록 저장** in
   `/admin/software`. The report holds the most recent run (up to 256 steps) and
   is replaced when the next replay begins. The display refreshes once per second;
   that display latency does not schedule MIDI.
4. Compare recording onsets with the per-step report. If drift persists, retain
   both artifacts from the same run. Closing the hardware tab for a comparison
   can help identify other background work, but is not a required operating workaround.

`/api/status` also exposes the report under `playback.timing`:

| Field | Meaning |
|---|---|
| `clock`, `clock_resolution_ms`, `python_version` | Verify runtime and high-resolution clock |
| `dispatch_late_ms` | Scheduler wakeup/send-callback start minus planned deadline |
| `send_ms` | Entire batch callback time, including preparation and driver calls |
| `note_on_late_ms` | Latest successful note-on **call start** minus deadline |
| `note_on_spread_ms` | First-to-last successful note-on call start in the batch |
| `driver_send_ms` | Longest successful individual `port.send()` call |
| `note_on_completed_late_ms` | Last successful send return minus deadline |
| `sent_count`, `error_count` | Timestamped successful sends and reported note failures |

Timestamps are measured around the Mido send call, **not at Ableton's input**.
Large dispatch lateness points to Python/OS wakeup contention. Small dispatch but
large send/preparation or driver time points to the MIDI output path. Small values
through send completion while Ableton remains late require loopMIDI/input-side
measurement. REST steps have no note-on metrics; failed sends must not be read as
zero-latency success. No per-beat file logging is added.
