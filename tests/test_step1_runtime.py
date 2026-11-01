"""Controlled transport, fake-clock and filesystem gates for the step 1 skeleton."""

import asyncio
import errno
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from geo_audit import GeoAuditError, Step0Config
from geo_audit.evidence import EvidenceStore
from geo_audit.handoff import prepare_step1
from geo_audit.request_policy import RequestAuthorization, RequestPolicy
from geo_audit.step1 import Step1Runtime, load_step1_result
from geo_audit.step1.storage import Step1Store
from tests.fakes import wire
from tests.test_step0_end_to_end import execute


async def allowed(url):
    return RequestAuthorization("allowed", "TEST_RULE")


def fixture_runtime(monkeypatch, tmp_path, scripts=(), config=None):
    site, client, budget, clock, connector, resolver = execute(
        monkeypatch,
        tmp_path / "source",
        [wire(), *scripts],
        config=config,
    )
    handoff = prepare_step1(site, budget=budget, timing=clock.services())
    runtime = Step1Runtime(
        handoff,
        source_directory=site._run_directory,
        output_dir=tmp_path / "publications",
        client=client,
        timing=clock.services(),
    )
    return runtime, site, clock, connector


def snapshot(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_same_process_reuses_identity_and_readonly_source(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    before = snapshot(site._run_directory)

    async def work():
        async with runtime:
            assert runtime.entry is site._fetch_result
            assert runtime.budget is site._live_budget
            runtime.note_cache_hit()

    asyncio.run(work())
    saved = load_step1_result(runtime.store.io.root)
    assert saved == runtime.result
    assert saved.run.status == "partial" and saved.run.stop_reason == "SKELETON_ONLY"
    assert not saved.crawl.material.can_start_step2
    assert saved.run.cache_hits == 2
    assert saved.run.budget.total_attempts == len(connector.calls) == 1
    assert saved.run.budget.total_deadline_at == site.budget.total_deadline_at
    assert snapshot(site._run_directory) == before
    assert (runtime.store.io.root / "site.json").read_bytes() == (
        site._run_directory / "site.json"
    ).read_bytes()
    with pytest.raises(ValueError):
        runtime.finish()
    with pytest.raises(ValueError):
        runtime.store.publish(saved)
    with pytest.raises(GeoAuditError, match="prepare"):
        Step1Store(runtime.store.io.root, EvidenceStore(site._run_directory), site)


def test_saved_input_new_process_has_no_dns_get_or_deadline_reset(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    program = r"""
import asyncio, json, socket, sys
from datetime import timedelta
from pathlib import Path
from geo_audit import TimeServices, load_site_result
from geo_audit.step1 import Step1Runtime, load_step1_result
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected network')
loop = asyncio.new_event_loop()  # Windows creates its local wakeup socket before the guard.
socket.getaddrinfo = socket.create_connection = socket.socket.connect = forbidden
source = Path(sys.argv[1])
site = load_site_result(source)
clock = TimeServices(utcnow=lambda: site.budget.captured_at + timedelta(seconds=10))
runtime = Step1Runtime.from_directory(source, output_dir=Path(sys.argv[2]), timing=clock)
async def work():
    async with runtime:
        assert runtime.entry.entity_bytes
loop.run_until_complete(work())
loop.close()
result = load_step1_result(runtime.store.io.root)
assert result.run.budget.total_deadline_at == site.budget.total_deadline_at
assert result.run.budget.total_attempts == site.budget.total_attempts
assert result.run.budget.elapsed_seconds >= 10
print(json.dumps({'status': result.run.status, 'attempts': result.run.budget.total_attempts}))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(site._run_directory), str(tmp_path / "new-process")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"status": "partial", "attempts": 1}
    assert len(connector.calls) == 1


@pytest.mark.parametrize(
    "mutation", ["expired", "version", "scope", "hash", "bytes", "budget", "assessment"]
)
def test_invalid_input_does_not_create_output_or_send(monkeypatch, tmp_path, mutation):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    ready = runtime.handoff
    if mutation == "expired":
        clock.seconds = 301
    elif mutation in {"version", "scope"}:
        data = json.loads((site._run_directory / "site.json").read_bytes())
        if mutation == "version":
            data["schema_version"] = "bad"
        else:
            data["allowed_hosts"].append("evil.test")
        (site._run_directory / "site.json").write_text(json.dumps(data), encoding="utf-8")
    elif mutation == "hash":
        selected = next(
            f for f in ready.contract.selected_evidence.files if f.representation == "entity_bytes"
        )
        (site._run_directory / selected.path).write_bytes(b"bad")
    elif mutation == "bytes":
        from dataclasses import replace

        ready = replace(ready, fetch_result=replace(ready.fetch_result, entity_bytes=b"forged"))
    elif mutation == "assessment":
        from dataclasses import replace

        ready = replace(
            ready,
            fetch_result=replace(
                ready.fetch_result,
                assessment=replace(ready.fetch_result.assessment, state="non_html"),
            ),
        )
    else:
        from dataclasses import replace

        from geo_audit import Budget

        ready = replace(ready, budget=Budget(site.config, timing=clock.services()))
    with pytest.raises(GeoAuditError):
        Step1Runtime(
            ready,
            source_directory=site._run_directory,
            output_dir=tmp_path / "invalid",
            client=runtime.client,
            timing=clock.services(),
        )
    assert not (tmp_path / "invalid").exists()
    assert len(connector.calls) == 1


def test_detached_handoff_cannot_replace_restore_with_fresh_budget(monkeypatch, tmp_path):
    from dataclasses import replace

    from geo_audit import Budget, load_step1_input

    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    loaded = load_step1_input(site._run_directory, timing=clock.services())
    clock.seconds += 50
    reset = Budget(site.config, timing=clock.services(), started_at=site.budget.started_at)
    reset.reserve_attempt(step=0)
    assert reset.snapshot().total_attempts == loaded.contract.budget.total_attempts
    assert reset.snapshot().total_deadline_at == site.budget.total_deadline_at
    forged = replace(loaded, budget=reset)
    with pytest.raises(GeoAuditError) as caught:
        Step1Runtime(
            forged,
            source_directory=site._run_directory,
            output_dir=tmp_path / "reset",
            client=runtime.client,
            timing=clock.services(),
        )
    assert caught.value.detail.code == "INPUT_ARTIFACT_INVALID"
    assert not (tmp_path / "reset").exists() and len(connector.calls) == 1


def test_detached_budget_sampling_survives_scheduler_delay(monkeypatch, tmp_path):
    from dataclasses import replace

    from geo_audit import load_step1_input

    _, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    loaded = load_step1_input(site._run_directory, timing=clock.services())

    def delayed_utc():
        clock.seconds += 0.02
        return clock.utcnow()

    timing = replace(clock.services(), utcnow=delayed_utc)
    resumed = Step1Runtime(
        loaded,
        source_directory=site._run_directory,
        output_dir=tmp_path / "resumed",
        timing=timing,
    )
    assert resumed.budget.snapshot().total_deadline_at == site.budget.total_deadline_at
    assert len(connector.calls) == 1


def test_restore_charges_initialization_work(monkeypatch, tmp_path):
    from geo_audit import Budget

    _, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    original = Budget.__init__

    def slow_init(self, *args, **kwargs):
        clock.seconds += 0.02
        original(self, *args, **kwargs)

    monkeypatch.setattr(Budget, "__init__", slow_init)
    clock.seconds += 10
    restored = Budget.restore(site.config, site.budget, timing=clock.services())
    assert restored.snapshot().elapsed_seconds >= clock.seconds
    assert restored.snapshot().total_deadline_at == site.budget.total_deadline_at
    assert len(connector.calls) == 1


def test_step0_fifteen_attempts_and_sixty_seconds_do_not_reset_total(monkeypatch, tmp_path):
    scripts = []
    for n in range(4):
        scripts += [
            wire(status=503),
            wire(status=503),
            wire(status=302, headers=f"Location: /hop{n}\r\n".encode()),
        ]
    scripts += [wire(status=503), wire(status=302, headers=b"Location: /final\r\n"), wire()]
    site, client, budget, clock, connector, _ = execute(
        monkeypatch,
        tmp_path / "source",
        [*scripts, wire(b"User-agent: *\n", headers=b"Content-Type: text/plain\r\n")],
    )
    assert site.can_enter_step1 and site.budget.step0_attempts == 15
    clock.seconds = 61
    ready = prepare_step1(site, budget=budget, timing=clock.services())
    runtime = Step1Runtime(
        ready,
        source_directory=site._run_directory,
        output_dir=tmp_path / "out",
        client=client,
        timing=clock.services(),
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert trace.error is None

    asyncio.run(work())
    result = load_step1_result(runtime.store.io.root)
    assert result.run.budget.step0_attempts == 15
    assert result.run.budget.total_attempts == len(connector.calls) == 16
    assert result.run.budget.total_deadline_at == site.budget.total_deadline_at


def test_external_robots_redirect_and_multivalue_headers_do_not_expand_html(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(status=302, headers=b"Location: https://cdn.test/policy\r\n"),
            wire(
                b"User-agent: *\n",
                headers=(
                    b"X-Robots-Tag: noindex\r\nX-Robots-Tag: googlebot: none\r\n"
                    b"Set-Cookie: secret=1\r\n"
                ),
            ),
        ],
        Step0Config(path_scope=("/ru/",)),
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert trace.scope == runtime.scope
            assert trace.algorithm_version == "step1-1.0"
            assert [a.purpose for a in trace.attempts] == ["initial", "redirect"]
            assert trace.attempts[-1].resource_headers == (
                ("content-length", "14"),
                ("x-robots-tag", "noindex"),
                ("x-robots-tag", "googlebot: none"),
            )
            blocked = await runtime.fetch(
                "https://cdn.test/ru/page", RequestPolicy("html", allowed)
            )
            assert blocked.error.code == "REDIRECT_EXTERNAL_HOST"

    asyncio.run(work())
    assert len(connector.calls) == 3
    assert "cdn.test" not in runtime.scope.allowed_hosts
    assert all(s.closed for s in connector.streams)
    assert load_step1_result(runtime.store.io.root).run.budget.total_attempts == 3


def test_nested_authorizer_without_deadlock_gates_each_redirect(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\n", headers=b"Content-Type: text/plain\r\n"),
            wire(status=302, headers=b"Location: /ru/blocked\r\n"),
        ],
    )
    evaluated = []

    async def authorize(url):
        evaluated.append(url)
        if len(evaluated) == 1:
            robots = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert robots.error is None
            return RequestAuthorization("allowed", "TEST_ALLOWED", min_interval_seconds=2)
        return RequestAuthorization("disallowed", "ROBOTS_FORBIDS")

    async def work():
        async with runtime:
            trace = await asyncio.wait_for(
                runtime.fetch("https://example.com/ru/start", RequestPolicy("html", authorize)), 3
            )
            assert trace.policy_denial.reason_code == "ROBOTS_FORBIDS"
            assert len(trace.attempts) == 1

    asyncio.run(work())
    assert evaluated == ["https://example.com/ru/start", "https://example.com/ru/blocked"]
    assert len(connector.calls) == 3
    assert clock.delays == [1.0, 2.0]


def test_retry_after_outside_shared_deadline_preserves_partial(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(status=503, headers=b"Retry-After: 999\r\n"),
        ],
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch(
                "https://example.com/ru/retry", RequestPolicy("html", allowed)
            )
            assert trace.error.code == "RETRY_DEFERRED"

    asyncio.run(work())
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.status == "partial" and saved.run.budget.total_attempts == 2
    assert len(connector.calls) == 2 and clock.delays == [1.0]


def test_cancellation_preserves_completed_raw_and_closes_stream(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\n"),
            [10.0, *wire()],
        ],
    )

    async def work():
        async with runtime:
            await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            task = asyncio.current_task()
            asyncio.get_running_loop().call_later(0.03, task.cancel)
            await runtime.fetch("https://example.com/ru/slow", RequestPolicy("html", allowed))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(work())
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.lifecycle == "cancelled" and saved.run.status == "partial"
    assert saved.run.stop_reason == "NETWORK_CANCELLED"
    assert saved.run.unfinished_urls == ("https://example.com/ru/slow",)
    assert saved.run.budget.total_attempts == len(connector.calls) == 3
    assert all(stream.closed for stream in connector.streams)
    assert any(
        b"User-agent: *" in (runtime.store.io.root / e.file.path).read_bytes()
        for e in saved.evidence
        if e.representation == "entity_bytes"
    )


def test_programming_observer_error_is_preserved_with_completed_evidence(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path, [wire()])
    bug = RuntimeError("controlled observer defect")

    async def broken(event):
        raise bug

    async def work():
        async with runtime:
            await runtime.fetch(
                "https://example.com/robots.txt", RequestPolicy("robots"), observer=broken
            )

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(work())
    assert caught.value is bug
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.status == "failed" and saved.run.stop_reason == "IMPLEMENTATION_BUG"
    assert saved.run.budget.total_attempts == len(connector.calls) == 2


@pytest.mark.parametrize(
    "target", ["raw", "raw/manifest.json", "crawl.json", "checks.json", "run.json"]
)
@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
def test_write_failure_has_no_commit_and_does_not_change_source(
    monkeypatch, tmp_path, target, code
):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path, [wire()])
    before = snapshot(site._run_directory)
    original = os.replace

    def denied(source, destination, **kwargs):
        # Handle-confined POSIX writes pass basenames and dir_fd; Windows uses full paths.
        name = Path(destination).name
        if name == Path(target).name or target == "raw" and name.endswith(".bin"):
            raise OSError(code, "controlled storage error")
        return original(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", denied)

    async def work():
        async with runtime:
            await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))

    with pytest.raises(GeoAuditError) as caught:
        asyncio.run(work())
    assert caught.value.detail.code == "OUTPUT_WRITE_FAILED"
    assert not (runtime.store.io.root / "run.json").exists()
    assert runtime.result is None and snapshot(site._run_directory) == before
    assert len(connector.calls) == 2
    with pytest.raises(GeoAuditError):
        load_step1_result(runtime.store.io.root)


def test_output_preflight_denied_sends_nothing(monkeypatch, tmp_path):
    import geo_audit.evidence as module

    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    original = module.open_confined

    def denied(path, *, writing=False):
        if writing:
            raise PermissionError("controlled permission denial")
        return original(path, writing=writing)

    monkeypatch.setattr(module, "open_confined", denied)
    with pytest.raises(GeoAuditError) as caught:
        Step1Runtime(
            runtime.handoff,
            source_directory=site._run_directory,
            output_dir=tmp_path / "denied",
            client=runtime.client,
            timing=clock.services(),
        )
    assert caught.value.detail.code == "OUTPUT_WRITE_FAILED" and len(connector.calls) == 1


def test_storage_and_cpu_caps_do_not_create_new_budget(monkeypatch, tmp_path):
    import geo_audit.step1.storage as module

    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    original = snapshot(runtime.store.io.root)
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 3)
    with pytest.raises(GeoAuditError):
        runtime.store.write_evidence(b"1234", representation="entity_bytes", complete=True)
    assert snapshot(runtime.store.io.root) == original
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", runtime.store._logical_bytes + 3)
    runtime.store.write_evidence(b"123", representation="entity_bytes", complete=True)
    with pytest.raises(GeoAuditError):
        runtime.store.write_evidence(b"x", representation="entity_bytes", complete=True)
    called = []
    with pytest.raises(GeoAuditError):
        runtime.bounded_phase(b"1234", lambda b: called.append(b), max_bytes=3)
    assert not called

    def late(data):
        clock.seconds = 301
        return data

    with pytest.raises(GeoAuditError) as caught:
        runtime.bounded_phase(b"abc", late, max_bytes=3)
    assert caught.value.detail.code == "BUDGET_DEADLINE" and len(connector.calls) == 1


def test_mixed_generation_and_tampered_commit_are_rejected(monkeypatch, tmp_path):
    first, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)
    result = first.finish()
    second = Step1Runtime(
        first.handoff,
        source_directory=site._run_directory,
        output_dir=tmp_path / "second",
        client=first.client,
        timing=clock.services(),
    )
    with pytest.raises(ValueError):
        second.store.publish(result)
    assert not (second.store.io.root / "run.json").exists()
    (first.store.io.root / "checks.json").write_bytes(b"{}")
    with pytest.raises(GeoAuditError):
        load_step1_result(first.store.io.root)


def test_total_attempt_cap_allows_cache_and_processing_but_no_new_transport(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [wire()],
        Step0Config(step0_max_attempts=1, total_max_attempts=2),
    )

    async def work():
        async with runtime:
            first = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert first.error is None
            runtime.note_cache_hit()
            assert runtime.bounded_phase(b"ok", bytes, max_bytes=2) == b"ok"
            blocked = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert blocked.error.code == "BUDGET_ATTEMPTS" and blocked.attempts == ()

    asyncio.run(work())
    assert len(connector.calls) == 2
    assert runtime.result.run.stop_reason == "BUDGET_ATTEMPTS"
    assert runtime.result.run.budget.step0_attempts == 1


def test_private_service_redirect_is_blocked_and_robots_cap_is_enforced(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(status=302, headers=b"Location: http://127.0.0.1/private\r\n"),
            wire(b"abcdef"),
        ],
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch("https://example.com/robots.txt", RequestPolicy("robots"))
            assert trace.error.code == "TARGET_NOT_PUBLIC"
            assert sum(a.request_sent for a in trace.attempts) == 1
            capped = await runtime.fetch(
                "https://example.com/robots.txt",
                RequestPolicy("robots", max_encoded_bytes=3, max_decoded_bytes=3),
            )
            assert capped.error.code == "CONTENT_TOO_LARGE"
            assert not capped.attempts[-1].body_complete

    asyncio.run(work())
    assert len(connector.calls) == 3
    assert RequestPolicy("robots").max_decoded_bytes == 512 * 1024


def test_expired_phase_yields_partial_not_failed(monkeypatch, tmp_path):
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path)

    async def work():
        async with runtime:

            def late(data):
                clock.seconds = 301
                return data

            runtime.bounded_phase(b"data", late, max_bytes=4)

    with pytest.raises(GeoAuditError):
        asyncio.run(work())
    result = load_step1_result(runtime.store.io.root)
    assert result.run.status == "partial" and result.run.stop_reason == "BUDGET_DEADLINE"
    assert len(connector.calls) == 1
