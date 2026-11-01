"""Acceptance checks prove forbidden side effects absent, using controlled clocks/I/O."""

import asyncio
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys

import pytest

from geo_audit import Budget, GeoAuditError, Step0Config, load_step1_input, validate_site
from geo_audit.cli import main
from geo_audit.connection import SystemResolver
from geo_audit.evidence import EvidenceStore
from geo_audit.handoff import load_site_result
from geo_audit.http_client import SafeHttpClient
from geo_audit.policy import FALLBACK_ERRORS, http_fallback_eligible
from tests.fakes import Connector, Resolver, Stream, VirtualTime, install_connector, wire
from tests.test_step0_end_to_end import execute


@pytest.mark.parametrize("url", ["example.com/", "http://example.com/", "https://example.com/"])
def test_scheme_and_no_extra_probe(monkeypatch, tmp_path, url):
    result, _, budget, clock, connector, resolver = execute(
        monkeypatch, tmp_path, [wire()], url=url
    )
    assert result.status == "ok" and not result.fallback_used
    assert connector.calls == [("8.8.8.8", 80 if url.startswith("http:") else 443)]
    assert budget.snapshot().total_attempts == len(resolver.calls) == 1
    assert load_step1_input(result._run_directory, timing=clock.services()).html


@pytest.mark.parametrize("code", sorted(FALLBACK_ERRORS))
@pytest.mark.parametrize("gate", ["enabled", "disabled", "used", "no_budget", "http", "status"])
def test_all_fallback_causes_and_gates(code, gate):
    assert http_fallback_eligible(
        initial_url="http://example.com/" if gate == "http" else "https://example.com/",
        error_code=code,
        status_code=403 if gate == "status" else None,
        enabled=gate != "disabled",
        fallback_already_used=gate == "used",
        retries_exhausted=True,
        budget_available=gate != "no_budget",
    ) is (gate == "enabled")


@pytest.mark.parametrize("status", [204, 206, 401, 403, 404, 410, 429, 500, 502, 503, 504])
def test_http_never_starts_fallback(monkeypatch, tmp_path, status):
    cfg = Step0Config(max_retries=0, retry_delays_seconds=())
    result, _, budget, _, connector, _ = execute(
        monkeypatch, tmp_path, [wire(b"", status=status)], config=cfg
    )
    assert not result.can_enter_step1 and not result.fallback_used
    assert len(connector.calls) == budget.snapshot().total_attempts == 1


@pytest.mark.parametrize("code,calls", [(socket.EAI_NONAME, 1), (socket.EAI_AGAIN, 3)])
def test_system_dns_failures_do_not_charge_http_or_fallback(monkeypatch, tmp_path, code, calls):
    lookups = []

    async def dns(*args, **kwargs):
        lookups.append(True)
        raise socket.gaierror(code, "fixture")

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", dns)
    result, _, budget, _, connector, _ = execute(
        monkeypatch, tmp_path, [], resolver=SystemResolver()
    )
    assert result.status == "failed" and not result.fallback_used
    assert connector.calls == [] and budget.snapshot().total_attempts == 0
    assert len(lookups) == calls


def test_virtual_retry_after_and_rate_limit(monkeypatch, tmp_path):
    result, _, budget, clock, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [wire(b"", status=503, headers=b"Retry-After: 7\r\n"), wire()],
    )
    assert result.status == "partial" and result.can_enter_step1
    assert clock.delays == [7] and clock.seconds == 7
    assert len(connector.calls) == budget.snapshot().total_attempts == 2


def test_virtual_slow_stream_stops_at_shared_deadline(monkeypatch, tmp_path):
    original = Stream.read
    clock = VirtualTime()

    async def slow(self, *args, **kwargs):
        clock.seconds += 31
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Stream, "read", slow)
    connector = Connector(
        [[b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", b"1\r\nx\r\n", b"1\r\ny\r\n"]]
    )
    install_connector(monkeypatch, connector)
    cfg = Step0Config()
    budget = Budget(cfg, timing=clock.services())
    client = SafeHttpClient(cfg, resolver=Resolver(), timing=clock.services())
    result = asyncio.run(
        validate_site(
            "example.com", cfg, client, budget, output_dir=tmp_path, timing=clock.services()
        )
    )
    assert result.status == "failed" and "BUDGET_DEADLINE" in result.reason_codes
    assert len(connector.calls) == budget.snapshot().total_attempts == 1
    assert all(s.closed for s in connector.streams) and not result.can_enter_step1


@pytest.mark.parametrize(
    "location,reason",
    [
        ("/ru/", "REDIRECT_LOOP"),
        ("https://other.com/", "REDIRECT_EXTERNAL_HOST"),
        ("http://127.0.0.1/", "TARGET_NOT_PUBLIC"),
    ],
)
def test_stopped_redirect_no_get(monkeypatch, tmp_path, location, reason):
    result, _, budget, _, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [wire(b"", status=302, headers=f"Location: {location}\r\n".encode())],
        url="example.com/ru/",
    )
    assert reason in result.reason_codes
    assert len(connector.calls) == budget.snapshot().total_attempts == 1
    assert "other.com" not in result.allowed_hosts


def test_redirect_limit_and_explicit_host(monkeypatch, tmp_path):
    cfg = Step0Config(max_redirect_hops=1, allowed_hosts=("other.com",))
    result, _, budget, _, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [
            wire(b"", status=302, headers=b"Location: https://other.com/a\r\n"),
            wire(b"", status=302, headers=b"Location: /b\r\n"),
        ],
        config=cfg,
    )
    assert "REDIRECT_LIMIT" in result.reason_codes
    assert len(connector.calls) == budget.snapshot().total_attempts == 2


