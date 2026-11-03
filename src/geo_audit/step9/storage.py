"""Bounded new-only publications shared by steps 9-12; no network or model I/O."""

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field

from ..contracts import SHA256, Contract
from ..evidence import MAX_TOTAL_BYTES, EvidenceStore
from ..secure_io import pinned_directory
from ..step5.models import PublicationFile
from ..step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded, unique_json

Builder = Callable[[dict[str, bytes]], dict[str, bytes]]


class Manifest(Contract):
    schema_version: Literal["tail-publication-1.0"] = "tail-publication-1.0"
    stage: Literal[9, 10, 11, 12]
    committed: Literal[True] = True
    source_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=100)


def read(path: Path, limit: int = MAX_METADATA_BYTES) -> bytes:
    return EvidenceStore(path.parent).read_bytes(path.name, limit=limit)


def separate(source: Path, output: Path) -> None:
    a, b = EvidenceStore(source).root, EvidenceStore(output).root
    if a == b or a in b.parents or b in a.parents:
        raise ValueError("input and output directories must be separate")


def bounded(files: dict[str, bytes]) -> None:
    if len(files) > 100 or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise ValueError("publication total budget exceeded")
    for name, data in files.items():
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or path.as_posix() != name
            or len(data) > (MAX_METADATA_BYTES if name.endswith(".json") else MAX_ANSWERS_BYTES)
        ):
            raise ValueError("invalid publication file or size")


def artifacts(inputs: dict[str, bytes], outputs: dict[str, bytes]) -> dict[str, bytes]:
    result = {f"input/{name}": data for name, data in inputs.items()}
    if set(result).intersection(outputs) or "manifest.json" in outputs:
        raise ValueError("reserved output name")
    result.update(outputs)
    bounded(result)
    return result


def fingerprint(inputs: dict[str, bytes]) -> str:
    return digest(encoded({name: digest(data) for name, data in inputs.items()}))


def publish(
    inputs: dict[str, bytes], output: Path, stage: Literal[9, 10, 11, 12], builder: Builder
) -> dict[str, bytes]:
    bounded(inputs)
    files = builder(inputs)
    bounded(files)
    store = EvidenceStore(output)
    store.prepare(new=True)
    with pinned_directory(store.root):
        for name, data in files.items():
            with pinned_directory((store.root / name).parent, create=True):
                store.atomic_bytes(name, data)
        store.atomic_json(
            "manifest.json",
            Manifest(
                stage=stage,
                source_fingerprint=fingerprint(inputs),
                files={
                    name: PublicationFile(sha256=digest(data), size_bytes=len(data))
                    for name, data in files.items()
                },
            ).model_dump(mode="json"),
        )
    return files


def verify(
    directory: Path, stage: Literal[9, 10, 11, 12], names: tuple[str, ...], builder: Builder
) -> dict[str, bytes]:
    store = EvidenceStore(directory)
    manifest = Manifest.model_validate(
        unique_json(store.read_bytes("manifest.json", limit=MAX_METADATA_BYTES))
    )
    if manifest.stage != stage:
        raise ValueError("wrong publication stage")
    # Read fixed names only; a supplied manifest never chooses paths to open.
    inputs = {name: store.read_bytes(f"input/{name}", limit=MAX_ANSWERS_BYTES) for name in names}
    bounded(inputs)
    expected = builder(inputs)
    if manifest.source_fingerprint != fingerprint(inputs) or set(manifest.files) != set(expected):
        raise ValueError("publication manifest mismatch")
    for name, data in expected.items():
        reference = manifest.files[name]
        if (
            reference.sha256 != digest(data)
            or reference.size_bytes != len(data)
            or store.read_bytes(name, limit=len(data)) != data
        ):
            raise ValueError("publication differs from replay")
    return expected


def bind(inputs: dict[str, bytes], stage: int, names: tuple[str, ...]) -> None:
    manifest = Manifest.model_validate(unique_json(inputs["source-manifest.json"]))
    if manifest.stage != stage:
        raise ValueError("wrong captured source stage")
    for name in names:
        ref = manifest.files.get(name)
        if ref is None or ref.sha256 != digest(inputs[name]) or ref.size_bytes != len(inputs[name]):
            raise ValueError("captured source file mismatch")
