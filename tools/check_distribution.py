"""Inspect wheel/sdist names and required runtime data without extracting archives."""

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def check_names(names: list[str], *, wheel: bool) -> int:
    required = {
        "geo_audit/py.typed",
        "geo_audit/data/public_suffix_list.dat",
        "geo_audit/data/README.md",
        "geo_audit/api.py",
        "geo_audit/cli.py",
    }
    seen = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe archive path")
        relative = path.as_posix() if wheel else PurePosixPath(*path.parts[1:]).as_posix()
        if relative in seen:
            raise ValueError("Duplicate archive member")
        seen.add(relative)
        package = relative if wheel else relative.removeprefix("src/")
        if package.startswith("geo_audit/"):
            parts = PurePosixPath(package).parts
            if any(
                part.startswith(".") or part in {"__pycache__", "tmp", "tests"} for part in parts
            ):
                raise ValueError("Non-runtime file in package")
            if not (package.endswith(".py") or package in required):
                raise ValueError("Unexpected runtime data")
            required.discard(package)
        elif wheel and ".dist-info/" in relative:
            if relative.split(".dist-info/", 1)[1] not in {
                "METADATA",
                "WHEEL",
                "entry_points.txt",
                "RECORD",
            }:
                raise ValueError("Unexpected wheel metadata")
        elif not wheel and relative in {
            "pyproject.toml",
            "README.md",
            "uv.lock",
            ".python-version",
            ".gitignore",
            "PKG-INFO",
        }:
            pass
        else:
            raise ValueError("Non-runtime archive member")
    if required:
        raise ValueError("Required code, typing or PSL provenance is missing")
    return len(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheels = list(args.directory.glob("*.whl"))
    sources = list(args.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("directory must contain exactly one wheel and one sdist")
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_count = check_names(archive.namelist(), wheel=True)
    with tarfile.open(sources[0], "r:gz") as archive:
        members = archive.getmembers()
        if not all(member.isfile() for member in members):
            raise ValueError("sdist must contain only ordinary files")
        source_count = check_names([member.name for member in members], wheel=False)
    print(f"Verified wheel ({wheel_count} files) and sdist ({source_count} files).")


if __name__ == "__main__":
    main()
