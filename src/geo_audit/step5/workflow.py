"""Run steps 5-8 from the unchanged collector's files, without APIs or browsers."""

import argparse
import json
from pathlib import Path
from typing import Literal

from ..contracts import SHA256, Contract
from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..secure_io import pinned_directory
from ..step6 import run_step6
from ..step6 import verify_publication as verify_step6
from ..step7 import run_step7
from ..step7 import verify_publication as verify_step7
from ..step8 import run_step8
from ..step8 import verify_publication as verify_step8
from ..step8.models import EntityCatalog
from . import collector
from .collector import AdapterPolicy
from .pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded, run_step5, unique_json
from .pipeline import verify_publication as verify_step5
from .replay import replay_session

STAGES = ("bundle", "step5", "step6", "step7", "step8")
REPORT = (
    """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Шаги 5-8: результаты</title><style>body{font:18px system-ui;max-width:880px;
margin:48px auto;padding:20px;line-height:1.7}a{color:#087f8c}</style>
<h1>Обработка собранных ответов завершена</h1>
<p>Обработаны результаты шага 4. Исходные ответы не переписывались.
Повторные обращения к нейросетям и сайтам не выполнялись.</p>
<ol start="5"><li><a href="step5/report.html">Найденные названия, ссылки и исходные ответы</a></li>
<li><a href="step6/report.html">Присутствие компаний, рекомендации и точные цитаты</a></li>
<li><a href="step7/report.html">Подготовка независимой проверки и оставшиеся сомнения</a></li>
<li><a href="step8/report.html">Группы названий и типы источников</a></li></ol>
<p>Неясные случаи ожидают проверки. Результат не означает, что независимый аудит
нейросетью уже выполнен. Итоговые проценты относятся к шагу 9.</p>
<p>Признак учебных данных, исключённые системы и заменённые попытки сохранены
в bundle/summary.json. Сведения о компании и вопросах сохранены в исходных снимках.</p>
</html>"""
).encode()


class ChainManifest(Contract):
    schema_version: Literal["steps5-8-chain-1.0"] = "steps5-8-chain-1.0"
    committed: Literal[True] = True
    stages: dict[str, SHA256]
    report_sha256: SHA256
    model_calls: Literal[0] = 0
    network_requests: Literal[0] = 0


def read_input(path: Path, limit: int) -> bytes:
    return EvidenceStore(path.parent).read_bytes(path.name, limit=limit)


def validate_layout(
    step4_input: Path, answers_dir: Path, output_dir: Path, profile: Path
) -> None:
    """Reject invalid destinations before collection or publication, without writing."""
    output = EvidenceStore(output_dir)
    if output.root.exists() or output.root.is_symlink():
        raise ValueError("analysis output must be new")
    directories = (step4_input.parent, profile.parent, answers_dir)
    for directory in directories:
        source = EvidenceStore(directory)
        if (
            source.root == output.root
            or source.root in output.root.parents
            or output.root in source.root.parents
        ):
            raise ValueError("inputs and output must be separate directories")
    # Collector/output folders can be new. Inspect the nearest existing ancestor
    # without creating directories or following symlinks/junctions.
    for directory in (*directories, output.root):
        ancestor = EvidenceStore(directory).root
        while not ancestor.exists() and not ancestor.is_symlink():
            if ancestor.parent == ancestor:
                raise ValueError("local filesystem root is unavailable")
            ancestor = ancestor.parent
        with pinned_directory(ancestor):
            pass


