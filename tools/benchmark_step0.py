"""Offline end-to-end benchmark; controlled DNS/TCP time, real parsing and artifact I/O."""

import argparse
import asyncio
import gc
import hashlib
import json
import platform
import ssl
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

from geo_audit import Budget, Step0Config, validate_site
from geo_audit.connection import PinnedBackend
from geo_audit.http_client import SafeHttpClient
from tests.fakes import Connector, Resolver, VirtualTime, wire


def measure(name):
    cfg = Step0Config()
    clock = VirtualTime()
    html = b"<html><p>" + b"x" * (cfg.max_decoded_bytes - 100) + b"</p></html>"
    scripts = {
        "https_html": [wire()],
        "redirects": [
            wire(b"", status=302, headers=f"Location: /hop{n}\r\n".encode()) for n in range(3)
        ]
        + [wire()],
        "tls_fallback": [[ssl.SSLCertVerificationError(1, "fixture")], wire()],
        "near_limit": [wire(html)],
        "retries": [wire(b"", status=503), wire(b"", status=429), wire()],
    }[name]
    connector = Connector(scripts)
    resolver = Resolver()
    counters = {
        "sha256_calls": 0,
        "hashed_bytes": 0,
        "psl_loads": 0,
        "ssl_contexts": 0,
        "classifications": 0,
        "classified_bytes": 0,
        "retained_body_bytes": 0,
        "duplicate_identity_bytes": 0,
    }
    original_sha = hashlib.sha256
    original_digest = hashlib.file_digest
    original_ssl = ssl.create_default_context
    import geo_audit.api as api
    import geo_audit.http_client as http

    original_assess = api.assess_response
    original_psl = http.load_bundled_psl
    original_attempt = http.HttpAttempt

    def attempt(*args, **kwargs):
        result = original_attempt(*args, **kwargs)
        counters["retained_body_bytes"] += len(result.encoded_body)
        if result.decoded_body is not result.encoded_body:
            counters["retained_body_bytes"] += len(result.decoded_body)
            if result.decoded_body == result.encoded_body:
                counters["duplicate_identity_bytes"] += len(result.decoded_body)
        return result

    def sha(data=b"", **kwargs):
        counters["sha256_calls"] += 1
        counters["hashed_bytes"] += len(data)
        return original_sha(data, **kwargs)

    def digest(stream, *args, **kwargs):
        counters["sha256_calls"] += 1
        counters["hashed_bytes"] += stream.seek(0, 2)
        stream.seek(0)
        return original_digest(stream, *args, **kwargs)

    def context(*args, **kwargs):
        counters["ssl_contexts"] += 1
        return original_ssl(*args, **kwargs)

    def psl():
        counters["psl_loads"] += 1
        return original_psl()

    def assess(attempt, **kwargs):
        counters["classifications"] += 1
        counters["classified_bytes"] += len(attempt.decoded_body)
        return original_assess(attempt, **kwargs)

    async def resolve(host, port):
        clock.seconds += 0.002
        return await Resolver.resolve(resolver, host, port)

    async def connect(*args, **kwargs):
        clock.seconds += 0.005
        return await Connector.connect_tcp(connector, *args, **kwargs)

    resolver.resolve = resolve
    connector.connect_tcp = connect
    gc.collect()
    with (
        tempfile.TemporaryDirectory(prefix="geo-bench-") as directory,
        patch.object(http, "PinnedBackend", lambda endpoint: PinnedBackend(endpoint, connector)),
        patch.object(hashlib, "sha256", sha),
        patch.object(hashlib, "file_digest", digest),
        patch.object(ssl, "create_default_context", context),
        patch.object(http, "load_bundled_psl", psl),
        patch.object(api, "assess_response", assess),
        patch.object(http, "HttpAttempt", attempt),
    ):
        tracemalloc.start()
        started = time.perf_counter()
        client = SafeHttpClient(cfg, resolver=resolver, timing=clock.services())
        budget = Budget(cfg, timing=clock.services())
        result = asyncio.run(
            validate_site(
                "example.com/",
                cfg,
                client,
                budget,
                output_dir=Path(directory),
                timing=clock.services(),
            )
        )
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if not result.can_enter_step1:
            raise RuntimeError(f"Benchmark did not produce usable input: {result.reason_codes}")
    return {
        "wall_ms": round(elapsed * 1000, 3),
        "peak_mib": round(peak / 2**20, 3),
        "virtual_seconds": round(clock.seconds, 3),
        "dns": len(resolver.calls),
        "http": len(connector.calls),
        **counters,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", default=3, type=int)
    args = parser.parse_args()
    results = {}
    for name in ("https_html", "redirects", "tls_fallback", "near_limit", "retries"):
        samples = [measure(name) for _ in range(args.repeats)]
        results[name] = {
            "median": {key: statistics.median(s[key] for s in samples) for key in samples[0]},
            "samples": samples,
        }
        print(name, results[name]["median"], flush=True)
    args.output.write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "repeats": args.repeats,
                "scenarios": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
