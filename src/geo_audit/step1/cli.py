"""Additive CLI commands; old step0 and saved-source commands retain their behavior."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import TypeVar

from ..config import Step0Config
from ..evidence import EvidenceStore
from .config import Step1Config
from .handoff import load_step2_input
from .models import Step1Result
from .pipeline import audit_site, run_step1
from .runner import Step1Runtime

Parser = TypeVar("Parser", bound=argparse.ArgumentParser)


def configure(sub: "argparse._SubParsersAction[Parser]") -> None:
    audit = sub.add_parser("audit", help="Run the bounded0->1 chain; no LLM or profile")
    audit.add_argument("url")
    audit.add_argument("--step0-config", type=Path)
    saved = sub.add_parser("step1", help="Run step1 from a fresh verified saved step0 handoff")
    saved.add_argument("run_dir", type=Path)
    for command in (audit, saved):
        command.add_argument(
            "--config", type=Path, help="Step1Config; source config hash must match"
        )
        command.add_argument("--output-dir", type=Path, default=Path("private/runs"))
        description = command.add_mutually_exclusive_group()
        description.add_argument("--description")
        description.add_argument("--description-file", type=Path)
    consumer = sub.add_parser(
        "load-step2", help="Offline validate saved corpus and freshness, no network"
    )
    consumer.add_argument("run_dir", type=Path)
    recommend = sub.add_parser("recommend", help="Offline evidence-based technical recommendations")
    recommend.add_argument("run_dir", type=Path)


def bounded_config(path: Path) -> bytes:
    return EvidenceStore(path.absolute().parent).read_bytes(path.name, limit=1024 * 1024)


def run_command(args: argparse.Namespace) -> int:
    if args.command == "recommend":
        from ..recommendations import recommendation_report

        print(
            json.dumps(
                recommendation_report(args.run_dir).model_dump(mode="json"), ensure_ascii=True
            )
        )
        return 0
    if args.command == "load-step2":
        loaded = load_step2_input(args.run_dir)
        print(
            json.dumps(
                dict(
                    can_start_step2=True,
                    publication_id=loaded.result.run.publication_id,
                    material=loaded.result.crawl.material.state,
                    useful_texts=len(loaded.useful_texts),
                    supporting_texts=len(loaded.supporting_texts),
                    freshness_policy="step2-offline-observation-7d-1",
                )
            )
        )
        return 0
    config = Step1Config.model_validate_json(bounded_config(args.config)) if args.config else None
    if args.command == "audit":
        source = (
            Step0Config.model_validate_json(bounded_config(args.step0_config))
            if args.step0_config
            else Step0Config()
        )
        result = asyncio.run(
            audit_site(
                args.url,
                config=source,
                step1_config=config,
                output_dir=args.output_dir,
                description=args.description,
                description_file=args.description_file,
            )
        )
    else:
        runtime = Step1Runtime.from_directory(
            args.run_dir, output_dir=args.output_dir, config=config
        )
        result = asyncio.run(
            run_step1(runtime, description=args.description, description_file=args.description_file)
        )
    if isinstance(result, Step1Result):
        from ..recommendations import recommendation_report

        recommendations = recommendation_report(args.output_dir / result.run.publication_id)
        print(
            json.dumps(
                dict(
                    status=result.run.status,
                    material=result.crawl.material.state,
                    can_start_step2=result.crawl.material.can_start_step2,
                    coverage=result.crawl.coverage.model_dump(
                        mode="json", exclude={"skip_examples"}
                    ),
                    limitations=result.run.limitations,
                    attempts=result.run.budget.total_attempts,
                    result_directory=str(args.output_dir / result.run.publication_id),
                    recommendations=recommendations.model_dump(mode="json"),
                ),
                ensure_ascii=True,
            )
        )
        return {"ok": 0, "partial": 3, "needs_input": 4, "failed": 1}[result.run.status]
    print(
        json.dumps(
            dict(
                status=result.status,
                phase="step0",
                reason_codes=result.reason_codes,
                result_directory=str(result._run_directory),
            ),
            ensure_ascii=True,
        )
    )
    return (
        5
        if "OUTPUT_WRITE_FAILED" in result.reason_codes
        else {"ok": 0, "partial": 3, "needs_input": 4, "failed": 1}[result.status]
    )
