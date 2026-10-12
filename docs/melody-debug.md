# Melody discovery and debug audition

In sequence mode, the configured playlist defines the first songs and their order.
Other JSON filenames in `data/melodies` are appended in sorted order automatically.
The folder is checked at each game start. Adding a file does not rewind an existing
sequence. Fixed mode continues to use only the configured melody. Invalid melody
JSON still reports a validation error; it is not silently played or rewritten.

In `/admin/software`, the right panel has Game Status, MIDI Diagnostics, Time
Settings and Log tabs. Game controls remain visible above the tabs.

MIDI Diagnostics contains Debug Mode and a melody selector. Refresh List discovers
files added while the page was open. Enable Debug Mode to immediately set the
current game's DFesta completion requirement to 99 presses. This also applies to
subsequent debug games. Disabling it restores the current song's original requirement.
The accumulated press count is preserved on toggle; normal completion is evaluated
on the next valid DFesta press. Correct-placement conditions remain unchanged.

Select a melody and press Restart with Selected Song to stop playback and restart
the current game with that song, preserving the orb placement and resetting game
progress/press count. This one-off selection does not consume the ordinary rotation.
The selection is validated before interrupting playback. Debug actions do not edit
melody JSON or config; Debug Mode is off after server restart.

Existing MIDI routing, scheduling, harmony, velocity and hardware controls are unchanged.

## Initial Listen timer grace

Every game start (including debug restarts) holds TimeOver and Idle countdowns for
`initial Listen plan duration + 1 second`, measured from the game start. Duration
uses the selected melody BPM and all step lengths, including rests. The displayed
remaining values stay at their configured limits during this period. Activity
inside the grace period cannot start the Idle countdown early. Normal activity
afterwards resets Idle only. Later Listen requests retain their existing behavior;
they do not grant another intro grace period. Manual reset/end still work immediately.
