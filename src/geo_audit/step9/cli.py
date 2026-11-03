"""Small consistent CLIs for the four final offline stages."""

import argparse
import json
from pathlib import Path

from ..errors import GeoAuditError
from ..step9 import storage


def main(stage: int, argv: list[str] | None = None) -> int:
    from ..step9 import pipeline as step9
    from ..step10 import pipeline as step10
    from ..step11 import pipeline as step11
    from ..step12 import pipeline as step12
    from .models import MetricPolicy

    parser = argparse.ArgumentParser(description=f"Шаг {stage}, локальная обработка без API.")
    parser.add_argument(f"--step{stage - 1}-dir", dest="source", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    if stage == 9:
        parser.add_argument("--site-dir", type=Path)
        parser.add_argument("--policy", type=Path)
        parser.add_argument("--source-checks-dir", type=Path)
    if stage == 10:
        parser.add_argument("--results", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        if any(v is not None for key, v in vars(args).items() if key != "verify"):
            parser.error("--verify используется отдельно")
    elif args.source is None or args.output_dir is None:
        parser.error("нужны папка предыдущего шага и новая --output-dir")
    try:
        if args.verify:
            modules = {9: step9, 10: step10, 11: step11, 12: step12}
            modules[stage].verify_publication(args.verify)
        elif stage == 9:
            policy = (
                step9.json_model(storage.read(args.policy), MetricPolicy) if args.policy else None
            )
            step9.run_step9(
                args.source,
                args.output_dir,
                site_dir=args.site_dir,
                policy=policy,
                source_checks_dir=args.source_checks_dir,
            )
        elif stage == 10:
            step10.run_step10(args.source, args.output_dir, results_file=args.results)
        elif stage == 11:
            step11.run_step11(args.source, args.output_dir)
        else:
            step12.run_step12(args.source, args.output_dir)
        print(
            json.dumps(
                {
                    "status": "verified" if args.verify else "published",
                    "stage": stage,
                    "directory": str(args.verify or args.output_dir),
                    "model_calls": 0,
                    "network_requests": 0,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RecursionError, GeoAuditError):
        print("Шаг не завершён. Проверьте входные данные и новую папку результата.")
        return 2
