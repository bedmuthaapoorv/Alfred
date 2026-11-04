import pytest

from alfred.config import ConfigError, load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(env={})
    assert cfg.source is None
    assert cfg.heartbeat.interval_seconds == 300
    assert cfg.models.triage.startswith("claude-")


def test_file_and_env_override(tmp_path):
    f = tmp_path / "alfred.toml"
    f.write_text('[heartbeat]\ninterval_seconds = 10\n[models]\nplan = "x"\n')
    cfg = load_config(f, env={"ALFRED_HEARTBEAT__INTERVAL_SECONDS": "2.5"})
    assert cfg.source == f
    assert cfg.heartbeat.interval_seconds == 2.5
    assert cfg.models.plan == "x"


@pytest.mark.parametrize(
    "toml",
    ['[nope]\na = 1\n', '[heartbeat]\nbogus = 1\n', '[heartbeat]\ninterval_seconds = "fast"\n', '[heartbeat]\ninterval_seconds = 0\n'],
)
def test_invalid_config_rejected(tmp_path, toml):
    f = tmp_path / "alfred.toml"
    f.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(f, env={})


def test_missing_explicit_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml", env={})
