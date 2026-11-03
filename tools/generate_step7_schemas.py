"""Generate step 7 schemas; no provider SDKs, keys or network calls."""

import json
from pathlib import Path

from geo_audit.step7.models import SCHEMA_MODELS, SCHEMA_VERSIONS, Reviewer, ReviewResult


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas" / "step7"
    root.mkdir(parents=True, exist_ok=True)
    for model in SCHEMA_MODELS:
        version = SCHEMA_VERSIONS.get(model.__name__, "1.0")
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step7:{model.__name__}:{version}",
        }
        (root / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    examples = root.parents[1] / "examples" / "contracts" / "step7"
    examples.mkdir(parents=True, exist_ok=True)
    for name, status in (
        ("review-result.synthetic.json", "ok"),
        ("review-error.synthetic.json", "error"),
    ):
        result = ReviewResult(
            job_id="unassigned-synthetic-example",
            text_sha256="0" * 64,
            reviewer=Reviewer(kind="synthetic", provider="fixture-only"),
            status=status,
            target_presence="no" if status == "ok" else None,
            target_recommended="no" if status == "ok" else None,
            target_discouraged="no" if status == "ok" else None,
            error_code="rate_limit" if status == "error" else None,
        )
        (examples / name).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
