"""Regenerate step 5 JSON schemas; does not run an audit or write private data."""

import json
from pathlib import Path

from geo_audit.step5.collector import (
    AdapterManifest,
    AdapterPolicy,
    ApprovedProfile,
    CollectorAnswer,
    Step4Input,
)
from geo_audit.step5.models import SCHEMA_MODELS
from geo_audit.step5.workflow import ChainManifest


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas" / "step5"
    root.mkdir(parents=True, exist_ok=True)
    for model in (
        *SCHEMA_MODELS,
        AdapterManifest,
        AdapterPolicy,
        ApprovedProfile,
        CollectorAnswer,
        Step4Input,
        ChainManifest,
    ):
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step5:{model.__name__}:1.0",
        }
        (root / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
