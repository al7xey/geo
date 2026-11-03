"""Generate or check contracts for the final offline stages; synthetic examples only."""

import argparse
import json
from importlib import import_module
from pathlib import Path

from geo_audit.step9.storage import Manifest
from geo_audit.step10.models import GapResponse
from geo_audit.step12.workflow import TailManifest


def generated(root: Path) -> dict[Path, bytes]:
    files = {}
    for step in range(9, 13):
        module = import_module(f"geo_audit.step{step}.models")
        for model in module.SCHEMA_MODELS:
            schema = model.model_json_schema() | {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": f"urn:geo-audit:step{step}:{model.__name__}:1.0",
            }
            files[root / "schemas" / f"step{step}" / f"{model.__name__}.schema.json"] = (
                json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
            ).encode()
    for model in (Manifest, TailManifest):
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema"
        }
        files[root / "schemas/tail" / f"{model.__name__}.schema.json"] = (
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        ).encode()
    example = GapResponse(
        job_id="synthetic-unassigned-job",
        context_sha256="0" * 64,
        reviewer_kind="synthetic",
        suggestions=[],
    )
    files[root / "examples/contracts/step10/response.synthetic.json"] = (
        example.model_dump_json(indent=2) + "\n"
    ).encode()
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = generated(Path(__file__).resolve().parents[1])
    for path, data in files.items():
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                raise SystemExit(f"Contract differs: {path.name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print(
        f"Checked {len(files)} final-stage contract files."
        if args.check
        else f"Generated {len(files)} final-stage contract files."
    )


if __name__ == "__main__":
    main()
