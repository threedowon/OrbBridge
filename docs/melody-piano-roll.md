# Melody Register piano roll

Run `py -m tools.melody_register` from the repository root, or use the existing
`tools/melody_register.bat`. The editor uses Tkinter; no new dependency is required.

## Editing

- The dark, compact editor has **12 sequential slots**, each containing a note or
  REST. New songs start with 12 one-beat rests. Only C4/D4/E4/F4/G4/A4/B4/C5 are
  available, matching the existing eight sample keys.
- Click within a slot to set its pitch. Drag vertically to change the pitch.
- Drag a note's right edge to change its length. Later slots move with it.
  The time axis is proportional to duration; **12 steps does not mean 12 beats**.
  This preserves the existing sequential JSON/game-column model. Independent
  overlapping melody notes and arbitrary start offsets are not introduced.
- Right-click or Delete turns the selected slot into REST; it does not delete the
  slot or its time. A rest clears that slot's harmony. Use Length to resize rests.
- Grid choices are 1/4, 1/8, 1/16 and 1/32. Triplet multiplies the normal duration
  by 2/3: an eighth-note triplet is 1/3 beat; a sixteenth-note triplet is 1/6 beat.
- Snap applies to the **length being dragged**, relative to the slot's start.
  It does not quantize the existing song when enabled. Snap off allows free length.
  Enter exact fractions such as `1/3` or `2/3` in Length and press Apply.
  The piano roll supports 1/96–16 beats per slot. Larger source values are rejected
  on opening rather than silently shortened. Runtime JSON support is unchanged.
- Drag the lower velocity bar to set **1–127**, or enter a value in the inspector
  and press Apply. Zero is not a silent note; use REST to mute a slot.
- Harmony A/B and lyrics remain editable in the inspector. Apply commits those
  fields too. Changing a melody pitch preserves its harmony for manual adjustment;
  clearing both harmony fields restores unison for that slot.
- BPM accepts integers 20–300. Enter or leaving the field applies it. Total beats
  and seconds update immediately. Zoom and Fit only change the view.
- Undo/redo buttons, or Ctrl+Z / Ctrl+Y while the grid is focused, undo whole mouse
  gestures. Ctrl+S saves the current song. Inspector entries require Apply first.
- Title, artist and DFesta completion count are in Song Settings. Existing metadata
  and choir weights are preserved. Game playback order is edited in its own dialog.
  Switching songs, creating another song or closing prompts before discarding edits.
- Older files with fewer than 12 steps are padded with REST **in memory** and marked
  unsaved. No file changes until Save. Files over 12 steps are not truncated.

## Velocity JSON and playback

```json
{
  "step": 1,
  "note": "C4",
  "duration": 0.3333333333333333,
  "velocity": 82,
  "harmony": ["E4", "G4"]
}
```

`steps[].velocity` is an integer from 1 to 127. Missing values default to **127**,
the previous MIDI output level. Existing shipped songs explicitly retain 127.
The editor normalizes, validates and saves the field. Invalid values fail validation
instead of silently clamping or overwriting a file.

Listen, Play and DFesta all use the current step's velocity. In DFesta it is copied
to **every singer**, including both harmony parts. Member routing and the weighted
part assignment are unchanged. The MIDI transport already sends velocity, so no
new ports, channels or CC mappings are needed. Audible dynamics on the installation
depend on the Ableton instrument's velocity response; actual sample output is not
measured by the unit tests.

## Reference-tone preview

On Windows the editor renders a full PCM WAV before playing it with the standard
Windows sound API. Pitch, BPM, complete slot lengths (including rests), and velocity
are reflected in the waveform. A short release/silent tail does not shorten the
next note's scheduled slot, correcting the old beep preview's 88% timing compression.
Sample boundaries are based on cumulative time to preserve triplet durations.

This is a melody reference tone, not Ableton's member samples or a choir mix. A
background renderer and a main-thread UI queue keep Tk updates out of worker
threads. Stop/new preview uses separate cancellation tokens, so a cancelled preview
cannot resume under a newer request. Previews are limited to 120 seconds. On other
platforms the editor reports that reference audio is unsupported instead of silently
pretending to play it.

Validation covers legacy defaults, strict velocity bounds, all three runtime modes,
shared choir velocity, JSON round-trip, fractional/triplet lengths, canvas editing,
REST/harmony preservation and preview PCM amplitude/duration. The Windows GUI was
visually inspected and real mouse drags verified triplet resizing and velocity.
