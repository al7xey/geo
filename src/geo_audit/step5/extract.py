"""Pure Unicode/span and URL extraction. URLs are observations, never egress permits."""

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from ..psl import PublicSuffixList
from ..urls import normalize_host
from .models import Answer, MentionCandidate, SourceObservation, Target

URL_PATTERN = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
MAX_OBSERVATIONS = 2000


def folded(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Map NFKC/casefold expansions and combining clusters to original codepoint spans."""
    pieces: list[str] = []
    positions: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = start + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        piece = unicodedata.normalize("NFKC", text[start:end]).casefold()
        pieces.append(piece)
        positions.extend([(start, end)] * len(piece))
        start = end
    return "".join(pieces), positions


class Extractor:
    def __init__(self, target: Target, psl: PublicSuffixList):
        self.target = target
        self.psl = psl
        self.patterns: list[tuple[str, bool, re.Pattern[str]]] = []
        ambiguous = {folded(a)[0] for a in target.ambiguous_aliases}
        seen: set[str] = set()
        for alias in [target.name, *target.aliases]:
            normalized = folded(alias)[0]
            if normalized in seen:
                continue
            seen.add(normalized)
            pattern = re.compile(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)")
            self.patterns.append((alias, normalized in ambiguous, pattern))

    def source(self, url: str, origin: str, span: list[int] | None) -> SourceObservation:
        if len(url) > 4096 or any(c.isspace() or unicodedata.category(c)[0] == "C" for c in url):
            raise ValueError("invalid URL size or controls")
        if "\\" in url:
            raise ValueError("backslash in URL")
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid or credential-bearing URL")
        port = parsed.port
        if port == 0:
            raise ValueError("invalid port")
        host = normalize_host(parsed.hostname)
        authority = f"[{host}]" if ":" in host else host
        if port is not None and (parsed.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
            authority += f":{port}"
        normalized = urlunsplit(
            (parsed.scheme.lower(), authority, parsed.path or "/", parsed.query, parsed.fragment)
        )
        _, registrable, known = self.psl.boundary(host)
        return SourceObservation(
            url=normalized,
            host=host,
            registrable_domain=registrable if known else None,
            psl_known=known,
            target_host_match=any(host == d or host.endswith("." + d) for d in self.target.domains),
            origins=[origin],
            text_spans=[span] if span else [],
        )

    def extract(
        self, answer: Answer
    ) -> tuple[list[MentionCandidate], list[SourceObservation], int]:
        text_urls: list[tuple[str, list[int]]] = []
        for match in URL_PATTERN.finditer(answer.text):
            value = match[0].rstrip(".,;:!?。»”")
            # Closing Markdown punctuation is not part of the URL unless balanced.
            for left, right in (("(", ")"), ("[", "]"), ("{", "}")):
                while value.endswith(right) and value.count(right) > value.count(left):
                    value = value[:-1]
            text_urls.append((value, [match.start(), match.start() + len(value)]))
            if len(text_urls) > MAX_OBSERVATIONS:
                raise ValueError("extraction observation limit")
        normalized, positions = folded(answer.text)
        candidates: list[MentionCandidate] = []
        for alias, ambiguous, pattern in self.patterns:
            for match in pattern.finditer(normalized):
                start, end = positions[match.start()][0], positions[match.end() - 1][1]
                in_url = any(start < span[1] and end > span[0] for _, span in text_urls)
                candidates.append(
                    MentionCandidate(
                        alias=alias,
                        quote=answer.text[start:end],
                        start=start,
                        end=end,
                        ambiguous=ambiguous,
                        location="text_url" if in_url else "body",
                    )
                )
                if len(candidates) > MAX_OBSERVATIONS:
                    raise ValueError("extraction observation limit")
        candidates.sort(key=lambda c: (c.start, c.end, c.alias))
        sources: dict[str, SourceObservation] = {}
        invalid = 0
        observations: list[tuple[str, str, list[int] | None, int | None]] = [
            (c.url, "citation", None, index) for index, c in enumerate(answer.sources)
        ]
        observations += [(url, "text", span, None) for url, span in text_urls]
        for url, origin, span, citation_index in observations:
            try:
                source = self.source(url, origin, span)
                if citation_index is not None:
                    source.citation_indices.append(citation_index)
            except ValueError:
                invalid += 1
                continue
            existing = sources.get(source.url)
            if existing is None:
                sources[source.url] = source
            else:
                if origin not in existing.origins:
                    existing.origins.append(origin)
                existing.text_spans.extend(source.text_spans)
                existing.citation_indices.extend(source.citation_indices)
        return candidates, list(sources.values()), invalid