def test_relative_redirect_www_alias_survives_loading(monkeypatch, tmp_path):
    result, _, _, clock, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [
            wire(b"", status=302, headers=b"Location: https://www.example.com/ru/a\r\n"),
            wire(b"", status=302, headers=b"Location: ../final?x=1&x=2\r\n"),
            wire(),
        ],
    )
    assert result.effective_url == "https://www.example.com/final?x=1&x=2"
    assert result.allowed_hosts == ("example.com", "www.example.com")
    assert result.redirect_chain[0].psl_sha256
    assert load_step1_input(result._run_directory, timing=clock.services()).html
    assert len(connector.calls) == 3


def test_timeout_programming_bug_is_not_network_failure(monkeypatch, tmp_path):
    async def bug(*args, **kwargs):
        raise TimeoutError("programming defect")

    monkeypatch.setattr(Resolver, "resolve", bug)
    with pytest.raises(TimeoutError):
        execute(monkeypatch, tmp_path, [])
    assert not list(tmp_path.glob("*/site.json"))


def test_cancel_runner_preserves_completed_observation(monkeypatch, tmp_path):
    async def scenario():
        connector = Connector([wire(b"", status=302, headers=b"Location: /next\r\n")])
        install_connector(monkeypatch, connector)
        entered = asyncio.Event()
        clock = VirtualTime()

        async def wait(_delay):
            entered.set()
            await asyncio.Event().wait()

        from geo_audit.timing import TimeServices

        timing = TimeServices(clock.monotonic, clock.utcnow, wait, lambda a, b: 0)
        cfg = Step0Config()
        client = SafeHttpClient(cfg, resolver=Resolver(), timing=timing)
        task = asyncio.create_task(
            validate_site(
                "example.com",
                cfg,
                client,
                Budget(cfg, timing=timing),
                output_dir=tmp_path,
                timing=timing,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        sites = list(tmp_path.glob("*/site.json"))
        assert len(sites) == 1
        saved = load_site_result(sites[0].parent)
        assert saved.status == "failed" and "NETWORK_CANCELLED" in saved.reason_codes
        assert len(saved.evidence) >= 1 and saved.attempts[0].status_code == 302
        assert len(connector.calls) == saved.budget.total_attempts == 1
        assert all(s.closed for s in connector.streams)

    asyncio.run(scenario())


def test_interrupted_atomic_write_has_no_commit_or_temp(monkeypatch, tmp_path):
    import geo_audit.evidence as module

    store = EvidenceStore(tmp_path / "run")
    store.prepare(new=True)
    replaced = []

    def interrupt(*args, **kwargs):
        replaced.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(module.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        store.write_blob(b"partial", representation="entity_bytes", complete=True)
    assert replaced == [True] and not list(store.root.rglob("*.tmp"))
    assert not (store.root / "site.json").exists()


def test_storage_preparation_failure_sends_nothing(monkeypatch, tmp_path):
    import geo_audit.evidence as module

    def denied(*args, **kwargs):
        raise OSError("disk fixture")

    monkeypatch.setattr(module.os, "fsync", denied)
    result, _, budget, _, connector, resolver = execute(monkeypatch, tmp_path, [])
    assert "OUTPUT_WRITE_FAILED" in result.reason_codes and not result.can_enter_step1
    assert connector.calls == resolver.calls == [] and budget.snapshot().total_attempts == 0


def test_repeat_run_never_overwrites_existing_data(monkeypatch, tmp_path):
    first, _, _, _, _, _ = execute(monkeypatch, tmp_path, [wire()])
    before = (first._run_directory / "site.json").read_bytes()
    second, _, _, _, _, _ = execute(monkeypatch, tmp_path, [wire()])
    assert first.run_id != second.run_id
    assert (first._run_directory / "site.json").read_bytes() == before
    assert load_site_result(second._run_directory).status == "ok"


def test_dns_change_on_redirect_is_checked_again_before_connection(monkeypatch, tmp_path):
    resolver = Resolver()

    async def changing(host, port):
        resolver.calls.append((host, port))
        return ("8.8.8.8",) if len(resolver.calls) == 1 else ("127.0.0.1",)

    resolver.resolve = changing
    result, _, budget, _, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [wire(b"", status=302, headers=b"Location: /next\r\n")],
        resolver=resolver,
    )
    assert "TARGET_NOT_PUBLIC" in result.reason_codes
    assert (
        len(resolver.calls) == 2 and len(connector.calls) == budget.snapshot().total_attempts == 1
    )
    assert connector.calls == [("8.8.8.8", 443)]


def test_small_contact_form_with_captcha_is_not_a_confirmed_barrier(monkeypatch, tmp_path):
    html = (
        b"<html><title>Company contacts</title><p>Consulting services. Contact us.</p>"
        b"<form><div class='g-recaptcha' data-sitekey='x'></div>"
        b"<p>Verify you are human before sending this form</p></form></html>"
    )
    result, _, _, _, connector, _ = execute(monkeypatch, tmp_path, [wire(html)])
    assert result.content_state == "html" and result.can_enter_step1
    assert "ACCESS_CHALLENGE" not in result.reason_codes and len(connector.calls) == 1


def test_observer_work_cannot_extend_deadline(monkeypatch, tmp_path):
    clock = VirtualTime()
    connector = Connector([wire()])
    install_connector(monkeypatch, connector)
    cfg = Step0Config()
    budget = Budget(cfg, timing=clock.services())
    client = SafeHttpClient(cfg, resolver=Resolver(), timing=clock.services())
    original = EvidenceStore.checkpoint

    def slow_checkpoint(self, *args, **kwargs):
        if args[1]:
            clock.seconds += 61
        return original(self, *args, **kwargs)

    monkeypatch.setattr(EvidenceStore, "checkpoint", slow_checkpoint)
    result = asyncio.run(
        validate_site(
            "example.com", cfg, client, budget, output_dir=tmp_path, timing=clock.services()
        )
    )
    assert not result.can_enter_step1 and "BUDGET_DEADLINE" in result.reason_codes
    assert len(connector.calls) == 1 and all(s.closed for s in connector.streams)


def test_hard_process_exit_keeps_evidence_without_positive_commit(tmp_path):
    root = tmp_path / "run-crash"
    program = r"""
import os, sys
from pathlib import Path
from geo_audit.evidence import EvidenceStore
store = EvidenceStore(Path(sys.argv[1]))
store.prepare(new=True)
blob = store.write_blob(b"completed observation", representation="entity_bytes", complete=True)
store.atomic_json("raw/manifest.json", [blob.model_dump(mode="json")])
store.atomic_json("journal.json", {"state": "running"})
original = os.replace
def interrupt(source, target, **kwargs):
    if Path(target).name == "site.json":
        os._exit(99)
    return original(source, target, **kwargs)
os.replace = interrupt
store.atomic_json("site.json", {"status": "ok"})
"""
    child = subprocess.run([sys.executable, "-c", program, str(root)], timeout=15, check=False)
    assert child.returncode == 99 and not (root / "site.json").exists()
    assert json.loads((root / "journal.json").read_bytes())["state"] == "running"
    files = json.loads((root / "raw/manifest.json").read_bytes())
    assert (root / files[0]["path"]).read_bytes() == b"completed observation"
    with pytest.raises(GeoAuditError):
        load_step1_input(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
def test_windows_directory_swap_fails_while_pinned(tmp_path):
    from geo_audit.secure_io import open_confined, pinned_directory

    parent = tmp_path / "run"
    parent.mkdir()
    raw = parent / "raw"
    raw.mkdir()
    (raw / "body.bin").write_bytes(b"original")
    with pinned_directory(raw):
        with pytest.raises(PermissionError):
            raw.rename(parent / "moved")
        with open_confined(raw / "body.bin") as stream:
            assert stream.read() == b"original"
    raw.rename(parent / "moved")  # Handles were released; no descriptor leak prevents renaming.


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "junction"])
def test_linked_evidence_rejected_before_read(monkeypatch, tmp_path, kind):
    import geo_audit.secure_io as secure

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret.bin"
    sentinel.write_bytes(b"do not read")
    root = tmp_path / "run"
    root.mkdir()
    raw = root / "raw"
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction scenario")
        import _winapi

        _winapi.CreateJunction(str(outside), str(raw))
        target = "raw/secret.bin"
    else:
        raw.mkdir()
        target = "raw/linked.bin"
        if kind == "hardlink":
            os.link(sentinel, root / target)
        else:
            try:
                os.symlink(sentinel, root / target)
            except OSError as error:
                pytest.skip(f"OS lacks symlink privilege: {error.winerror}")
    opened_streams = []
    original = secure.os.fdopen

    def track(*args, **kwargs):
        opened_streams.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(secure.os, "fdopen", track)
    with pytest.raises(GeoAuditError):
        EvidenceStore(root).read_bytes(target, limit=100)
    assert opened_streams == [] and sentinel.read_bytes() == b"do not read"


def rewrite(site, root):
    (root / "site.json").write_text(json.dumps(site), encoding="utf-8")
    (root / "raw/manifest.json").write_text(json.dumps(site["evidence"]), encoding="utf-8")


@pytest.mark.parametrize(
    "mutation",
    [
        "versions",
        "missing",
        "text",
        "encoded",
        "observation",
        "scope",
        "path",
        "deadline",
        "duplicate_json",
    ],
)
def test_artifact_forgery_rejected(monkeypatch, tmp_path, mutation):
    result, _, _, clock, connector, resolver = execute(monkeypatch, tmp_path, [wire()])
    root = result._run_directory
    site = json.loads((root / "site.json").read_bytes())
    if mutation == "versions":
        site["algorithm_version"] = "step0-1.0"
    elif mutation == "scope":
        site["allowed_hosts"].append("evil.com")
    elif mutation == "path":
        site["evidence"][0]["files"][0]["path"] = "raw/../../outside"
    elif mutation == "deadline":
        clock.seconds = 301
    elif mutation == "duplicate_json":
        (root / "site.json").write_text('{"status":"ok","status":"failed"}', encoding="utf-8")
    else:
        rep = {
            "missing": "entity_bytes",
            "text": "decoded_text",
            "encoded": "content_encoded",
            "observation": "attempt_observation",
        }[mutation]
        file = next(f for f in site["evidence"][0]["files"] if f["representation"] == rep)
        if mutation == "missing":
            (root / file["path"]).unlink()
        else:
            payload = (
                b"<html><p>Different body</p></html>" if rep != "attempt_observation" else b"{}"
            )
            (root / file["path"]).write_bytes(payload)
            file.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    if mutation != "duplicate_json":
        rewrite(site, root)
    with pytest.raises(GeoAuditError) as caught:
        load_step1_input(root, timing=clock.services())
    assert caught.value.detail.code == "INPUT_ARTIFACT_INVALID"
    assert len(connector.calls) == len(resolver.calls) == 1


def test_network_logs_redact_url_headers_and_exception(monkeypatch, tmp_path, caplog):
    original = Stream.write

    async def diagnostic(self, data, *args, **kwargs):
        logging.getLogger("httpcore.http11").warning("cookie=secret\nFAKE token=%s", "secret")
        return await original(self, data, *args, **kwargs)

    monkeypatch.setattr(Stream, "write", diagnostic)
    caplog.set_level(logging.DEBUG)
    result, _, _, _, _, _ = execute(
        monkeypatch,
        tmp_path,
        [wire(headers=b"Content-Type: text/html\r\nSet-Cookie: private=secret\r\n")],
    )
    assert result.status == "ok"
    assert "secret" not in caplog.text and "FAKE" not in caplog.text
    assert not any(h.name == "set-cookie" for h in result.attempts[0].response_headers)


def test_cli_parse_error_does_not_echo_rejected_secret(capsys):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "step0",
                "example.com",
                "https://user:private-marker@example.com/?token=private-marker",
            ]
        )
    assert error.value.code == 2
    assert "private-marker" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "state,expected", [("ok", 0), ("partial", 3), ("needs_input", 4), ("failed", 1)]
)
def test_cli_exit_codes_real_runner(monkeypatch, tmp_path, capsys, state, expected):
    scripts = {
        "ok": [wire()],
        "partial": [wire(b"", status=204)],
        "needs_input": [wire(b"", status=302, headers=b"Location: https://other.com/\r\n")],
        "failed": [wire(b"", status=403)],
    }[state]
    connector = Connector(scripts)
    install_connector(monkeypatch, connector)
    monkeypatch.setattr("geo_audit.http_client.SystemResolver", Resolver)
    assert main(["step0", "example.com/?secret=local", "--output-dir", str(tmp_path)]) == expected
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["status"] == state and "secret" not in output.out + output.err
    assert len(connector.calls) == 1
