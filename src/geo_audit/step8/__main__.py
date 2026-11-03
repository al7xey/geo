"""Local-only step 8 CLI; no network or model executor."""

import argparse
import json
from pathlib import Path

from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_METADATA_BYTES, unique_json
from .models import EntityCatalog
from .pipeline import run_step8, verify_publication


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize step 7 entities and sources offline.")
    parser.add_argument("--step7-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if any(v is not None for v in (args.step7_dir, args.output_dir, args.catalog)):
            parser.error("use --verify alone")
    elif args.step7_dir is None or args.output_dir is None:
        parser.error("--step7-dir and --output-dir required")
    try:
        catalog = None
        if args.catalog:
            catalog = EntityCatalog.model_validate(
                unique_json(
                    EvidenceStore(args.catalog.parent).read_bytes(
                        args.catalog.name, limit=MAX_METADATA_BYTES
                    )
                )
            )
        result = (
            verify_publication(args.verify)
            if args.verify
            else run_step8(args.step7_dir, args.output_dir, catalog=catalog)
        )
    except (OSError, ValueError, RecursionError, GeoAuditError):
        print(
            json.dumps(
                {"status": "failed", "error": "Invalid source, catalog or publication directory."}
            )
        )
        return 2
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
