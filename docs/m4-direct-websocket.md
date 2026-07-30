# M4: Python Direct WebSocket

M4 removes the browser delivery dependency on the OSC loopback bridge. The
Python process now publishes browser events directly and sends a full snapshot
whenever a WebSocket client connects or reconnects.

## Runtime path

```text
Serial / HTTP
  → GameRuntime / Scheduler
  → Python WebSocket service
  → screen / table / admin
```

OSC output remains enabled for TouchDesigner and other external consumers:

```text
GameRuntime / Scheduler
  → OSC UDP
  → TouchDesigner / external tools
```

`server/main.py` and `server/launcher.py` no longer start
`bridge_service.py`. The bridge files remain as a legacy standalone path until
the later directory-cleanup milestone.

## Wire protocol

Live events preserve the existing browser contract:

```json
{
  "type": "event",
  "address": "/game/state",
  "args": ["RUNNING", "", 3, 119.4, 29.4]
}
```

Every new connection receives a snapshot before later live events:

```json
{
  "type": "snapshot",
  "snapshot": {
    "schema_version": 1,
    "game": {},
    "cells": {},
    "status": {},
    "events": []
  }
}
```

The snapshot is authoritative. It restores the current phase, placed orbs,
admin status, recent event log, and active playback identity after a network
drop or browser reload.

## Browser recovery

- `screen` silently rebuilds placed orbs so reconnecting does not replay tones.
- `table` restores the current phase and placed cells from the snapshot.
- `admin` restores status, cells, and recent events; its existing HTTP refresh
  remains for continuously decreasing timer displays.
- The old HTTP bootstrap remains as a short compatibility fallback when a
  separately launched legacy bridge does not provide M4 snapshots.

## Configuration

The WebSocket port remains `8765`, matching all three browser clients and the
legacy bridge contract. The listener host defaults to the HTTP listener host;
an optional `websocket.host` in `config.json` can override only the bind host.
