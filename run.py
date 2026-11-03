"""Запуск проекта на Windows, macOS и Linux без WSL и активации окружения."""

import argparse
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMANDS = ("demo", "analyze", "verify", "prepare", "review", "collect", "serve", "audit")


def environment_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def call(arguments: list[str]) -> int:
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run(arguments, cwd=ROOT, env=env, check=False).returncode  # nosec B603


def setup() -> int:
    if sys.version_info < (3, 12):  # noqa: UP036 - this installer also runs before setup
        print("Установите Python 3.12 или новее.")
        return 2
    bootstrap = environment_python(ROOT / ".bootstrap")
    if not bootstrap.is_file():
        code = call([sys.executable, "-m", "venv", str(ROOT / ".bootstrap")])
        if code:
            return code
    code = call([str(bootstrap), "-m", "pip", "install", "uv==0.9.7"])
    if code:
        return code
    return call([str(bootstrap), "-m", "uv", "sync", "--locked", "--no-dev", "--python", "3.12"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("setup", *COMMANDS),
        help=(
            "setup — установка; demo — учебные ответы без API; analyze — шаги 5–8; "
            "verify — проверка результата; prepare/review/collect — шаги 0–4; "
            "serve/audit — техническая проверка сайта"
        ),
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "setup":
        if args.arguments:
            parser.error("setup не принимает дополнительные параметры")
        return setup()
    python = environment_python(ROOT / ".venv")
    if not python.is_file():
        print("Сначала выполните: python run.py setup")
        return 2
    prefix = [str(python), "-X", "utf8"]
    if args.command == "demo":
        return call([*prefix, str(ROOT / "tools" / "offline_demo.py"), *args.arguments])
    if args.command in {"serve", "audit"}:
        return call([*prefix, "-m", "geo_audit", args.command, *args.arguments])
    arguments = args.arguments
    command = args.command
    if command == "verify":
        if len(arguments) != 1:
            parser.error("verify требует путь к папке результата")
        command, arguments = "analyze", ["--verify", arguments[0]]
    return call([*prefix, str(ROOT / "tools" / "steps0_8.py"), command, *arguments])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
