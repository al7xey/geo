"""Small standard-library CLI. JSON on stdout, safe structured errors on stderr."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .api import validate_site
from .config import Step0Config
from .errors import ErrorDetail, GeoAuditError
from .evidence import EvidenceStore
from .handoff import verify_step1_input
from .models import Step1Input
from .schemas import MODELS, schema_for, write_schemas
from .urls import normalize_host


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="geo-audit",
        description="GEO step 0 contracts; live network runner not yet implemented.",
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("config-defaults", help="Print versioned default configuration as JSON")
    schema = sub.add_parser("schema", help="Print a schema or generate all schema files")
    schema.add_argument("model", nargs="?", choices=MODELS)
    schema.add_argument("--output-dir", type=Path)
    validate = sub.add_parser("validate", help="Validate JSON shape and Python contract invariants")
    validate.add_argument("model", choices=MODELS)
    validate.add_argument("file", type=Path)
    handoff = sub.add_parser(
        "check-handoff", help="Also verify files, hashes and current shared deadline"
    )
    handoff.add_argument("file", type=Path)
    handoff.add_argument("--run-dir", required=True, type=Path)
    run = sub.add_parser("step0", help="Reserved command; exits 4, does not perform network I/O")
    run.add_argument("url")
    run.add_argument("--config", type=Path)
    run.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Exact explicitly allowed host (reserved for the future runner)",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        if args.command == "config-defaults":
            print(Step0Config().model_dump_json(indent=2))
        elif args.command == "schema":
            if args.output_dir is not None:
                write_schemas(args.output_dir)
            elif args.model is not None:
                print(json.dumps(schema_for(args.model), indent=2))
            else:
                arguments.error("schema needs MODEL or --output-dir")
        elif args.command == "validate":
            MODELS[args.model].model_validate_json(args.file.read_bytes())
            print(json.dumps({"valid": True, "model": args.model}))
        elif args.command == "check-handoff":
            value = Step1Input.model_validate_json(args.file.read_bytes())
            verify_step1_input(value, EvidenceStore(args.run_dir))
            print(json.dumps({"valid": True, "files_verified": True}))
        elif args.command == "step0":
            config = (
                Step0Config.model_validate_json(args.config.read_bytes())
                if args.config
                else Step0Config()
            )
            if args.allow_host:
                try:
                    hosts = [normalize_host(h) for h in args.allow_host]
                except ValueError:
                    arguments.error("--allow-host requires a valid hostname")
                data = config.model_dump(mode="json")
                data["allowed_hosts"] = list(dict.fromkeys([*config.allowed_hosts, *hosts]))
                config = Step0Config.model_validate_json(json.dumps(data))
            asyncio.run(validate_site(args.url, config))
        return 0
    except GeoAuditError as error:
        print(error.detail.model_dump_json(), file=sys.stderr)
        return 4 if error.detail.category == "implementation" else 1
    except ValidationError as error:
        # Pydantic's default str(error) may reveal complete input URLs/secrets.
        detail = ErrorDetail(
            code="INPUT_ARTIFACT_INVALID",
            category="input",
            message=f"Contract validation failed ({error.error_count()} issues).",
            retryable=False,
            attempt_id=None,
        )
        print(detail.model_dump_json(), file=sys.stderr)
        return 2
    except OSError:
        detail = ErrorDetail(
            code="INPUT_ARTIFACT_INVALID",
            category="input",
            message="File I/O failed.",
            retryable=False,
            attempt_id=None,
        )
        print(detail.model_dump_json(), file=sys.stderr)
        return 2
