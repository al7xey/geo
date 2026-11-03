"""Generate local step 8 schemas and a synthetic explicit catalog example."""

import json
from pathlib import Path

from geo_audit.step8.models import SCHEMA_MODELS, EntityCatalog


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schemas = root / "schemas" / "step8"
    schemas.mkdir(parents=True, exist_ok=True)
    for model in SCHEMA_MODELS:
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step8:{model.__name__}:1.0",
        }
        (schemas / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    examples = root / "examples" / "contracts" / "step8"
    examples.mkdir(parents=True, exist_ok=True)
    catalog = EntityCatalog.model_validate(
        {
            "alias_groups": [
                {
                    "aliases": ["Synthetic Brand", "Синтетик Бренд"],
                    "reason": "Synthetic fixture; not evidence about a real company.",
                }
            ],
            "domain_labels": [
                {
                    "host": "directory.example.test",
                    "kind": "directory",
                    "reason": "Controlled synthetic directory fixture.",
                }
            ],
        }
    )
    (examples / "catalog.synthetic.json").write_text(
        catalog.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
