"""Generate the versioned step 6 schemas without running models or audits."""

import json
from pathlib import Path

from geo_audit.step6.models import SCHEMA_MODELS


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas" / "step6"
    root.mkdir(parents=True, exist_ok=True)
    for model in SCHEMA_MODELS:
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step6:{model.__name__}:1.1",
        }
        (root / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
