"""New-only step 1 publication, with a read-only step 0 source and a last commit marker."""

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Self

from pydantic import TypeAdapter, model_validator

from ..contracts import SHA256, Contract
from ..errors import problem
from ..evidence import MAX_FILE_BYTES, MAX_JSON_BYTES, MAX_TOTAL_BYTES, EvidenceStore
from ..http_client import HttpAttempt
from ..models import RedirectHop, SiteValidationResult
from .models import (
    ChecksResult,
    CrawlResult,
    Representation,
    RunResult,
    SourceRef,
    Step1Evidence,
    Step1File,
    Step1Result,
)

ArtifactName = Literal[
    "site.json",
    "source.json",
    "config.json",
    "raw/manifest.json",
    "crawl.json",
    "checks.json",
]
ARTIFACTS: tuple[ArtifactName, ...] = (
    "site.json",
    "source.json",
    "config.json",
    "raw/manifest.json",
    "crawl.json",
    "checks.json",
)


def encoded_json(value: object) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise problem("OUTPUT_WRITE_FAILED", "output", "Metadata size limit exceeded.")
    return data


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactDigest(Contract):
    name: ArtifactName
    sha256: SHA256


class PublicationCommit(RunResult):
    commit_version: Literal["step1-commit-1.0.0"] = "step1-commit-1.0.0"
    artifact_hashes: tuple[ArtifactDigest, ...]

    @model_validator(mode="after")
    def artifact_set(self) -> Self:
        if tuple(a.name for a in self.artifact_hashes) != ARTIFACTS:
            raise ValueError("commit must name each artifact exactly once in canonical order")
        return self


