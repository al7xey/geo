"""JSON Schema generation from the exact same models used by the Python API."""

import json
from pathlib import Path

from .config import Step0Config
from .contracts import Contract
from .errors import ErrorDetail
from .models import (
    BudgetSnapshot,
    EvidenceRecord,
    FetchAttempt,
    RedirectHop,
    SiteValidationResult,
    Step1Input,
)

MODELS: dict[str, type[Contract]] = {
    "Step0Config": Step0Config,
    "SiteValidationResult": SiteValidationResult,
    "FetchAttempt": FetchAttempt,
    "RedirectHop": RedirectHop,
    "EvidenceRecord": EvidenceRecord,
    "BudgetSnapshot": BudgetSnapshot,
    "Step1Input": Step1Input,
    "ErrorDetail": ErrorDetail,
}


def schema_for(name: str) -> dict[str, object]:
    schema = MODELS[name].model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    # A stable URN, not a promise that a web-hosted schema registry exists.
    schema["$id"] = f"urn:geo-audit:{name}:1.1.0"
    return schema


def write_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in MODELS:
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema_for(name), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
