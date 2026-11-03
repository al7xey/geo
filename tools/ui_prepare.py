"""Bridge to the unchanged upstream preparation and human approval programs."""

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path

from geo_audit.evidence import EvidenceStore
from geo_audit.step5.pipeline import MAX_METADATA_BYTES

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--description", default="")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--approve", type=Path)
    parser.add_argument("--step2-dir", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "tools"))
    if args.approve:
        module = importlib.import_module("step3_review")
        payload = json.load(sys.stdin)
        result = module.save(args.step2_dir, args.approve, payload)
        EvidenceStore(args.receipt.parent).atomic_json(args.receipt.name, result)
        return 0 if result.get("saved") else 2
    if not args.url:
        parser.error("--url is required for preparation")
    module = importlib.import_module("geo_pipeline")
    base = ROOT / "private/step2_fast"
    before = set(base.iterdir()) if base.exists() else set()
    config = argparse.Namespace(
        url=args.url,
        country="Россия",
        language="русский",
        preset="cheap",
        pages=5,
        description=args.description or None,
        description_file=None,
        no_web=True,
    )
    code = asyncio.run(module.pipeline(config))
    if code not in {0, 3}:
        return code
    created = sorted(set(base.iterdir()) - before)
    created = [p for p in created if p.is_dir() and (p / "step2_report.json").is_file()]
    if len(created) != 1:
        raise ValueError("Preparation did not produce one identifiable publication")
    source = EvidenceStore(created[0])
    report = source.read_json("step2_report.json")
    source.read_bytes("profile.json", limit=MAX_METADATA_BYTES)
    source.read_bytes("prompts.json", limit=MAX_METADATA_BYTES)
    site_dir = ROOT / "private/runs" / created[0].name
    EvidenceStore(args.receipt.parent).atomic_json(
        args.receipt.name,
        {
            "step2_dir": str(created[0]),
            "site_dir": str(site_dir) if (site_dir / "crawl.json").is_file() else None,
            "report": report,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
