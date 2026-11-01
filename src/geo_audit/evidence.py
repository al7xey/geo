"""Handle-confined, bounded reads and atomic evidence-first publication."""

import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from .errors import problem
from .models import EvidenceFile, EvidenceRecord, SiteValidationResult
from .secure_io import open_confined, pinned_directory, restrict_private_directory

MAX_JSON_BYTES = 16 * 1024 * 1024


class EvidenceStore:
    def __init__(self, run_directory: Path) -> None:
        self.root = Path(os.path.abspath(run_directory))  # Do not resolve/follow links.

    def prepare(self, *, new: bool = False) -> None:
        try:
            if new and self.root.exists():
                raise OSError("run directory must be new")
            with pinned_directory(self.root.parent, create=True) as parent_fd:
                if new:
                    if parent_fd is None:
                        self.root.mkdir(mode=0o700)
                    else:
                        os.mkdir(self.root.name, mode=0o700, dir_fd=parent_fd)
                    restrict_private_directory(self.root)
            with pinned_directory(self.root, create=not new) as root_fd:
                probe = self.root / (uuid.uuid4().hex + ".tmp")
                try:
                    with open_confined(probe, writing=True) as stream:
                        stream.write(b"write-check")
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    if root_fd is None:
                        probe.unlink(missing_ok=True)
                    else:
                        try:
                            os.unlink(probe.name, dir_fd=root_fd)
                        except FileNotFoundError:
                            pass
                with pinned_directory(self.root / "raw", create=True):
                    pass
        except OSError:
            raise problem(
                "OUTPUT_WRITE_FAILED", "output", "Cannot prepare a new evidence directory."
            ) from None

    def _path(self, relative: str) -> Path:
        EvidenceFile(
            path=relative,
            sha256="0" * 64,
            size_bytes=0,
            representation="attempt_observation",
            complete=True,
        )
        return self.root / relative

    @contextmanager
    def open_read(self, relative: str) -> Iterator[BinaryIO]:
        try:
            with open_confined(self._path(relative)) as stream:
                yield stream
        except (OSError, ValueError, ValidationError):
            raise problem(
                "INPUT_ARTIFACT_INVALID", "input", "Artifact path or file is invalid."
            ) from None

    def read_bytes(self, relative: str, *, limit: int) -> bytes:
        with self.open_read(relative) as stream:
            if os.fstat(stream.fileno()).st_size > limit:
                raise problem("INPUT_ARTIFACT_INVALID", "input", "Artifact exceeds its size limit.")
            value = stream.read(limit + 1)
            if len(value) > limit:
                raise problem("INPUT_ARTIFACT_INVALID", "input", "Artifact grew beyond its limit.")
            return value

    def read_json(self, relative: str) -> object:
        try:

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result

            return json.loads(
                self.read_bytes(relative, limit=MAX_JSON_BYTES), object_pairs_hook=unique
            )
        except (ValueError, RecursionError):
            raise problem(
                "INPUT_ARTIFACT_INVALID", "input", "Artifact is not valid unique-key JSON."
            ) from None

    def _atomic_bytes(self, relative: str, data: bytes) -> None:
        path = self._path(relative)
        temporary = path.parent / (uuid.uuid4().hex + ".tmp")
        try:
            with pinned_directory(path.parent) as directory_fd:
                try:
                    with open_confined(temporary, writing=True) as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Source/target names live under pinned no-reparse parents.
                    if directory_fd is None:  # Windows parents cannot be renamed while pinned.
                        os.replace(temporary, path)
                    else:
                        os.replace(
                            temporary.name,
                            path.name,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                        )
                finally:
                    if directory_fd is None:
                        temporary.unlink(missing_ok=True)
                    else:
                        try:
                            os.unlink(temporary.name, dir_fd=directory_fd)
                        except FileNotFoundError:
                            pass
        except OSError:
            raise problem(
                "OUTPUT_WRITE_FAILED", "output", "Cannot atomically publish artifact."
            ) from None

    def write_blob(self, data: bytes, *, representation: str, complete: bool) -> EvidenceFile:
        relative = f"raw/{uuid.uuid4().hex}.bin"
        self._atomic_bytes(relative, data)
        return EvidenceFile.model_validate(
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "representation": representation,
                "complete": complete,
            }
        )

    def verify(self, record: EvidenceRecord) -> None:
        for item in record.files:
            if not item.path.startswith("raw/") or len(Path(item.path).parts) != 2:
                raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence must be inside raw/.")
            with self.open_read(item.path) as stream:
                if os.fstat(stream.fileno()).st_size != item.size_bytes:
                    raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence size mismatch.")
                digest = hashlib.sha256()
                remaining = item.size_bytes
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence was truncated.")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise problem(
                        "INPUT_ARTIFACT_INVALID", "input", "Evidence grew beyond its size."
                    )
                if digest.hexdigest() != item.sha256:
                    raise problem(
                        "INPUT_ARTIFACT_INVALID", "input", "Evidence is corrupt: hash mismatch."
                    )

    def atomic_json(self, name: str, value: object) -> None:
        data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        if len(data) > MAX_JSON_BYTES:
            raise problem("OUTPUT_WRITE_FAILED", "output", "Metadata size limit exceeded.")
        self._atomic_bytes(name, data)

    def checkpoint(
        self, attempts: object, evidence: tuple[EvidenceRecord, ...], *, state: str
    ) -> None:
        self.atomic_json("raw/manifest.json", [e.model_dump(mode="json") for e in evidence])
        self.atomic_json("journal.json", {"state": state, "observations": attempts})

    def publish(self, result: SiteValidationResult) -> None:
        result = SiteValidationResult.model_validate_json(result.model_dump_json())
        for record in result.evidence:
            self.verify(record)
        self.atomic_json("raw/manifest.json", [e.model_dump(mode="json") for e in result.evidence])
        self.atomic_json("config.json", result.config.model_dump(mode="json"))
        self.atomic_json("budget.json", result.budget.model_dump(mode="json"))
        self.atomic_json("site.json", result.model_dump(mode="json"))  # Commit marker, last.

    def verified_bytes(self, file: EvidenceFile, *, limit: int) -> bytes:
        value = self.read_bytes(file.path, limit=limit)
        if len(value) != file.size_bytes or hashlib.sha256(value).hexdigest() != file.sha256:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence is missing or corrupt.")
        return value
