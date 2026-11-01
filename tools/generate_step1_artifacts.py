"""Deterministic SYNTHETIC step 1 contracts; never fetches or creates an audit run."""

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geo_audit import BudgetSnapshot, Step0Config
from geo_audit.step1.config import Step1Config
from geo_audit.step1.models import Step1Result, Step2Input
from geo_audit.step1.schemas import MODELS, schema_for

ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime(2026, 10, 31, 12, tzinfo=UTC)
TEXT = "Example manufactures industrial pumps. " * 12
TEXT_HASH = hashlib.sha256(TEXT.encode()).hexdigest()


def sample_result():
    cfg0 = Step0Config()
    cfg = Step1Config(source_step0_config_sha256=cfg0.fingerprint())
    envelope = dict(
        schema_version="step1-1.0.0",
        algorithm_version="step1-1.0",
        run_id="synthetic-run",
        publication_id="synthetic-publication",
        config_hash=cfg.fingerprint(),
        registry_version="synthetic-registry",
        started_at=STAMP,
        finished_at=STAMP + timedelta(seconds=2),
    )
    text_ref = dict(evidence_id="text-1", text_hash=TEXT_HASH, text_chars=len(TEXT))
    evidence = []
    for identity, body, representation, parent, version in (
        ("html-1", b"<p>SYNTHETIC ONLY</p>", "entity_bytes", None, None),
        ("text-1", TEXT.encode(), "main_text", "html-1", "synthetic-extraction-1"),
    ):
        evidence.append(
            dict(
                evidence_id=identity,
                run_id="synthetic-run",
                file=dict(
                    path=f"raw/{identity}.bin",
                    sha256=hashlib.sha256(body).hexdigest(),
                    size_bytes=len(body),
                    complete=True,
                ),
                representation=representation,
                source_evidence_id=parent,
                transform_version=version,
                source_run_id=None,
                original_evidence_id=None,
            )
        )
    return Step1Result.model_validate(
        dict(
            source=dict(
                run_id="synthetic-run",
                site_sha256="1" * 64,
                manifest_sha256="2" * 64,
                source_step0_config_sha256=cfg0.fingerprint(),
                selected_evidence_id="html-1",
            ),
            config=cfg,
            evidence=tuple(evidence),
            crawl=dict(
                **envelope,
                candidates=(
                    dict(
                        candidate_id="candidate-1",
                        url="https://example.com/",
                        sources=(dict(kind="entry", source_id="html-1", occurrence=0),),
                        role="entry",
                        role_basis="entry",
                        language=None,
                        priority=0,
                        ordinal=0,
                        selected=True,
                        state="fetched",
                        reason_code=None,
                        alias_of=None,
                    ),
                ),
                pages=(
                    dict(
                        page_id="page-1",
                        candidate_id="candidate-1",
                        url="https://example.com/",
                        effective_url="https://example.com/",
                        evidence_id="html-1",
                        title="Example",
                        headings=(),
                        lists=(),
                        tables=(),
                        main_text=text_ref,
                        alternate_texts=(),
                        extraction_method="synthetic",
                        extractor_version="1",
                        extraction_reason="fixture",
                        content_state="html",
                        useful_candidate=True,
                        supporting_page=False,
                        business_relevance="observed",
                        business_quotes=(
                            dict(
                                evidence_id="text-1",
                                text_hash=TEXT_HASH,
                                start=0,
                                end=37,
                                text=TEXT[:37],
                            ),
                        ),
                        directives=(),
                        signals=(),
                        header_capture_complete=False,
                        limitations=("SYNTHETIC_NOT_AUDIT",),
                    ),
                ),
                robots=(),
                robots_decisions=(),
                sitemaps=(),
                pending_external_urls=(),
                coverage=dict(
                    discovered_urls=1,
                    selected_candidates=1,
                    fetch_successes=1,
                    unique_html_pages=1,
                    extracted_pages=1,
                    useful_pages=1,
                    skipped=0,
                    failed=0,
                    discarded_entries=0,
                    skip_examples=(),
                    skip_examples_truncated=False,
                    sitemap_truncated=False,
                ),
                material=dict(
                    state="limited",
                    can_start_step2=True,
                    reason="SYNTHETIC_ONLY",
                    useful_page_ids=("page-1",),
                    supporting_page_ids=(),
                    user_materials=(),
                ),
            ),
            checks=dict(**envelope, checks=(), diagnostics=()),
            run=dict(
                **envelope,
                status="ok",
                lifecycle="completed",
                stop_reason="SYNTHETIC_ONLY",
                budget=BudgetSnapshot(
                    schema_version="1.2.0",
                    started_at=STAMP,
                    captured_at=STAMP + timedelta(seconds=2),
                    step0_deadline_at=STAMP + timedelta(seconds=60),
                    total_deadline_at=STAMP + timedelta(seconds=300),
                    elapsed_seconds=2.0,
                    step0_attempts=1,
                    total_attempts=1,
                    step0_max_attempts=15,
                    total_max_attempts=150,
                ),
                browser_attempts=0,
                cache_hits=0,
                unfinished_urls=(),
                limitations=("SYNTHETIC_ONLY",),
            ),
        )
    )


def invalid_specs():
    return tuple(
        dict(case=name, path=path, value=value)
        for name, path, value in (
            ("unknown_version", ("schema_version",), "unknown"),
            ("foreign_run", ("run", "run_id"), "other-run"),
            ("config_hash", ("source", "source_step0_config_sha256"), "0" * 64),
            ("dangling_evidence", ("crawl", "pages", 0, "evidence_id"), "missing"),
            ("quote_offsets", ("crawl", "pages", 0, "business_quotes", 0, "end"), 99999),
            ("coverage", ("crawl", "coverage", "useful_pages"), 9),
            ("material_gate", ("crawl", "material", "can_start_step2"), False),
            ("not_terminal", ("crawl", "candidates", 0, "state"), "queued"),
        )
    )


def invalid_examples():
    base = sample_result().model_dump(mode="json")
    cases = []
    for spec in invalid_specs():
        path = spec["path"]
        payload = deepcopy(base)
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = spec["value"]
        cases.append(dict(case=spec["case"], payload=payload))
    return cases


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def artifacts():
    result = sample_result()
    values = {f"schemas/step1/{name}.schema.json": schema_for(name) for name in MODELS}
    values["examples/contracts/step1/result.json"] = result.model_dump(mode="json")
    values["examples/contracts/step1/step2-input.json"] = Step2Input(
        source_commit_sha256="0" * 64,
        result=result,
        useful_texts=(result.crawl.pages[0].main_text,),
        supporting_texts=(),
    ).model_dump(mode="json")
    values["examples/contracts/step1/invalid.json"] = dict(
        base="result.json",
        instructions="Replace the value at path in a copy of base; reject it.",
        cases=invalid_specs(),
    )
    return {name: encode(value) for name, value in values.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, body in artifacts().items():
        path = ROOT / name
        if args.check:
            if not path.is_file() or path.read_bytes() != body:
                raise SystemExit("Generated artifact differs: " + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    print(
        f"{'Checked' if args.check else 'Generated'} {len(artifacts())} synthetic artifacts; "
        "no I/O to network."
    )


if __name__ == "__main__":
    main()
