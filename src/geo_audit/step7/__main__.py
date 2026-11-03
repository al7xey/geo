"""Offline step 7 CLI. Import files explicitly; no API execution is implemented."""

import argparse
import json
from pathlib import Path

from ..errors import GeoAuditError
from .models import ReviewPolicy
from .pipeline import run_step7, verify_publication


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/import blind step 7 reviews offline.")
    parser.add_argument("--step6-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--minimum-comparable", type=int)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if any(
            v is not None
            for v in (
                args.step6_dir,
                args.output_dir,
                args.results,
                args.seed,
                args.minimum_comparable,
            )
        ):
            parser.error("use --verify alone")
    elif args.step6_dir is None or args.output_dir is None:
        parser.error("--step6-dir and --output-dir required")
    try:
        if args.verify:
            result = verify_publication(args.verify)
        else:
            values = {}
            if args.seed is not None:
                values["seed"] = args.seed
            if args.minimum_comparable is not None:
                values["minimum_comparable"] = args.minimum_comparable
            result = run_step7(
                args.step6_dir,
                args.output_dir,
                policy=ReviewPolicy.model_validate(values),
                results_file=args.results,
            )
    except (OSError, ValueError, RecursionError, GeoAuditError):
        print(
            json.dumps(
                {"status": "failed", "error": "Invalid source, policy or publication directory."}
            )
        )
        return 2
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
