"""Controlled pipeline fixtures: real wall/memory, fake policy time, no public I/O."""

import argparse
import asyncio
import gc
import hashlib
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

import pytest

from geo_audit.step1.handoff import load_step2_input
from geo_audit.step1.pipeline import run_step1
from tests.fakes import wire
from tests.test_step1_pipeline import html
from tests.test_step1_sitemap import fixture, xml

CASES = ("one_page", "30_pages", "large_sitemap", "parameter_trap", "robots_error", "deadline")


def sample(name: str, *, memory: bool = True) -> dict[str, object]:
    entry, replies, options = html(), [wire(b"User-agent: *\nAllow: /")], {"max_sitemap_files": 0}
    if name == "30_pages":
        entry = html(links="".join(f'<a href="/ru/p{n}">P</a>' for n in range(29)))
        replies += [wire(html())] * 29
    elif name == "large_sitemap":
        replies = [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml"),
            wire(xml([f"https://example.com/ru/p{n}" for n in range(5000)])),
        ]
        replies += [wire(html())] * 29
        options = {}
    elif name == "parameter_trap":
        entry = html(links="".join(f'<a href="/ru/?n={n}">P</a>' for n in range(6000)))
        replies += [wire(html())]
    elif name in {"robots_error", "deadline"}:
        entry = html(links='<a href="/ru/about">About</a>')
        replies = (
            [wire(b"", status=503)] * 3
            if name == "robots_error"
            else [
                wire(b"User-agent: *\nAllow: /"),
                wire(b"", status=429, headers=b"Retry-After: 999\r\n"),
            ]
        )
    gc.collect()
    with tempfile.TemporaryDirectory(prefix="geo-step1-benchmark-") as directory:
        with pytest.MonkeyPatch.context() as patch:
            if memory:
                tracemalloc.start()
            started = time.perf_counter()
            runtime, _, clock, connector, _ = fixture(
                patch, Path(directory), replies, entry=entry, **options
            )
            result = asyncio.run(run_step1(runtime))
            loaded = load_step2_input(runtime.store.io.root, now=clock.utcnow())
            elapsed = time.perf_counter() - started
            peak = tracemalloc.get_traced_memory()[1] if memory else None
            if memory:
                tracemalloc.stop()
            if loaded.result != result or result.run.budget.total_attempts != len(connector.calls):
                raise ValueError("benchmark artifact/transport invariant failed")
            selection = [c.url for c in result.crawl.candidates if c.selected]
            invariants = dict(
                coverage=result.crawl.coverage.model_dump(mode="json"),
                material=result.crawl.material.state,
                attempts=len(connector.calls),
                selection_sha256=hashlib.sha256(json.dumps(selection).encode()).hexdigest(),
                text_hashes=[p.main_text.text_hash for p in result.crawl.pages if p.main_text],
            )
            return dict(
                case=name,
                wall_ms=elapsed * 1000,
                peak_mib=peak / 1024**2 if peak is not None else None,
                invariants=invariants,
            )


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--repeats", type=int, default=3)
    arguments.add_argument("--wall-only", action="store_true")
    args = arguments.parse_args()
    if not 1 <= args.repeats <= 10:
        arguments.error("repeats must be between1 and10")
    samples = [
        sample(name, memory=not args.wall_only) for name in CASES for _ in range(args.repeats)
    ]
    aggregates = []
    for name in CASES:
        group = [s for s in samples if s["case"] == name]
        if not all(s["invariants"] == group[0]["invariants"] for s in group):
            raise ValueError("benchmark replay changed selection/output invariants")
        aggregates.append(
            dict(
                case=name,
                median_ms=statistics.median(s["wall_ms"] for s in group),
                peak_mib=max(s["peak_mib"] for s in group) if not args.wall_only else None,
                invariants=group[0]["invariants"],
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dict(samples=samples, aggregates=aggregates), indent=2), encoding="utf8"
    )
    print(json.dumps(aggregates))


if __name__ == "__main__":
    main()
