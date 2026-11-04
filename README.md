# Alfred

A proactive, self-improving Claude-based assistant. See [PLAN.md](PLAN.md) for
the design and roadmap.

**Status:** Phase 1 (observer). Alfred watches Gmail and Google Calendar,
triages every new item with Claude Haiku, and emails you a morning brief
written by Claude Sonnet. It reads only; the one thing it sends is email to
you.

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

## Setting up Phase 1

1. **Google:** follow [docs/google-setup.md](docs/google-setup.md), then run
   `alfred google-auth`.
2. **Claude:** put `ANTHROPIC_API_KEY=sk-ant-...` in `~/.config/alfred/env`
   (`chmod 600`). The systemd unit loads it.
3. **Config:** in `alfred.toml` set `core.timezone`, `notify.email` and
   `brief.time`. Set `budget.max_daily_usd` too; over budget, Alfred falls back
   to rules and templates instead of stopping.
4. **Tell Alfred about you (optional, high value):** write
   `~/.alfred/profile.md`, free-form notes such as who matters, what's noise,
   and your working hours. Triage reads it before every decision.
5. Try it: `alfred brief` previews today's brief without sending it.
   `alfred brief --send` emails it.

### What it does

| Piece | Behaviour |
|---|---|
| Gmail sensor | Polls history every 60s. On first start it backfills the last day of inbox mail. Reads headers and snippet only. Skips sent mail, spam and its own emails. |
| Calendar sensor | Emits changes (new invites included) and an `upcoming` event 15 min before each meeting you haven't declined. |
| Triage (Haiku) | Labels each email or calendar change `ignore` / `remember` / `act` / `ask` with an urgency of 0–3. Email content is wrapped as untrusted data. Without a model it falls back to rules that never ignore anything. |
| Morning brief (Sonnet) | Daily at `brief.time`: what needs you, today's calendar, what Alfred could handle, and FYIs. If Alfred was down at that time, it still sends within 3 hours. Falls back to a plain template. |
| Email | Sends **only** to `notify.email`. The code rejects any other To/Cc/Bcc address. Nothing is sent while paused. |

### Teaching it

```bash
alfred triage            # recent decisions with short ids
alfred correct 3fa9 ignore   # "that was noise". Feeds accuracy and future evals
alfred status            # google health, spend today, triage accuracy
alfred spend -d 7        # model cost by tier
```

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
  llm.py              Claude calls, cost accounting, daily budget
  sensors/heartbeat.py  drift-free periodic tick: the root of proactivity
  sensors/schedule.py   daily routines (morning brief)
  google/               OAuth, Gmail + Calendar clients and sensors
  agent/triage.py       first look at every item (Haiku)
  agent/brief.py        morning brief (Sonnet, template fallback)
  notify/email.py       owner-only email
deploy/alfred.service
tests/
```

## Tests

```bash
.venv/bin/pytest
```
