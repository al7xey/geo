"""Local evidence utilities, atomic publication, confined paths and hash verification."""

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from .errors import problem
from .models import EvidenceFile, EvidenceRecord, SiteValidationResult


class EvidenceStore:
    def __init__(self, run_directory: Path) -> None:
        self.root = run_directory.resolve()

    def prepare(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.root) as stream:
                stream.write(b"write-check")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise problem(
                "OUTPUT_WRITE_FAILED", "output", "Cannot prepare evidence directory."
            ) from None

    def _path(self, relative: str) -> Path:
        # Reuse contract validation for Windows drive paths, backslashes and traversal.
        EvidenceFile(
            path=relative,
            sha256="0" * 64,
            size_bytes=0,
            representation="attempt_observation",
            complete=True,
        )
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence path escapes run directory.")
        return path

    def write_blob(
        self,
        data: bytes,
        *,
        representation: str,
        complete: bool,
    ) -> EvidenceFile:
        relative = f"raw/{uuid.uuid4().hex}.bin"
        try:
            path = self._path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Never derive filename from URL or overwrite another observation.
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return EvidenceFile.model_validate(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "representation": representation,
                    "complete": complete,
                }
            )
        except OSError:
            raise problem("OUTPUT_WRITE_FAILED", "output", "Cannot save evidence bytes.") from None

    def verify(self, record: EvidenceRecord) -> None:
        for item in record.files:
            try:
                path = self._path(item.path)
                if path.stat().st_size != item.size_bytes:
                    raise ValueError("size mismatch")
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != item.sha256:
                    raise ValueError("hash mismatch")
            except (OSError, ValueError):
                raise problem(
                    "INPUT_ARTIFACT_INVALID", "input", "Evidence is missing or corrupt."
                ) from None

    def atomic_json(self, name: str, value: object) -> None:
        path = self._path(name)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp"
            ) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            raise problem("OUTPUT_WRITE_FAILED", "output", "Cannot publish result JSON.") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def publish(self, result: SiteValidationResult) -> None:
        """Commit evidence manifest before site.json; refuse dangling references."""
        result = SiteValidationResult.model_validate_json(result.model_dump_json())
        for record in result.evidence:
            self.verify(record)
        self.atomic_json("manifest.json", [e.model_dump(mode="json") for e in result.evidence])
        self.atomic_json("site.json", result.model_dump(mode="json"))
