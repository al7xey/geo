"""Offline-only extraction from saved HTML, versioned Unicode text and quoted heuristics."""

from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from html.parser import HTMLParser
from importlib.metadata import version
from typing import TYPE_CHECKING

import trafilatura
from bs4 import BeautifulSoup, Tag
from trafilatura.settings import DEFAULT_CONFIG

from .models import Page, QuoteRef, TextRef

if TYPE_CHECKING:
    from .runner import Step1Runtime

TRANSFORM = "static-extraction-1"
BUSINESS = re.compile(
    r"\b(?:we\s+(?:provide|offer|manufacture|produce|supply|develop|deliver|sell|speciali[sz]e\s+in)"
    r"|our company\s+(?:provides|offers|manufactures|produces|supplies|develops|sells)"
    r"|we\s+help|(?:service|platform|marketplace)\s+(?:for|that)"
    r"|мы\s+(?:производим|предлагаем|поставляем|разрабатываем|продаём|продаем|оказываем"
    r"|помогаем|делаем|создаём|создаем|обучаем|находим|подбираем|сравниваем|ищем|доставляем)"
    r"|компания\s+(?:производит|предлагает|поставляет|разрабатывает|продаёт|продает|оказывает)"
    r"|(?:сервис|платформа|маркетплейс|биржа|школа|магазин)\s+(?:для|поиска|по|услуг|товаров))"
    r"\s+[^.!?\n]{8,240}",
    re.I,
)
LEGAL_SEGMENTS = frozenset({"privacy", "terms", "legal", "cookies", "policy", "oferta"})
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_NODES = 100000


class _WorkLimit(ValueError):
    pass


