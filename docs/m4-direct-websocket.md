# M4: Python Direct WebSocket

The Python process publishes browser events directly and sends a full snapshot
whenever a WebSocket client connects or reconnects. `screen`, `table`, and
`admin` are all web-rendered clients of the same source of truth.

## Runtime path

```text
Serial / HTTP
  → GameRuntime / Scheduler
  → Python WebSocket service
  → screen / table / admin
```

## Wire protocol

Live events preserve the existing browser contract:

```json
{
  "type": "event",
  "address": "/game/state",
  "args": ["RUNNING", "", 3, 119.4, 29.4, "STARTING"]
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
- HTTP state fetch remains as a short fallback for startup races.

## Configuration

The WebSocket port is `8765` for all three browser clients. The listener host
defaults to the HTTP listener host; an optional `websocket.host` in
`config.json` can override only the bind host.
