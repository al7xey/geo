"""Deterministic material gate; user statements never rewrite technical observations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import problem
from ..evidence import EvidenceStore
from .extraction import business_span, normalize_text
from .models import Material, Page, TextRef, UserMaterial

if TYPE_CHECKING:
    from .runner import Step1Runtime

MAX_DESCRIPTION_BYTES = 64 * 1024


def description_input(*, text: str | None = None, file: Path | None = None) -> str | None:
    if text is not None and file is not None:
        raise ValueError("provide description text or file, not both")
    if file is not None:
        try:
            text = (
                EvidenceStore(file.absolute().parent)
                .read_bytes(file.name, limit=MAX_DESCRIPTION_BYTES)
                .decode("utf-8-sig")
            )
        except UnicodeError:
            raise problem(
                "INPUT_ARTIFACT_INVALID", "input", "Description requires UTF-8."
            ) from None
    if text is not None and (not text.strip() or len(text.encode("utf-8")) > MAX_DESCRIPTION_BYTES):
        raise problem(
            "INPUT_ARTIFACT_INVALID",
            "input",
            "Description must be nonempty and at most64 KiB UTF-8.",
        )
    return text


def save_description(runtime: Step1Runtime, text: str) -> UserMaterial:
    text = description_input(text=text) or ""
    normalized = normalize_text(text)
    raw = runtime.store.write_evidence(
        text.encode("utf-8"), representation="user_material", complete=True
    )
    record = runtime.store.write_evidence(
        normalized.encode("utf-8"),
        representation="user_material",
        complete=True,
        source_evidence_id=raw.evidence_id,
        transform_version="user-description-text-ws-1",
    )
    # The user chose to describe the company; any wording works, the phrase list is for sites.
    suitable = len(normalized) >= 30 and (
        business_span(normalized) is not None or len(normalized.split()) >= 5
    )
    return UserMaterial(
        material_id="user-" + uuid.uuid4().hex,
        provided_at=runtime.time.utcnow(),
        text=TextRef(
            evidence_id=record.evidence_id, text_hash=record.file.sha256, text_chars=len(normalized)
        ),
        suitability="usable" if suitable else "needs_clarification",
        reason="user-material-2: length>=30, activity phrase or 5+ words; unverified user statement"
        if suitable
        else "user-material-1: clarify activity and products/services; no semantic assertion",
    )


def summarize(pages: list[Page], users: tuple[UserMaterial, ...]) -> Material:
    useful = tuple(p.page_id for p in pages if p.useful_candidate)
    supporting = tuple(p.page_id for p in pages if p.supporting_page)
    business = sum(p.useful_candidate and p.business_relevance == "observed" for p in pages)
    # The literal phrase is only one signal; enough owner text is material for step 2 on its own.
    state = (
        "user_supported"
        if any(u.suitability == "usable" for u in users)
        else "usable"
        if business >= 3 or len(useful) >= 5
        else "limited"
        if business or useful
        else "insufficient"
        if any(
            p.main_text
            and p.main_text.text_chars
            and p.content_state in {"html", "service_content"}
            for p in pages
        )
        else "needs_description"
    )
    return Material.model_validate(
        dict(
            state=state,
            can_start_step2=state in {"usable", "limited", "user_supported"},
            reason=(
                "material-2: activity phrase or useful pages; 300 chars is not completeness proof"
            ),
            useful_page_ids=useful,
            supporting_page_ids=supporting,
            user_materials=users,
        )
    )