class _HTMLWork(HTMLParser):
    """Reject token/attribute bombs before either DOM-building extractor allocates its tree."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.work = 0

    def add(self, count: int = 1) -> None:
        self.work += count
        if self.work > MAX_NODES:
            raise _WorkLimit("EXTRACTION_NODE_LIMIT")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.add(1 + len(attrs))

    def handle_endtag(self, tag: str) -> None:
        self.add()

    def handle_data(self, data: str) -> None:
        self.add()

    def handle_comment(self, data: str) -> None:
        self.add()


def normalize_text(text: str) -> str:
    """text-ws-1: NFC, whitespace to spaces per line, empty lines removed; repeats retained."""
    lines = (" ".join(line.split()) for line in unicodedata.normalize("NFC", text).splitlines())
    return "\n".join(line for line in lines if line)


def business_span(text: str) -> tuple[int, int] | None:
    match = BUSINESS.search(text)
    return match.span() if match else None


def page_description(soup: BeautifulSoup) -> list[str]:
    """Owner-written summary from title, meta and JSON-LD; read before scripts are removed."""
    found: list[str] = []
    if soup.title:
        found.append(soup.title.get_text(" "))
    for meta in soup.find_all("meta", limit=2000):
        key = str(meta.get("name") or meta.get("property") or "").lower()
        if key in {"description", "og:title", "og:description", "twitter:description"}:
            found.append(str(meta.get("content", "")))
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=20):
        try:
            data = json.loads(script.get_text()[:200_000])
        except ValueError:
            continue
        stack, seen = [data], 0
        while stack and seen < 500:
            item, seen = stack.pop(), seen + 1
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
                for key in ("name", "slogan", "description"):
                    if isinstance(item.get(key), str):
                        found.append(item[key])
    lines = (normalize_text(text)[:2048] for text in found)
    return list(dict.fromkeys(line for line in lines if len(line) >= 15))


def quote(text: str, ref: TextRef, start: int, end: int) -> QuoteRef:
    return QuoteRef(
        evidence_id=ref.evidence_id,
        text_hash=ref.text_hash,
        start=start,
        end=end,
        text=text[start:end],
    )


def save_text(runtime: Step1Runtime, text: str, source: str, *, main: bool) -> TextRef:
    raw = runtime.store.write_evidence(
        text.encode("utf-8"),
        representation="main_text" if main else "extracted_raw",
        complete=True,
        source_evidence_id=source,
        transform_version=TRANSFORM,
    )
    return TextRef(evidence_id=raw.evidence_id, text_hash=raw.file.sha256, text_chars=len(text))


def extract_page(
    runtime: Step1Runtime,
    page: Page,
    html: str,
    headers: tuple[tuple[str, str], ...],
    header_evidence: str | None = None,
) -> Page:
    from urllib.parse import unquote, urlsplit

    from .checks import capture, metadata

    runtime.check_time()
    data = html.encode("utf-8")
    if len(data) > MAX_HTML_BYTES:
        return page.model_copy(
            update={
                "extraction_reason": "EXTRACTION_SIZE_LIMIT",
                "limitations": ("EXTRACTION_SIZE_LIMIT",),
            }
        )
    work = _HTMLWork()
    try:
        for offset in range(0, len(html), 65536):
            work.feed(html[offset : offset + 65536])
            runtime.check_time()
        work.close()
    except _WorkLimit:
        return page.model_copy(
            update={
                "extraction_reason": "EXTRACTION_NODE_LIMIT",
                "limitations": ("EXTRACTION_NODE_LIMIT",),
            }
        )
    except AssertionError as error:
        if not str(error).startswith(("unknown status keyword ", "expected name token at ")):
            raise
        return page.model_copy(
            update={
                "extraction_reason": "EXTRACTION_MARKUP_INCOMPLETE",
                "limitations": ("EXTRACTION_MARKUP_INCOMPLETE",),
            }
        )
    decoded = runtime.store.write_evidence(
        data,
        representation="decoded_text",
        complete=True,
        source_evidence_id=page.evidence_id,
        transform_version="html-decoding-step0-1",
    )
    # Explicit html.parser never resolves DTD/external entities or requests resources.
    soup = BeautifulSoup(html, "html.parser")
    runtime.check_time()
    if sum(1 for _ in soup.descendants) > MAX_NODES:
        return page.model_copy(
            update={
                "extraction_reason": "EXTRACTION_NODE_LIMIT",
                "limitations": ("EXTRACTION_NODE_LIMIT",),
            }
        )
    title = normalize_text(soup.title.get_text(" "))[:1024] if soup.title else None
    headings = tuple(
        normalize_text(h.get_text(" "))[:2048] for h in soup.find_all(["h1", "h2"], limit=200)
    )
    original_links = sum(len(a.get_text(" ", strip=True)) for a in soup.find_all("a"))
    original_visible = len(soup.get_text(" ", strip=True))
    facts = capture(soup, headers)
    described = page_description(soup)
    # Both methods operate on data only: no fetch_url/sitemap/crawler APIs of trafilatura.
    extractor_config = deepcopy(DEFAULT_CONFIG)
    extractor_config.set("DEFAULT", "MAX_TREE_SIZE", str(MAX_NODES))
    extractor_config.set("DEFAULT", "MAX_FILE_SIZE", str(MAX_HTML_BYTES))
    primary_raw = (
        trafilatura.extract(
            html,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            include_links=False,
            with_metadata=False,
            fast=True,
            deduplicate=False,
            config=extractor_config,
        )
        or ""
    )
    runtime.check_time()
    for node in soup.find_all(["script", "style", "noscript", "template", "nav", "footer", "form"]):
        node.decompose()
    for node in soup.find_all(True, attrs={"role": ["navigation", "banner"]}):
        node.decompose()
    for node in soup.select("#cookie-banner, .cookie-banner, #cookie-consent, .cookie-consent"):
        node.decompose()
    main = soup.find(["main", "article"]) or soup.body or soup
    if not isinstance(main, Tag):
        raise TypeError("HTML parser returned an invalid root")
    lists = tuple(
        normalize_text(n.get_text(" "))[:4096] for n in main.find_all(["ul", "ol"], limit=200)
    )
    tables = tuple(
        normalize_text(n.get_text(" "))[:8192] for n in main.find_all("table", limit=200)
    )
    fallback_raw = main.get_text("\n", strip=True)
    primary, fallback = normalize_text(primary_raw), normalize_text(fallback_raw)
    pwords, fwords = set(primary.lower().split()), set(fallback.lower().split())
    disagreement = bool(
        primary
        and fallback
        and (
            abs(len(primary) - len(fallback)) >= 100
            or len(pwords & fwords) / max(1, len(pwords | fwords)) < 0.6
        )
    )
    primary_weak = len(primary) < min(100, len(fallback)) or (
        len(primary) < len(fallback) / 2 and len(fallback) >= 300
    )
    chosen = fallback if primary_weak or not primary else primary
    chosen_raw = fallback_raw if primary_weak or not primary else primary_raw
    method = "beautifulsoup" if primary_weak or not primary else "trafilatura"
    from_metadata = False
    if described and len(chosen) < runtime.config.useful_chars:
        # Script-rendered pages: static HTML keeps the owner's own summary in metadata.
        extra = [line for line in described if line not in chosen]
        if extra:
            chosen = normalize_text("\n".join([chosen, *extra]) if chosen else "\n".join(extra))
            chosen_raw, method, from_metadata = chosen, "beautifulsoup+metadata", True
    raw_ref = save_text(runtime, chosen_raw, decoded.evidence_id, main=False)
    ref = save_text(runtime, chosen, raw_ref.evidence_id, main=True)
    alternates: tuple[TextRef, ...] = ()
    if disagreement:
        other = primary if method == "beautifulsoup" else fallback
        alternates = (save_text(runtime, other, decoded.evidence_id, main=False),)
    state = page.content_state
    segments = set(unquote(urlsplit(page.effective_url).path).lower().split("/"))
    lowered_title = (title or "").lower()
    if any(s in lowered_title for s in ("404", "page not found", "страница не найдена")):
        state = "soft_404"
    elif any(
        s in lowered_title
        for s in ("captcha", "access denied", "verify you are human", "sign in", "вход в аккаунт")
    ):
        state = "access_barrier"
    elif (
        segments & LEGAL_SEGMENTS
        or re.search(
            r"privacy policy|terms and conditions|terms of use|cookie policy|legal terms|"
            r"политика конфиденциальности|условия использования|публичная оферта|"
            r"^(?:terms|privacy)$",
            lowered_title,
        )
        or (
            not soup.find(["main", "article", "p", "table"])
            and original_links > original_visible * 0.7
        )
        or chosen.lower() in {"accept cookies", "accept all cookies", "cookie consent"}
    ):
        state = "service_content"
    quality = state == "html" and bool(chosen)
    span = business_span(chosen) if quality else None
    quality_chars = len("\n".join(dict.fromkeys(chosen.splitlines())))
    useful = quality and quality_chars >= runtime.config.useful_chars
    candidate = (
        next(c for c in runtime._crawler.candidates.values() if c.candidate_id == page.candidate_id)
        if runtime._crawler
        else None
    )
    supporting = (
        quality
        and not useful
        and len(chosen) >= 15
        and candidate is not None
        and (candidate.role == "contacts" or bool(re.search(r"contact|контакт", title or "", re.I)))
    )
    page = page.model_copy(
        update=dict(
            title=title,
            headings=headings,
            lists=lists,
            tables=tables,
            main_text=ref,
            alternate_texts=alternates,
            extraction_method=method,
            extractor_version=version(
                "trafilatura" if method == "trafilatura" else "beautifulsoup4"
            ),
            extraction_reason="METADATA_TEXT_FALLBACK"
            if from_metadata
            else "PRIMARY_WEAK_FALLBACK"
            if method == "beautifulsoup"
            else "PRIMARY_ACCEPTED",
            content_state=state,
            useful_candidate=useful,
            supporting_page=supporting,
            business_relevance="observed"
            if span
            else "not_observed"
            if state == "service_content"
            else "unknown",
            business_quotes=(quote(chosen, ref, *span),) if span else (),
            limitations=(("EXTRACTORS_DISAGREE",) if disagreement else ())
            + (("METADATA_TEXT_FALLBACK",) if from_metadata else ())
            + ("Business relevance: literal activity-phrase heuristic; no semantic certainty.",),
        )
    )
    # Parse metadata from original HTML, before destructive cleanup; no second tree/parse.
    page = metadata(runtime, page, facts, chosen, header_evidence)
    runtime.check_time()
    return page
