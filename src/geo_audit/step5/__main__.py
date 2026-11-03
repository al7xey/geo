"""python -m geo_audit.step5 --input-dir <bundle> --output-dir <new-directory>."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from ..errors import GeoAuditError
from .pipeline import run_step5, verify_publication


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline step 5; no models, APIs or URL fetches.")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path, help="Verify and replay a committed result offline")
    args = parser.parse_args(argv)
    if args.verify:
        if args.input_dir or args.output_dir:
            parser.error("use --verify alone")
    elif args.input_dir is None or args.output_dir is None:
        parser.error("both --input-dir and --output-dir are required")
    try:
        result = (
            verify_publication(args.verify)
            if args.verify
            else run_step5(args.input_dir, args.output_dir)
        )
    except (OSError, ValueError, RecursionError, ValidationError, GeoAuditError):
        # Do not echo raw answer text, rejected URLs, credentials or validation input.
        print(json.dumps({"status": "failed", "error": "Invalid input or publication directory."}))
        return 2
    print(result.model_dump_json(indent=2))
    if args.verify:
        return 0
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