class Step1Store:
    def __init__(self, directory: Path, source: EvidenceStore, site: SiteValidationResult):
        self.io = EvidenceStore(directory)
        # Do not place the publication under its read-only source, including relative paths.
        try:
            inside = os.path.normcase(os.path.commonpath((self.io.root, source.root))) == (
                os.path.normcase(source.root)
            )
        except ValueError:  # Different local drives.
            inside = False
        if inside:
            raise ValueError("step 1 output must be separate from its read-only source")
        self.run_id = site.run_id
        self._sealed = False
        self.records: list[Step1Evidence] = []
        self._journal: list[object] = []
        self.attempt_evidence: dict[str, tuple[str, ...]] = {}
        sizes = {f.path: f.size_bytes for r in site.evidence for f in r.files}
        self._logical_bytes = sum(sizes.values())
        self._physical_bytes = 0
        if self._logical_bytes > MAX_TOTAL_BYTES:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Source evidence cap exceeded.")
        if site.selected_evidence_id is None:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Source has no selected evidence.")
        self.io.prepare(new=True)
        source_site = source.read_bytes("site.json", limit=MAX_JSON_BYTES)
        source_manifest = source.read_bytes(
            "raw/manifest.json" if site.schema_version == "1.2.0" else "manifest.json",
            limit=MAX_JSON_BYTES,
        )
        if SiteValidationResult.model_validate_json(source_site) != site:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Source changed during preparation.")
        self.source = SourceRef(
            run_id=site.run_id,
            site_sha256=digest(source_site),
            manifest_sha256=digest(source_manifest),
            source_step0_config_sha256=site.config_sha256,
            selected_evidence_id=site.selected_evidence_id,
        )
        self.io.atomic_bytes("site.json", source_site, limit=MAX_JSON_BYTES)
        self.io.atomic_json("source.json", self.source.model_dump(mode="json"))
        self.entry_evidence_id = ""
        for record in site.evidence:
            for file in record.files:
                data = source.verified_bytes(file, limit=MAX_FILE_BYTES)
                imported = self.write_evidence(
                    data,
                    representation=file.representation,
                    complete=file.complete,
                    original_evidence_id=record.evidence_id,
                    imported=True,
                )
                if (
                    record.evidence_id == site.selected_evidence_id
                    and file.representation == "entity_bytes"
                ):
                    self.entry_evidence_id = imported.evidence_id
        if not self.entry_evidence_id:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Selected HTML was not imported.")
        self.checkpoint("prepared")

    def write_evidence(
        self,
        data: bytes,
        *,
        representation: Representation,
        complete: bool,
        source_evidence_id: str | None = None,
        transform_version: str | None = None,
        original_evidence_id: str | None = None,
        imported: bool = False,
    ) -> Step1Evidence:
        if self._sealed:
            raise ValueError("publication is sealed")
        if (
            len(data) > MAX_FILE_BYTES
            or self._physical_bytes + len(data) > MAX_TOTAL_BYTES
            or (not imported and self._logical_bytes + len(data) > MAX_TOTAL_BYTES)
        ):
            raise problem("OUTPUT_WRITE_FAILED", "output", "Evidence storage cap exceeded.")
        if imported != (original_evidence_id is not None):
            raise ValueError("import requires original evidence identity")
        identity = "ev-" + uuid.uuid4().hex
        record = Step1Evidence(
            evidence_id=identity,
            run_id=self.run_id,
            file=Step1File(
                path=f"raw/{uuid.uuid4().hex}.bin",
                sha256=digest(data),
                size_bytes=len(data),
                complete=complete,
            ),
            representation=representation,
            source_evidence_id=source_evidence_id,
            source_run_id=self.run_id if imported else None,
            original_evidence_id=original_evidence_id,
            transform_version=transform_version,
        )
        # Reserve before writing; a failed write cannot open up a new storage allowance.
        self._physical_bytes += len(data)
        if not imported:
            self._logical_bytes += len(data)
        self.io.atomic_bytes(record.file.path, data)
        self.records.append(record)
        return record

    def observe(self, event: HttpAttempt | RedirectHop, purpose: str) -> None:
        if isinstance(event, RedirectHop):
            payload: object = dict(resource_purpose=purpose, redirect=event.model_dump(mode="json"))
        else:
            encoded = self.write_evidence(
                event.encoded_body,
                representation="content_encoded",
                complete=event.body_complete,
            )
            entity = self.write_evidence(
                event.decoded_body,
                representation="entity_bytes",
                complete=event.body_complete,
                source_evidence_id=encoded.evidence_id,
                transform_version="http-content-decoding-1",
            )
            bodies = (encoded, entity)
            payload = dict(
                schema_version="step1-1.0.0",
                resource_purpose=purpose,
                attempt_id=event.attempt_id,
                parent_attempt_id=event.parent_attempt_id,
                attempt_cause=event.purpose,
                requested_url=event.requested_url,
                started_at=event.started_at.isoformat(),
                elapsed_ms=event.elapsed_ms,
                request_sent=event.request_sent,
                target=asdict(event.target) if event.target else None,
                connected_ip=event.connected_ip,
                tls_state=event.tls_state,
                status_code=event.status_code,
                response_headers=event.resource_headers,
                bytes_read=event.received_bytes,
                body_complete=event.body_complete,
                error=event.error.model_dump(mode="json") if event.error else None,
                authorization=asdict(event.authorization) if event.authorization else None,
                body_evidence_ids=[b.evidence_id for b in bodies],
            )
        record = self.write_evidence(
            encoded_json(payload),
            representation="attempt_observation",
            complete=True,
        )
        if isinstance(event, HttpAttempt):
            self.attempt_evidence[event.attempt_id] = (
                *tuple(b.evidence_id for b in bodies),
                record.evidence_id,
            )
        self._journal.append(dict(evidence_id=record.evidence_id, resource_purpose=purpose))
        self.checkpoint("running")

    def checkpoint(self, state: str) -> None:
        if self._sealed:
            raise ValueError("publication is sealed")
        self.io.atomic_json("raw/manifest.json", [r.model_dump(mode="json") for r in self.records])
        self.io.atomic_json("journal.json", dict(state=state, observations=self._journal))

    def publish(self, result: Step1Result) -> None:
        if self._sealed:
            raise ValueError("publication cannot be committed twice")
        result = Step1Result.model_validate_json(result.model_dump_json())
        if result.source != self.source or result.run.publication_id != self.io.root.name:
            raise ValueError("publication/source identity mismatch")
        if result.evidence != tuple(self.records):
            raise ValueError("publication differs from stored observations")
        for record in self.records:
            verify_file(self.io, record)
        # Seal before metadata: an interrupted generation cannot be retried or mixed.
        self._sealed = True
        bodies: Mapping[ArtifactName, bytes] = {
            "site.json": self.io.read_bytes("site.json", limit=MAX_JSON_BYTES),
            "source.json": encoded_json(self.source.model_dump(mode="json")),
            "config.json": encoded_json(result.config.model_dump(mode="json")),
            "raw/manifest.json": encoded_json([r.model_dump(mode="json") for r in self.records]),
            "crawl.json": encoded_json(result.crawl.model_dump(mode="json")),
            "checks.json": encoded_json(result.checks.model_dump(mode="json")),
        }
        if digest(bodies["site.json"]) != self.source.site_sha256:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Site snapshot changed.")
        hashes = tuple(ArtifactDigest(name=name, sha256=digest(bodies[name])) for name in ARTIFACTS)
        for name in (
            "source.json",
            "config.json",
            "raw/manifest.json",
            "crawl.json",
            "checks.json",
        ):
            self.io.atomic_bytes(name, bodies[name], limit=MAX_JSON_BYTES)
        self.io.atomic_json(
            "journal.json", dict(state=result.run.lifecycle, observations=self._journal)
        )
        commit = PublicationCommit.model_validate_json(
            encoded_json(
                dict(
                    **result.run.model_dump(mode="json"),
                    artifact_hashes=[h.model_dump(mode="json") for h in hashes],
                )
            )
        )
        self.io.atomic_json("run.json", commit.model_dump(mode="json"))  # Commit marker, last.


