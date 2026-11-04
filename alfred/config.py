"""Configuration: TOML file + ALFRED_* environment overrides.

Lookup order for the file: explicit path > $ALFRED_CONFIG > ./alfred.toml >
~/.config/alfred/alfred.toml. A missing file is fine; defaults apply.

Environment overrides use ALFRED_<SECTION>__<KEY>, e.g.
ALFRED_HEARTBEAT__INTERVAL_SECONDS=60.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class CoreConfig:
    data_dir: str = "~/.alfred"
    log_level: str = "INFO"
    timezone: str = "UTC"  # IANA name, e.g. "Asia/Kolkata"; drives "today" and the brief time

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def profile_path(self) -> Path:
        """Free-form notes about you that Alfred reads before every decision."""
        return self.data_path / "profile.md"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_path / "alfred.db"


@dataclass
class HeartbeatConfig:
    interval_seconds: float = 300.0


@dataclass
class ModelsConfig:
    triage: str = "claude-haiku-5-5"
    draft: str = "claude-sonnet-5-5"
    plan: str = "claude-opus-5-5"
    reflect: str = "claude-opus-5-5"


@dataclass
class BudgetConfig:
    max_actions_per_hour: int = 30
    max_daily_usd: float = 5.0


@dataclass
class GoogleConfig:
    enabled: bool = True
    client_secret: str = "~/.config/alfred/google_client_secret.json"
    token: str = "~/.config/alfred/google_token.json"
    gmail_poll_seconds: float = 60.0
    calendar_poll_seconds: float = 120.0
    upcoming_lead_minutes: int = 15  # emit calendar.event.upcoming this long before start

    @property
    def client_secret_path(self) -> Path:
        return Path(self.client_secret).expanduser()

    @property
    def token_path(self) -> Path:
        return Path(self.token).expanduser()


@dataclass
class NotifyConfig:
    channel: str = "email"
    email: str = ""  # the ONLY address Alfred may send to


@dataclass
class BriefConfig:
    enabled: bool = True
    time: str = "07:30"  # local time, HH:MM

    @property
    def at(self) -> time:
        return time.fromisoformat(self.time)


@dataclass
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    google: GoogleConfig = field(default_factory=GoogleConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    brief: BriefConfig = field(default_factory=BriefConfig)
    source: Path | None = None  # file the config was loaded from, if any

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {
            f.name: {sf.name: getattr(getattr(self, f.name), sf.name) for sf in fields(getattr(self, f.name))}
            for f in fields(self)
            if is_dataclass(getattr(self, f.name))
        }


class ConfigError(ValueError):
    pass


def _candidate_paths(explicit: str | Path | None, env: Mapping[str, str]) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    if env.get("ALFRED_CONFIG"):
        return [Path(env["ALFRED_CONFIG"]).expanduser()]
    return [Path("alfred.toml"), Path("~/.config/alfred/alfred.toml").expanduser()]


def _coerce(value: Any, target_type: Any, where: str) -> Any:
    target = target_type if isinstance(target_type, type) else type(target_type)
    try:
        if target is bool and isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if target is float and isinstance(value, (int, float, str)) and not isinstance(value, bool):
            return float(value)
        if target is int and isinstance(value, (int, str)) and not isinstance(value, bool):
            return int(value)
        if target is str:
            return str(value)
    except ValueError as e:
        raise ConfigError(f"{where}: cannot convert {value!r} to {target.__name__}") from e
    if not isinstance(value, target):
        raise ConfigError(f"{where}: expected {target.__name__}, got {type(value).__name__}")
    return value


def _apply(cfg: Config, section: str, key: str, value: Any, where: str) -> None:
    sub = getattr(cfg, section, None)
    if not is_dataclass(sub):
        raise ConfigError(f"{where}: unknown section [{section}]")
    if key not in {f.name for f in fields(sub)}:
        raise ConfigError(f"{where}: unknown key {section}.{key}")
    setattr(sub, key, _coerce(value, getattr(sub, key), where))


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = Config()

    candidates = _candidate_paths(path, env)
    for p in candidates:
        if p.is_file():
            with p.open("rb") as fh:
                data = tomllib.load(fh)
            for section, values in data.items():
                if not isinstance(values, dict):
                    raise ConfigError(f"{p}: top-level key {section!r} must be a table")
                for key, value in values.items():
                    _apply(cfg, section, key, value, str(p))
            cfg.source = p
            break
    else:
        if path is not None or env.get("ALFRED_CONFIG"):
            raise ConfigError(f"config file not found: {candidates[0]}")

    for name, value in env.items():
        if not name.startswith("ALFRED_") or "__" not in name:
            continue
        section, _, key = name[len("ALFRED_"):].lower().partition("__")
        _apply(cfg, section, key, value, f"env {name}")

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.heartbeat.interval_seconds <= 0:
        raise ConfigError("heartbeat.interval_seconds must be > 0")
    if cfg.google.gmail_poll_seconds <= 0 or cfg.google.calendar_poll_seconds <= 0:
        raise ConfigError("google poll intervals must be > 0")
    try:
        cfg.core.tz
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"core.timezone: unknown time zone {cfg.core.timezone!r}") from e
    try:
        cfg.brief.at
    except ValueError as e:
        raise ConfigError(f"brief.time: expected HH:MM, got {cfg.brief.time!r}") from e
    if cfg.notify.channel != "email":
        raise ConfigError(f"notify.channel: only 'email' is supported, got {cfg.notify.channel!r}")
    if cfg.notify.email and "@" not in cfg.notify.email:
        raise ConfigError(f"notify.email: not an email address: {cfg.notify.email!r}")
