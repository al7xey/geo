"""Small standard-library CLI. JSON on stdout, safe structured errors on stderr."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from .api import validate_site
from .config import Step0Config
from .errors import ErrorDetail, GeoAuditError, error_detail
from .handoff import load_step1_input
from .models import Step1Input
from .redaction import public_summary
from .schemas import MODELS, schema_for, write_schemas
from .urls import normalize_host


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default message may echo a rejected URL, credentials or query.
        self.print_usage(sys.stderr)
        self.exit(2, "geo-audit: invalid arguments; use --help.\n")


def parser() -> argparse.ArgumentParser:
    result = _SafeArgumentParser(
        prog="geo-audit",
        description="GEO step 0: validate a public site and persist verifiable evidence.",
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
    load = sub.add_parser(
        "load-step1", help="Verify a saved run and its remaining budget; no network"
    )
    load.add_argument("run_dir", type=Path)
    run = sub.add_parser("step0", help="Run bounded site validation; no paid APIs or step 1 crawl")
    run.add_argument("url")
    run.add_argument("--config", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("output/runs"))
    run.add_argument("--http-fallback", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument("--parent-run-id")
    run.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Exact explicitly allowed host; does not permit private IPs or other subdomains",
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
            loaded = load_step1_input(args.run_dir)
            if loaded.contract != value:
                raise GeoAuditError(error_detail("INPUT_ARTIFACT_INVALID"))
            print(json.dumps({"valid": True, "files_verified": True}))
        elif args.command == "load-step1":
            loaded = load_step1_input(args.run_dir)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "run_id": loaded.contract.site.run_id,
                        "status": loaded.contract.site.status,
                        "html_bytes": len(loaded.html),
                        "remaining_seconds": loaded.budget.remaining_seconds(step=1),
                        "remaining_attempts": loaded.budget.config.total_max_attempts
                        - loaded.budget.snapshot().total_attempts,
                        "crawl_permission": "unknown",
                    }
                )
            )
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
            if args.http_fallback is not None:
                data = config.model_dump(mode="json")
                data["allow_http_fallback"] = args.http_fallback
                config = Step0Config.model_validate_json(json.dumps(data))
            result = asyncio.run(
                validate_site(
                    args.url, config, output_dir=args.output_dir, parent_run_id=args.parent_run_id
                )
            )
            summary = public_summary(
                status=result.status,
                can_enter_step1=result.can_enter_step1,
                effective_url=result.effective_url,
                reasons=result.reason_codes,
                next_action=result.next_action,
                run_id=result.run_id,
            )
            summary["run_directory"] = str(result._run_directory)
            print(json.dumps(summary, ensure_ascii=False))
            return (
                5
                if "OUTPUT_WRITE_FAILED" in result.reason_codes
                else {"ok": 0, "partial": 3, "needs_input": 4, "failed": 1}[result.status]
            )
        return 0
    except GeoAuditError as error:
        print(error.detail.model_dump_json(), file=sys.stderr)
        return (
            5 if error.detail.category == "output" else 2 if error.detail.category == "input" else 1
        )
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
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(error_detail("NETWORK_CANCELLED").model_dump_json(), file=sys.stderr)
        return 130
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
    except Exception:
        # Presentation boundary only; library programming errors still propagate unchanged.
        print(error_detail("IMPLEMENTATION_BUG").model_dump_json(), file=sys.stderr)
        return 70
