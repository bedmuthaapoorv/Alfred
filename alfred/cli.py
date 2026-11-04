"""`alfred` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from alfred import __version__
from alfred.config import Config, ConfigError, load_config
from alfred.core.store import Store


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_serve(cfg: Config, args: argparse.Namespace) -> int:
    from alfred.core.daemon import serve

    serve(cfg)
    return 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        daemon = store.get_state("daemon", {}) or {}
        last = store.get_state("last_heartbeat")
        age = None
        if last:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        status = {
            "running": bool(daemon.get("running")),
            "pid": daemon.get("pid"),
            "paused": store.is_paused(),
            "last_heartbeat": last,
            "heartbeat_age_seconds": round(age, 1) if age is not None else None,
            "stale": age is not None and age > 2 * cfg.heartbeat.interval_seconds,
            "events": store.count_events(),
            "db": str(cfg.core.db_path),
        }
    print(json.dumps(status, indent=2))
    return 0


def cmd_events(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        events = store.recent_events(limit=args.limit, source=args.source)
    for e in reversed(events):
        print(f"{e.received_at.isoformat(timespec='seconds')}  {e.topic:<24} {json.dumps(e.payload)}")
    return 0


def cmd_pause(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        store.set_paused(True)
    print("Alfred paused: observing only, no actions will be taken.")
    return 0


def cmd_resume(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        store.set_paused(False)
    print("Alfred resumed.")
    return 0


def cmd_config(cfg: Config, args: argparse.Namespace) -> int:
    print(json.dumps({"source": str(cfg.source) if cfg.source else None, **cfg.as_dict()}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alfred", description="Proactive, self-improving assistant.")
    p.add_argument("--version", action="version", version=f"alfred {__version__}")
    p.add_argument("-c", "--config", help="path to alfred.toml")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the always-on daemon").set_defaults(func=cmd_serve)
    sub.add_parser("status", help="show daemon health").set_defaults(func=cmd_status)
    ev = sub.add_parser("events", help="show recent events")
    ev.add_argument("-n", "--limit", type=int, default=20)
    ev.add_argument("-s", "--source", help="filter by sensor, e.g. heartbeat")
    ev.set_defaults(func=cmd_events)
    sub.add_parser("pause", help="kill switch: stop all actions").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="undo pause").set_defaults(func=cmd_resume)
    sub.add_parser("config", help="print effective config").set_defaults(func=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    _setup_logging(cfg.core.log_level)
    return args.func(cfg, args)
