"""Единая точка запуска без изменений кода шагов 0-4.

prepare: исходная программа шагов 0-2 (требует подключения к моделям).
review: исходная страница проверки вопросов шага 3.
collect: исходный сбор шага 4, затем автоматическая обработка 5-8.
analyze: только обработка уже сохранённых ответов 5-8, без сети и оплаты.
"""

import argparse
import os

# Fixed local CLI entry points, never shell commands from answers.
import subprocess  # nosec B404
import sys
from pathlib import Path

from geo_audit.app_web.model_gateway import load_environment
from geo_audit.errors import GeoAuditError
from geo_audit.step5.collector import validate_approval
from geo_audit.step5.pipeline import MAX_METADATA_BYTES
from geo_audit.step5.workflow import main as analyze
from geo_audit.step5.workflow import read_input, validate_layout

TOOLS = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        print("Подробнее: docs/INTEGRATION_0_8.md")
        return 0
    command, rest = args[0], args[1:]
    root = TOOLS.parent
    load_environment(root / ".env")
    load_environment(root / "private/.env")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key.startswith("AQ."):
        os.environ.setdefault("VERTEX_API_KEY", gemini_key)
    if command == "analyze":
        return analyze(rest)
    if command in {"prepare", "review"}:
        script = "geo_pipeline.py" if command == "prepare" else "step3_review.py"
        # Fixed local script; arguments come directly from the operator, never from answers.
        return subprocess.run(  # nosec B603
            [sys.executable, "-X", "utf8", str(TOOLS / script), *rest], check=False
        ).returncode
    if command != "collect":
        raise SystemExit("Ожидалась команда prepare, review, collect или analyze.")
    parser = argparse.ArgumentParser(description="Собрать ответы шага 4 и выполнить шаги 5-8.")
    parser.add_argument("--step4-input", required=True, type=Path)
    parser.add_argument("--answers-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--profile", type=Path)
    known, collector_args = parser.parse_known_args(rest)
    if any(a == "--out" or a.startswith("--out=") for a in collector_args):
        parser.error("папка сбора задаётся только через --answers-dir")
    if "--dry-run" not in collector_args:
        try:
            profile = known.profile or known.step4_input.parent / "profile.json"
            validate_layout(known.step4_input, known.answers_dir, known.output_dir, profile)
            validate_approval(
                read_input(known.step4_input, MAX_METADATA_BYTES),
                read_input(profile, MAX_METADATA_BYTES),
            )
        except (ValueError, OSError, GeoAuditError):
            print("До платного сбора: проверьте вопросы, профиль и новую папку результата.")
            return 2
    # Budget, repeat and system options pass through unchanged to the original collector.
    call = [
        sys.executable,
        "-X",
        "utf8",
        str(TOOLS / "step4_collect.py"),
        str(known.step4_input),
        "--out",
        str(known.answers_dir),
        *collector_args,
    ]
    code = subprocess.run(call, check=False).returncode  # nosec B603
    if code or "--dry-run" in collector_args:
        return code
    values = [
        "--step4-input",
        str(known.step4_input),
        "--answers-dir",
        str(known.answers_dir),
        "--output-dir",
        str(known.output_dir),
    ]
    if known.profile:
        values += ["--profile", str(known.profile)]
    return analyze(values)


if __name__ == "__main__":
    raise SystemExit(main())
