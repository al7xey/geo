"""Only verified local step 1 evidence can become page/check context."""

from pathlib import Path
from urllib.parse import urlsplit

from ..evidence import EvidenceStore
from ..step1.storage import load_step1_result, verify_file
from ..step5.models import Target
from ..step5.pipeline import MAX_METADATA_BYTES, digest
from ..urls import normalize_host
from .models import CompanyFact, PageContext, SiteContext


def belongs(url: str, target: Target) -> bool:
    host = normalize_host(urlsplit(url).hostname or "")
    return any(host == domain or host.endswith("." + domain) for domain in target.domains)


def load_context(directory: Path | None, target: Target) -> SiteContext:
    if directory is None:
        return SiteContext()
    result = load_step1_result(directory)
    store = EvidenceStore(directory)
    site = store.read_bytes("site.json", limit=MAX_METADATA_BYTES)
    from ..models import SiteValidationResult

    original = SiteValidationResult.model_validate_json(site)
    if not belongs(original.effective_url or original.input_url, target):
        raise ValueError("technical context belongs to another target")
    records = {record.evidence_id: record for record in result.evidence}
    pages = []
    facts: list[CompanyFact] = []
    for page in result.crawl.pages[:30]:
        if page.main_text is None or not belongs(page.effective_url, target):
            continue
        text = verify_file(store, records[page.main_text.evidence_id]).decode("utf-8")
        excerpt = text[:20000]
        pages.append(
            PageContext(
                page_id=page.page_id,
                url=page.effective_url,
                title=(page.title or "")[:1000],
                text=excerpt,
                source_text_sha256=digest(text.encode()),
                truncated=len(text) > 20000,
            )
        )
        for quote in page.business_quotes:
            if len(facts) >= 100:
                break
            if quote.text in excerpt and len(quote.text) <= 1000:
                facts.append(
                    CompanyFact(
                        fact_id="fact-" + digest((page.page_id + quote.text).encode())[:24],
                        text=quote.text,
                        page_id=page.page_id,
                        quote=quote.text,
                    )
                )
    unique_facts = {fact.fact_id: fact for fact in facts}
    return SiteContext(
        status="verified_step1",
        source_commit_sha256=digest(store.read_bytes("run.json", limit=MAX_METADATA_BYTES)),
        pages=pages,
        checks=list(result.checks.checks),
        facts=list(unique_facts.values()),
        limitations=list(result.run.limitations),
    )