def run(
    step4_input: Path,
    answers_dir: Path,
    output_dir: Path,
    *,
    profile: Path | None = None,
    synthetic: bool = False,
    reviews: Path | None = None,
    catalog: EntityCatalog | None = None,
) -> ChainManifest:
    profile = profile or step4_input.parent / "profile.json"
    validate_layout(step4_input, answers_dir, output_dir, profile)
    output = EvidenceStore(output_dir)
    snapshots = {
        "step4-input.json": read_input(step4_input, MAX_METADATA_BYTES),
        "profile.json": read_input(profile, MAX_METADATA_BYTES),
        "answers.jsonl": EvidenceStore(answers_dir).read_bytes(
            "answers.jsonl", limit=MAX_ANSWERS_BYTES
        ),
        "policy.json": encoded(AdapterPolicy(synthetic=synthetic).model_dump(mode="json")),
    }
    # Fail before creating the result directory if the approved export is inconsistent.
    collector.build_artifacts(snapshots)
    output.prepare(new=True)
    with replay_session(), pinned_directory(output.root):
        collector.publish(snapshots, output.root / "bundle")
        run_step5(output.root / "bundle", output.root / "step5")
        run_step6(output.root / "step5", output.root / "step6")
        run_step7(output.root / "step6", output.root / "step7", results_file=reviews)
        run_step8(output.root / "step7", output.root / "step8", catalog=catalog)
        manifests = {
            stage: digest(output.read_bytes(f"{stage}/manifest.json", limit=MAX_METADATA_BYTES))
            for stage in STAGES
        }
        result = ChainManifest(stages=manifests, report_sha256=digest(REPORT))
        output.atomic_bytes("report.html", REPORT)
        output.atomic_json("chain.json", result.model_dump(mode="json"))
    return result


def verify(directory: Path) -> ChainManifest:
    source = EvidenceStore(directory)
    result = ChainManifest.model_validate(
        unique_json(source.read_bytes("chain.json", limit=MAX_METADATA_BYTES))
    )
    if set(result.stages) != set(STAGES) or result.report_sha256 != digest(REPORT):
        raise ValueError("chain stage set mismatch")
    for stage in STAGES:
        if (
            digest(source.read_bytes(f"{stage}/manifest.json", limit=MAX_METADATA_BYTES))
            != result.stages[stage]
        ):
            raise ValueError("chain publication changed")
    if source.read_bytes("report.html", limit=len(REPORT)) != REPORT:
        raise ValueError("chain report changed")
    with replay_session():
        collector.verify_publication(source.root / "bundle")
        verify_step5(source.root / "step5")
        verify_step6(source.root / "step6")
        verify_step7(source.root / "step7")
        verify_step8(source.root / "step8")
    # Bind the actual stage 5 inputs to this adapter, not merely to their own snapshots.
    for name in ("prompts.json", "target.json", "answers.jsonl"):
        if source.read_bytes(f"bundle/{name}", limit=MAX_ANSWERS_BYTES) != source.read_bytes(
            f"step5/input/{name}", limit=MAX_ANSWERS_BYTES
        ):
            raise ValueError("adapter/step5 handoff mismatch")
    for current, previous in (("step6", "step5"), ("step7", "step6"), ("step8", "step7")):
        captured = source.read_bytes(
            f"{current}/input/{previous}-manifest.json", limit=MAX_METADATA_BYTES
        )
        actual = source.read_bytes(f"{previous}/manifest.json", limit=MAX_METADATA_BYTES)
        if captured != actual:
            raise ValueError("chain stages do not belong to the same publication")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шаги 5-8 по готовым ответам шага 4, без API.")
    parser.add_argument("--step4-input", type=Path)
    parser.add_argument("--answers-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--synthetic", action="store_true", help="явно пометить учебные данные")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if args.synthetic or any(
            v is not None
            for v in (
                args.step4_input,
                args.answers_dir,
                args.output_dir,
                args.profile,
                args.reviews,
                args.catalog,
            )
        ):
            parser.error("--verify используется отдельно")
    elif any(v is None for v in (args.step4_input, args.answers_dir, args.output_dir)):
        parser.error("нужны --step4-input, --answers-dir и --output-dir")
    try:
        catalog = (
            EntityCatalog.model_validate(unique_json(read_input(args.catalog, MAX_METADATA_BYTES)))
            if args.catalog
            else None
        )
        result = (
            verify(args.verify)
            if args.verify
            else run(
                args.step4_input,
                args.answers_dir,
                args.output_dir,
                profile=args.profile,
                synthetic=args.synthetic,
                reviews=args.reviews,
                catalog=catalog,
            )
        )
    except (ValueError, OSError, RecursionError, GeoAuditError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Входные данные или папка результата не прошли проверку.",
                },
                ensure_ascii=True,
            )
        )
        return 2
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
