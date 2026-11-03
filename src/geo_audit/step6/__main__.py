"""Local step 6 CLI; no model SDKs or keys are required."""

import argparse
import json
from pathlib import Path

from ..errors import GeoAuditError
from .pipeline import run_step6, verify_publication


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline step 6 rule annotation; APIs disabled.")
    parser.add_argument("--step5-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if args.step5_dir or args.output_dir:
            parser.error("use --verify alone")
    elif args.step5_dir is None or args.output_dir is None:
        parser.error("--step5-dir and --output-dir required")
    try:
        result = (
            verify_publication(args.verify)
            if args.verify
            else run_step6(args.step5_dir, args.output_dir)
        )
    except (OSError, ValueError, RecursionError, GeoAuditError):
        print(json.dumps({"status": "failed", "error": "Invalid source or publication directory."}))
        return 2
    print(result.model_dump_json(indent=2))
    # Pending jobs are an expected offline result, not a failed command.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
