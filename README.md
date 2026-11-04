# Alfred

A proactive, self-improving Claude-based assistant. See [PLAN.md](PLAN.md) for
the design and roadmap.

**Status:** Phase 0 (skeleton). The daemon runs, wakes on a heartbeat, and
logs every event to an append-only SQLite log. No model calls or external
actions yet.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp alfred.example.toml alfred.toml      # optional; defaults work
.venv/bin/alfred serve                  # run the daemon (Ctrl-C to stop)
```

In another shell:

```bash
alfred status        # health: running, last heartbeat, stale?, event counts
alfred events -n 20  # recent events (filter with -s heartbeat)
alfred pause         # kill switch: no actions while paused (observing continues)
alfred resume
alfred config        # effective configuration
```

Any setting can be overridden with `ALFRED_<SECTION>__<KEY>`, e.g.
`ALFRED_HEARTBEAT__INTERVAL_SECONDS=60`.

## Running always-on

`deploy/alfred.service` is a systemd user unit (install steps are in the file).
It restarts Alfred if it exits; inside the daemon, a crashed sensor is
restarted with backoff without taking the rest down.

## Layout

```
alfred/
  cli.py              alfred command
  config.py           TOML + env config, validated
  core/events.py      Event: the one shape every sensor emits
  core/bus.py         async pub/sub with topic patterns ("gmail.*")
  core/store.py       SQLite: append-only event log, state, pause switch
  core/router.py      tier (triage/draft/plan/reflect) -> model ID
  core/daemon.py      wires sensors -> bus -> handlers; signals; restarts
  sensors/heartbeat.py  drift-free periodic tick: the root of proactivity
deploy/alfred.service
tests/
```

## Tests

```bash
.venv/bin/pytest
```
