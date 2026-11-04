import json

from alfred.cli import main
from alfred.core.events import Event
from alfred.core.store import Store


def test_status_events_pause(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALFRED_CORE__DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    with Store(tmp_path / "alfred.db") as s:
        s.append_event(Event(source="heartbeat", kind="tick", payload={"beat": 1}))

    assert main(["pause"]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["paused"] is True
    assert status["events"] == {"heartbeat.tick": 1}

    assert main(["events"]) == 0
    assert "heartbeat.tick" in capsys.readouterr().out
    assert main(["resume"]) == 0


def test_bad_config_exit_code(tmp_path, capsys):
    f = tmp_path / "bad.toml"
    f.write_text("[heartbeat]\ninterval_seconds = -1\n")
    assert main(["-c", str(f), "status"]) == 2
