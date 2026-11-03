"""Учебный прогон 480 ответов: без сети, ключей и запуска браузера."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from geo_audit.errors import GeoAuditError
from geo_audit.evidence import EvidenceStore
from geo_audit.step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded
from geo_audit.step5.workflow import run, verify

ROOT = Path(__file__).resolve().parents[1]


def demo(destination: Path) -> Path:
    fixture = EvidenceStore(ROOT / "examples" / "mock_answers_v1" / "input")
    target = json.loads(fixture.read_bytes("target.json", limit=MAX_METADATA_BYTES))
    questions = json.loads(fixture.read_bytes("prompts.json", limit=MAX_METADATA_BYTES))
    prompts = [
        dict(
            id=q["prompt_id"],
            text=q["text"],
            level="ABCD".index(q["group"]) + 1,
            origin="suggested",
            has_brand=not q["visibility_eligible"],
            counts_in_organic_visibility=q["visibility_eligible"],
        )
        for q in questions["prompts"]
        if q["active"]
    ]
    approved = dict(
        schema_version="step4-input-1",
        domain=target["domains"][0],
        language=questions["language"],
        country=questions["country"],
        name_variants=target["aliases"],
        prompts=prompts,
        prompts_sha256=digest(
            json.dumps(prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ),
    )
    profile = dict(
        domain=approved["domain"],
        name_variants=[
            dict(variant=n, ambiguous=n in target["ambiguous_aliases"]) for n in target["aliases"]
        ],
    )
    rows = []
    for line in fixture.read_bytes("answers.jsonl", limit=MAX_ANSWERS_BYTES).splitlines():
        a = json.loads(line)
        rows.append(
            dict(
                answer_id=a["answer_id"],
                prompt_id=a["prompt_id"],
                system=a["provider"],
                model=a["model"],
                repeat=a["repeat_index"],
                status=a["status"] if a["status"] in {"ok", "empty"} else "error",
                text=a["text"],
                sources=[s["url"] for s in a["sources"]],
            )
        )
    # New namespaces only. Original fixtures and previous results stay intact.
    output = EvidenceStore(destination)
    output.prepare(new=True)
    inputs = EvidenceStore(output.root / "input")
    inputs.prepare(new=True)
    inputs.atomic_bytes("step4_input.json", encoded(approved))
    inputs.atomic_bytes("profile.json", encoded(profile))
    inputs.atomic_bytes("answers.jsonl", b"".join(encoded(a) for a in rows))
    result = output.root / "result"
    run(inputs.root / "step4_input.json", inputs.root, result, synthetic=True)
    verify(result)
    return result / "report.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "private" / "demo" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f"),
    )
    args = parser.parse_args()
    try:
        report = demo(args.output_dir)
    except (ValueError, OSError, GeoAuditError):
        print("Учебный прогон не завершён. Проверьте данные и выберите новую папку результата.")
        return 2
    print("Учебные данные. Независимая проверка нейросетью не выполнялась.")
    print(f"Отчёт: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
