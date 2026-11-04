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
        if not args.offline:
            from alfred.google.auth import token_status

            status["google"] = token_status(cfg.google) if cfg.google.enabled else "disabled"
        status["gmail_error"] = store.get_state("gmail.last_error")
        status["calendar_error"] = store.get_state("calendar.last_error")
        from alfred.llm import start_of_day

        status["spend_today_usd"] = round(store.spend_since(start_of_day(cfg.core.tz)), 4)
        status["budget_usd"] = cfg.budget.max_daily_usd
        status["brief_last_sent"] = store.get_state("brief.last_sent_at")
        status["triage_accuracy"] = store.triage_accuracy()
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


def cmd_google_auth(cfg: Config, args: argparse.Namespace) -> int:
    from alfred.google.auth import GoogleAuthError, authorize

    try:
        creds = authorize(cfg.google, port=args.port)
    except GoogleAuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Authorized. Token saved to {cfg.google.token_path}")
    print("Granted scopes:\n  " + "\n  ".join(sorted(creds.scopes or [])))
    return 0


def cmd_brief(cfg: Config, args: argparse.Namespace) -> int:
    """Build the brief now; print it, or with --send email it."""
    import asyncio

    from alfred.agent.brief import BriefBuilder
    from alfred.core.router import ModelRouter
    from alfred.google.auth import GoogleAuthError, load_credentials
    from alfred.llm import LLM

    async def run() -> int:
        with Store(cfg.core.db_path) as store:
            calendar = gmail = None
            try:
                creds = load_credentials(cfg.google)
                from alfred.google.calendar import GoogleCalendarAPI
                from alfred.google.gmail import GoogleGmailAPI

                calendar, gmail = GoogleCalendarAPI(creds), GoogleGmailAPI(creds)
            except GoogleAuthError as e:
                print(f"(google unavailable: {e})", file=sys.stderr)
            llm = None if args.no_llm else LLM(ModelRouter(cfg.models), store, max_daily_usd=cfg.budget.max_daily_usd, tz=cfg.core.tz)
            builder = BriefBuilder(store, calendar, llm, cfg.core.tz)
            if not args.send:
                brief = await builder.build()
                print(f"Subject: {brief.subject}  [{brief.model}]\n\n{brief.text}")
                return 0
            if gmail is None or not cfg.notify.email:
                print("error: --send needs Google authorized and notify.email set", file=sys.stderr)
                return 1
            from alfred.core.daemon import deliver_brief
            from alfred.notify.email import EmailNotifier

            sent = await deliver_brief(builder, EmailNotifier(gmail, store, cfg.notify.email), store)
            print(f"Brief {'sent to ' + cfg.notify.email if sent else 'not sent (paused)'}")
            return 0

    return asyncio.run(run())


def cmd_triage(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        rows = store.recent_triage(limit=args.limit)
    for r in reversed(rows):
        label = r["label"] if not r["corrected_label"] else f"{r['label']}->{r['corrected_label']}"
        print(f"{r['event_id'][:8]}  {label:<17} u{r['urgency']}  {r['summary']}")
    return 0


def cmd_correct(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.core.db_path) as store:
        full = store.correct_triage(args.event_id, args.label)
        if full is None:
            print(f"no unique triage entry matches {args.event_id!r}", file=sys.stderr)
            return 1
        acc = store.triage_accuracy()
    print(f"Recorded {full[:8]} as {args.label}. Triage accuracy on reviewed items: {acc['accuracy']} ({acc['agree']}/{acc['reviewed']})")
    return 0


def cmd_spend(cfg: Config, args: argparse.Namespace) -> int:
    from datetime import timedelta

    from alfred.llm import start_of_day

    since = start_of_day(cfg.core.tz) - timedelta(days=args.days - 1)
    with Store(cfg.core.db_path) as store:
        print(json.dumps({"since": since.isoformat(), "total_usd": round(store.spend_since(since), 4),
                          "by_tier": store.spend_by_tier_since(since), "daily_budget_usd": cfg.budget.max_daily_usd}, indent=2))
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
    st = sub.add_parser("status", help="show daemon health")
    st.add_argument("--offline", action="store_true", help="skip the Google token check")
    st.set_defaults(func=cmd_status)
    ev = sub.add_parser("events", help="show recent events")
    ev.add_argument("-n", "--limit", type=int, default=20)
    ev.add_argument("-s", "--source", help="filter by sensor, e.g. heartbeat")
    ev.set_defaults(func=cmd_events)
    sub.add_parser("pause", help="kill switch: stop all actions").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="undo pause").set_defaults(func=cmd_resume)
    sub.add_parser("config", help="print effective config").set_defaults(func=cmd_config)

    ga = sub.add_parser("google-auth", help="authorize Gmail + Calendar (docs/google-setup.md)")
    ga.add_argument("--port", type=int, default=8765, help="local port for the OAuth redirect (tunnel it over SSH)")
    ga.set_defaults(func=cmd_google_auth)

    br = sub.add_parser("brief", help="preview the morning brief now (or --send it)")
    br.add_argument("--send", action="store_true", help="email it to notify.email")
    br.add_argument("--no-llm", action="store_true", help="use the plain template, no model call")
    br.set_defaults(func=cmd_brief)

    tr = sub.add_parser("triage", help="recent triage decisions")
    tr.add_argument("-n", "--limit", type=int, default=20)
    tr.set_defaults(func=cmd_triage)

    from alfred.agent.triage import LABELS

    co = sub.add_parser("correct", help="record the right triage label (measures accuracy, seeds evals)")
    co.add_argument("event_id", help="id or unique prefix from `alfred triage`")
    co.add_argument("label", choices=LABELS)
    co.set_defaults(func=cmd_correct)

    sp = sub.add_parser("spend", help="model spend")
    sp.add_argument("-d", "--days", type=int, default=1)
    sp.set_defaults(func=cmd_spend)
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
