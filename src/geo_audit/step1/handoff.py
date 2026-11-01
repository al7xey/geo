"""Offline Step2 consumer. Snapshot freshness is independent of live crawl deadlines."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from ..errors import problem
from ..evidence import EvidenceStore
from .extraction import normalize_text
from .models import Step2Input
from .storage import _load_step1_result, digest, verify_file

FRESHNESS_POLICY = "step2-offline-observation-7d-1"
DEFAULT_MAX_AGE = timedelta(days=7)


def load_step2_input(
    directory: Path, *, now: datetime | None = None, max_age: timedelta = DEFAULT_MAX_AGE
) -> Step2Input:
    result, commit_raw = _load_step1_result(directory)
    now = now or datetime.now(UTC)
    if now.utcoffset() != timedelta(0) or max_age <= timedelta(0):
        raise ValueError("UTC time and positive max_age required")
    if now < result.run.finished_at or now - result.run.budget.started_at > max_age:
        raise problem(
            "INPUT_ARTIFACT_INVALID",
            "input",
            "Offline snapshot stale or clock precedes publication.",
        )
    io = EvidenceStore(directory)
    texts: dict[str, str] = {}
    records = {r.evidence_id: r for r in result.evidence}
    try:
        refs = [
            t
            for p in result.crawl.pages
            for t in ((p.main_text,) if p.main_text else ()) + p.alternate_texts
        ]
        refs.extend(u.text for u in result.crawl.material.user_materials)
        for ref in refs:
            if ref.evidence_id not in texts:
                text = verify_file(io, records[ref.evidence_id]).decode("utf-8")
                if len(text) != ref.text_chars:
                    raise ValueError("text character count differs from saved UTF-8 text")
                if (
                    records[ref.evidence_id].representation in {"main_text", "user_material"}
                    and normalize_text(text) != text
                ):
                    raise ValueError("saved text does not match normalization version")
                texts[ref.evidence_id] = text
        quotes = [q for p in result.crawl.pages for q in p.business_quotes]
        quotes.extend(s.quote for p in result.crawl.pages for s in p.signals if s.quote)
        if any(texts[q.evidence_id][q.start : q.end] != q.text for q in quotes):
            raise ValueError("quote slice mismatch")
        pages = {p.page_id: p for p in result.crawl.pages}
        return Step2Input.model_validate(
            dict(
                source_commit_sha256=digest(commit_raw),
                result=result,
                useful_texts=tuple(
                    pages[i].main_text for i in result.crawl.material.useful_page_ids
                ),
                supporting_texts=tuple(
                    pages[i].main_text for i in result.crawl.material.supporting_page_ids
                ),
            )
        )
    except (ValueError, KeyError, UnicodeError):
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Invalid offline text/corpus/quote evidence."
        ) from None


def read_corpus(
    directory: Path, *, now: datetime | None = None
) -> tuple[Step2Input, Mapping[str, str]]:
    """Example public consumer: verified saved strings, no extraction or profile inference."""
    handoff = load_step2_input(directory, now=now)
    io = EvidenceStore(directory)
    records = {e.evidence_id: e for e in handoff.result.evidence}
    refs = (
        *handoff.useful_texts,
        *handoff.supporting_texts,
        *(u.text for u in handoff.result.crawl.material.user_materials),
    )
    return handoff, MappingProxyType(
        {r.evidence_id: verify_file(io, records[r.evidence_id]).decode("utf-8") for r in refs}
    )
