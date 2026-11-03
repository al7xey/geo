"""Finish steps 9-12 from a saved step 8, and verify all adjacent handoffs."""

import argparse
import json
from pathlib import Path
from typing import Literal

from ..contracts import SHA256, Contract
from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..secure_io import pinned_directory
from ..step5.pipeline import MAX_METADATA_BYTES, digest, unique_json
from ..step5.replay import replay_session
from ..step5.workflow import verify as verify_5_8
from ..step9 import pipeline as step9
from ..step9 import storage
from ..step9.models import MetricPolicy
from ..step10 import pipeline as step10
from ..step11 import pipeline as step11
from . import pipeline as step12

STAGES = (("step9", step9), ("step10", step10), ("step11", step11), ("step12", step12))


class TailManifest(Contract):
    schema_version: Literal["steps9-12-chain-1.0"] = "steps9-12-chain-1.0"
    committed: Literal[True] = True
    stages: dict[str, SHA256]
    upstream_chain_sha256: SHA256 | None
    model_calls: Literal[0] = 0
    network_requests: Literal[0] = 0


def verify(directory: Path) -> TailManifest:
    source = EvidenceStore(directory)
    manifest = TailManifest.model_validate(
        unique_json(source.read_bytes("chain.json", limit=MAX_METADATA_BYTES))
    )
    if set(manifest.stages) != {name for name, _ in STAGES}:
        raise ValueError("invalid final chain stages")
    with replay_session():
        for name, module in STAGES:
            actual = source.read_bytes(name + "/manifest.json", limit=MAX_METADATA_BYTES)
            if digest(actual) != manifest.stages[name]:
                raise ValueError("changed final stage manifest")
            module.verify_publication(source.root / name)
        for previous, current, names in (
            ("step9", "step10", step10.SOURCE_FILES),
            ("step10", "step11", step11.SOURCE_FILES),
            ("step11", "step12", step12.SOURCE_FILES),
        ):
            captured = source.read_bytes(
                current + "/input/source-manifest.json", limit=MAX_METADATA_BYTES
            )
            if captured != source.read_bytes(previous + "/manifest.json", limit=MAX_METADATA_BYTES):
                raise ValueError("final stages are from different runs")
            for name in names:
                if source.read_bytes(
                    current + "/input/" + name, limit=MAX_METADATA_BYTES
                ) != source.read_bytes(previous + "/" + name, limit=MAX_METADATA_BYTES):
                    raise ValueError("final stage inputs differ from preceding outputs")
    upstream = source.read_bytes("upstream-chain.json", limit=MAX_METADATA_BYTES)
    if (digest(upstream) if upstream != b"null\n" else None) != manifest.upstream_chain_sha256:
        raise ValueError("upstream chain marker changed")
    if source.read_bytes("report.html", limit=MAX_METADATA_BYTES) != source.read_bytes(
        "step12/report.html", limit=MAX_METADATA_BYTES
    ):
        raise ValueError("root report differs from final report")
    for name in ("aggregates.json", "findings.json", "plan.json", "report_data.json"):
        if source.read_bytes(name, limit=MAX_METADATA_BYTES) != source.read_bytes(
            "step12/" + name, limit=MAX_METADATA_BYTES
        ):
            raise ValueError("root download differs from final output")
    return manifest


def run(
    step8_dir: Path,
    output_dir: Path,
    *,
    site_dir: Path | None = None,
    results_file: Path | None = None,
    policy: MetricPolicy | None = None,
    upstream_chain: Path | None = None,
    source_checks_dir: Path | None = None,
) -> TailManifest:
    storage.separate(step8_dir, output_dir)
    if site_dir:
        storage.separate(site_dir, output_dir)
    if results_file:
        storage.separate(results_file.parent, output_dir)
    if upstream_chain:
        storage.separate(upstream_chain, output_dir)
    if source_checks_dir:
        storage.separate(source_checks_dir, output_dir)
    upstream = b"null\n"
    with replay_session():
        if upstream_chain:
            verify_5_8(upstream_chain)
            if EvidenceStore(upstream_chain / "step8").root != EvidenceStore(step8_dir).root:
                raise ValueError("upstream chain does not supply this step 8")
            upstream = storage.read(upstream_chain / "chain.json")
        output = EvidenceStore(output_dir)
        output.prepare(new=True)
        with pinned_directory(output.root):
            step9.run_step9(
                step8_dir,
                output.root / "step9",
                site_dir=site_dir,
                policy=policy,
                source_checks_dir=source_checks_dir,
            )
            step10.run_step10(
                output.root / "step9", output.root / "step10", results_file=results_file
            )
            step11.run_step11(output.root / "step10", output.root / "step11")
            step12.run_step12(output.root / "step11", output.root / "step12")
            output.atomic_bytes("upstream-chain.json", upstream)
            report = output.read_bytes("step12/report.html", limit=MAX_METADATA_BYTES)
            output.atomic_bytes("report.html", report)
            # Root report's fixed downloads are adjacent copies of final outputs.
            for name in ("aggregates.json", "findings.json", "plan.json", "report_data.json"):
                output.atomic_bytes(
                    name, output.read_bytes("step12/" + name, limit=MAX_METADATA_BYTES)
                )
            manifest = TailManifest(
                stages={
                    name: digest(
                        output.read_bytes(name + "/manifest.json", limit=MAX_METADATA_BYTES)
                    )
                    for name, _ in STAGES
                },
                upstream_chain_sha256=digest(upstream) if upstream_chain else None,
            )
            output.atomic_json("chain.json", manifest.model_dump(mode="json"))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шаги 9–12 без API: расчёты, темы, план и отчёт.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--step8-dir", type=Path)
    source.add_argument("--chain-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--site-dir", type=Path)
    parser.add_argument("--gap-results", type=Path)
    parser.add_argument("--source-checks-dir", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if any(
            v is not None
            for v in (
                args.step8_dir,
                args.chain_dir,
                args.output_dir,
                args.site_dir,
                args.gap_results,
                args.source_checks_dir,
                args.policy,
            )
        ):
            parser.error("--verify используется отдельно")
    elif not args.output_dir or not (args.step8_dir or args.chain_dir):
        parser.error("нужны --chain-dir или --step8-dir и новая --output-dir")
    try:
        if args.verify:
            manifest = verify(args.verify)
        else:
            policy = (
                step9.json_model(storage.read(args.policy), MetricPolicy) if args.policy else None
            )
            manifest = run(
                args.step8_dir or args.chain_dir / "step8",
                args.output_dir,
                site_dir=args.site_dir,
                results_file=args.gap_results,
                policy=policy,
                upstream_chain=args.chain_dir,
                source_checks_dir=args.source_checks_dir,
            )
        print(
            json.dumps(
                {
                    "status": "published" if not args.verify else "verified",
                    "report": str((args.output_dir or args.verify) / "report.html"),
                    "model_calls": manifest.model_calls,
                    "network_requests": manifest.network_requests,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RecursionError, GeoAuditError):
        print(
            "Не удалось завершить шаги. Проверьте входные данные и выберите новую папку результата."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
