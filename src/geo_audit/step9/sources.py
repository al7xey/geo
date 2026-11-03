"""Explicit optional top-five page inspection through the existing safe crawler.

Never called by analysis or demo. No model calls. Default CLI refuses networking.
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from ..config import Step0Config
from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..step1.config import Step1Config
from ..step1.models import Step1Result
from ..step1.pipeline import audit_site
from ..step1.storage import load_step1_result, verify_file
from ..step5.extract import folded
from ..step5.models import Target
from ..step5.pipeline import MAX_METADATA_BYTES, digest, encoded
from ..urls import normalize_input
from . import storage
from .models import Aggregates, LiteralName, SourceInspection, SourceObservation


def literal_names(text: str, target: Target) -> list[LiteralName]:
    normalized, positions = folded(text)
    result = []
    for name in target.aliases:
        needle = folded(name)[0]
        if not needle:
            continue
        for match in re.finditer(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized):
            start, end = positions[match.start()][0], positions[match.end() - 1][1]
            result.append(
                LiteralName(
                    name=name,
                    quote=text[start:end],
                    start=start,
                    end=end,
                    ambiguous=name in target.ambiguous_aliases,
                )
            )
            if len(result) >= 100:
                return result
    return result


def selection_sha(aggregate: Aggregates) -> str:
    return digest(
        encoded(
            {
                "target": aggregate.target.model_dump(mode="json"),
                "tasks": [t.model_dump(mode="json") for t in aggregate.source_page_tasks],
                "other_names": [c.name for c in aggregate.competitors],
            }
        )
    )


def other_names(text: str, aggregate: Aggregates) -> list[LiteralName]:
    names = [
        c.name
        for c in sorted(aggregate.competitors, key=lambda c: (-len(c.answer_ids), c.name))[:30]
        if c.identity_scope == "non_target_candidate"
    ]
    if not names:
        return []
    target = aggregate.target.model_copy(update={"aliases": names, "ambiguous_aliases": names})
    return literal_names(text, target)


def validate_export(report: SourceInspection, aggregate: Aggregates) -> None:
    tasks = {t.task_id: t for t in aggregate.source_page_tasks}
    if (
        report.selection_sha256 != selection_sha(aggregate)
        or report.synthetic != aggregate.synthetic
        or len(report.observations) != len(tasks)
        or {o.task_id for o in report.observations} != set(tasks)
    ):
        raise ValueError("source inspection selection differs from this audit")
    for item in report.observations:
        if (
            item.url != tasks[item.task_id].url
            or digest(item.text.encode()) != item.text_sha256
            or item.matches != literal_names(item.text, aggregate.target)
            or item.other_matches != other_names(item.text, aggregate)
        ):
            raise ValueError("source inspection quote or text binding mismatch")
        if item.publication_id:
            if not item.publication_sha256 or (item.status == "observed") != bool(item.text):
                raise ValueError("source inspection lacks evidence binding")
        elif item != unavailable(item.task_id, item.url):
            raise ValueError("unobserved page cannot imply absence")


def observation(
    task_id: str, url: str, directory: Path, aggregate: Aggregates
) -> SourceObservation:
    result = load_step1_result(directory)
    wanted = normalize_input(url).url
    page = next(
        (
            p
            for p in result.crawl.pages
            if normalize_input(p.url).url == wanted and p.main_text is not None
        ),
        None,
    )
    store = EvidenceStore(directory)
    text = ""
    if page and page.main_text:
        records = {r.evidence_id: r for r in result.evidence}
        text = verify_file(store, records[page.main_text.evidence_id]).decode("utf-8")[:20000]
    return SourceObservation(
        task_id=task_id,
        url=url,
        status="observed" if text else "not_observed",
        publication_id=result.run.publication_id,
        publication_sha256=digest(store.read_bytes("run.json", limit=MAX_METADATA_BYTES)),
        text=text,
        text_sha256=digest(text.encode()),
        matches=literal_names(text, aggregate.target),
        other_matches=other_names(text, aggregate),
        limitation=(
            "Буквальные названия в сохранённом фрагменте; другие имена — кандидаты. "
            "Проверяется не более тридцати частых других названий, не их роль или принадлежность."
        ),
    )


def unavailable(task_id: str, url: str) -> SourceObservation:
    return SourceObservation(
        task_id=task_id,
        url=url,
        status="not_observed",
        text_sha256=digest(b""),
        matches=[],
        limitation=(
            "Страница не прочитана в пределах безопасного бюджета; "
            "отсутствие компании не установлено."
        ),
    )


async def inspect_pages(
    aggregate: Aggregates, aggregate_bytes: bytes, output_dir: Path
) -> SourceInspection:
    output = EvidenceStore(output_dir)
    output.prepare(new=True)
    output.atomic_bytes("aggregates.json", aggregate_bytes)
    observations = []
    attempts = 0
    attempts_complete = True
    config = Step0Config(
        step0_deadline_seconds=8.0,
        total_deadline_seconds=12.0,
        step0_max_attempts=3,
        total_max_attempts=6,
    )
    crawl_config = Step1Config(
        source_step0_config_sha256=config.fingerprint(),
        max_selected=1,
        max_sitemap_files=0,
        browser_enabled=False,
        ua_probes_enabled=False,
    )
    for task in aggregate.source_page_tasks[:5]:
        try:
            result = await audit_site(
                task.url, config=config, step1_config=crawl_config, output_dir=output.root / "runs"
            )
            attempts += (
                result.run.budget.total_attempts
                if isinstance(result, Step1Result)
                else result.budget.total_attempts
            )
            observations.append(
                observation(
                    task.task_id,
                    task.url,
                    output.root / "runs" / result.run.publication_id,
                    aggregate,
                )
                if isinstance(result, Step1Result)
                else unavailable(task.task_id, task.url)
            )
        except (GeoAuditError, OSError, ValueError, TimeoutError):
            observations.append(unavailable(task.task_id, task.url))
            attempts_complete = False
    report = SourceInspection(
        source_aggregates_sha256=digest(aggregate_bytes),
        selection_sha256=selection_sha(aggregate),
        synthetic=aggregate.synthetic,
        observations=observations,
        network_attempts=attempts,
        attempt_count_complete=attempts_complete,
    )
    output.atomic_json("source_inspection.json", report.model_dump(mode="json"))
    return report


def verify_inspection(directory: Path) -> SourceInspection:
    from .pipeline import json_model

    source = EvidenceStore(directory)
    data = source.read_bytes("aggregates.json", limit=MAX_METADATA_BYTES)
    aggregate = json_model(data, Aggregates)
    report = json_model(
        source.read_bytes("source_inspection.json", limit=MAX_METADATA_BYTES), SourceInspection
    )
    if report.source_aggregates_sha256 != digest(data):
        raise ValueError("source inspection input changed")
    validate_export(report, aggregate)
    tasks = {t.task_id: t for t in aggregate.source_page_tasks}
    if {o.task_id for o in report.observations} != set(tasks):
        raise ValueError("source inspection does not cover selected tasks")
    for item in report.observations:
        if item.url != tasks[item.task_id].url or digest(item.text.encode()) != item.text_sha256:
            raise ValueError("source page binding mismatch")
        if item.publication_id:
            replay = observation(
                item.task_id, item.url, source.root / "runs" / item.publication_id, aggregate
            )
            if replay != item:
                raise ValueError("source observation differs from verified page")
        elif item != unavailable(item.task_id, item.url):
            raise ValueError("unobserved source cannot imply an absent company")
    return report


def main(argv: list[str] | None = None) -> int:
    from .pipeline import verify_publication

    parser = argparse.ArgumentParser(
        description="Явная проверка выбранных внешних страниц без моделей."
    )
    parser.add_argument("--step9-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify and (args.step9_dir or args.output_dir or args.allow_network):
        parser.error("--verify используется отдельно")
    if not args.verify and not (args.step9_dir and args.output_dir and args.allow_network):
        parser.error("нужны --step9-dir, новая --output-dir и явный --allow-network")
    try:
        if args.verify:
            report = verify_inspection(args.verify)
        else:
            storage.separate(args.step9_dir, args.output_dir)
            aggregate = verify_publication(args.step9_dir)
            data = storage.read(args.step9_dir / "aggregates.json")
            # No subprocesses are used by this network worker. Selector preserves
            # bounded TLS cancellation on Windows; existing crawl code is unchanged.
            if sys.platform == "win32":
                with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                    report = runner.run(inspect_pages(aggregate, data, args.output_dir))
            else:
                report = asyncio.run(inspect_pages(aggregate, data, args.output_dir))
        print(
            encoded(
                {
                    "status": "complete",
                    "pages": len(report.observations),
                    "network_attempts": report.network_attempts,
                    "model_calls": 0,
                }
            ).decode()
        )
        return 0
    except (GeoAuditError, OSError, ValueError, RecursionError):
        print("Не удалось проверить источники. Сохранённый аудит не изменён.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