def verify_file(store: EvidenceStore, record: Step1Evidence) -> bytes:
    raw = store.read_bytes(record.file.path, limit=min(MAX_FILE_BYTES, record.file.size_bytes))
    if len(raw) != record.file.size_bytes or digest(raw) != record.file.sha256:
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Evidence size/hash mismatch.")
    return raw


def load_step1_result(directory: Path) -> Step1Result:
    """Offline committed-publication inspector, without restoring any network rights."""
    from .config import Step1Config

    io = EvidenceStore(directory)
    try:
        commit = PublicationCommit.model_validate_json(encoded_json(io.read_json("run.json")))
        if commit.publication_id != io.root.name:
            raise ValueError("publication identity differs from directory")
        artifacts = {}
        for item in commit.artifact_hashes:
            raw = io.read_bytes(item.name, limit=MAX_JSON_BYTES)
            if digest(raw) != item.sha256:
                raise ValueError("artifact digest mismatch")
            artifacts[item.name] = raw
        result = Step1Result(
            source=SourceRef.model_validate_json(artifacts["source.json"]),
            config=Step1Config.model_validate_json(artifacts["config.json"]),
            evidence=TypeAdapter(tuple[Step1Evidence, ...]).validate_json(
                artifacts["raw/manifest.json"]
            ),
            crawl=CrawlResult.model_validate_json(artifacts["crawl.json"]),
            checks=ChecksResult.model_validate_json(artifacts["checks.json"]),
            run=RunResult.model_validate_json(
                commit.model_dump_json(
                    exclude={"commit_version", "artifact_hashes"},
                )
            ),
        )
        if digest(artifacts["site.json"]) != result.source.site_sha256:
            raise ValueError("source site snapshot hash mismatch")
        for record in result.evidence:
            verify_file(io, record)
        return result
    except (ValueError, KeyError, RecursionError):
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Invalid step 1 publication.") from None
