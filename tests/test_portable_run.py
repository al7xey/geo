"""The launcher must not install packages or start a live audit implicitly."""

import importlib.util
import json
import socket
from pathlib import Path

import pytest

from geo_audit.errors import GeoAuditError


def load_script(name, relative):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", ["demo", "analyze", "serve", "collect"])
def test_missing_environment_requires_explicit_setup(monkeypatch, tmp_path, command):
    launcher = load_script("portable_launcher", "run.py")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "call", lambda args: pytest.fail("implicit install or network"))
    assert launcher.main([command]) == 2


def test_verify_dispatch_preserves_operator_path_as_single_argument(monkeypatch, tmp_path):
    launcher = load_script("portable_launcher", "run.py")
    python = launcher.environment_python(tmp_path / ".venv")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(launcher, "call", lambda args: calls.append(args) or 0)
    assert launcher.main(["verify", "private/Учебный результат"]) == 0
    assert calls == [
        [
            str(python),
            "-X",
            "utf8",
            str(tmp_path / "tools" / "steps0_8.py"),
            "analyze",
            "--verify",
            "private/Учебный результат",
        ]
    ]


def test_launcher_reports_broken_interpreter_without_traceback(monkeypatch, capsys):
    launcher = load_script("portable_launcher", "run.py")

    def broken(*args, **kwargs):
        raise OSError("synthetic inaccessible interpreter")

    monkeypatch.setattr(launcher.subprocess, "run", broken)
    assert launcher.call(["synthetic-python"]) == 2
    text = capsys.readouterr().out
    assert "run.py setup" in text and "Traceback" not in text


def test_offline_demo_has_no_network_and_preserves_source(monkeypatch, tmp_path):
    demo = load_script("portable_demo", "tools/offline_demo.py")
    source = demo.ROOT / "examples" / "mock_answers_v1" / "input" / "answers.jsonl"
    original = source.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("offline demo attempted network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    report = demo.demo(tmp_path / "Учебный прогон")
    assert report.is_file() and source.read_bytes() == original
    assert len((report.parent / "bundle" / "answers.jsonl").read_bytes().splitlines()) == 480
    summary = json.loads((report.parent / "bundle" / "summary.json").read_bytes())
    assert summary["synthetic"] is True
    assert summary["model_calls"] == summary["network_requests"] == 0
    with pytest.raises(GeoAuditError):
        demo.demo(tmp_path / "Учебный прогон")
