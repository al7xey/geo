"""Conservative annotation orchestration; roles are interpreted without model calls."""

import hashlib
import re
from typing import Literal, TypedDict

from ..step5.extract import folded
from ..step5.models import Answer, Delivery, Group, MechanicalAnswer, MentionCandidate, Target
from .brand_rules import classify_brand, linked_aliases
from .context import LIST_LINE, Segment, TextContext
from .models import Annotation, Decision

ANAPHORA = re.compile(
    r"(?:ту|та|той|эту|эта)\s+бирж\w*.*(?:обсуждал|ранее|истори|выше)|"
    r"(?:ранее|выше|истори|обсуждал).*(?:ту|та|той|эту|эта)\s+бирж|"
    r"компани\w*.*(?:название\s+неизвестно|истории\s+нет)",
    re.I,
)
REFUSAL = re.compile(r"^\s*(?:я\s+)?не\s+могу\s+(?:составить|дать\s+обоснованный)\b", re.I)


class AnnotationBase(TypedDict):
    answer_id: str
    prompt_id: str
    group: Group
    provider: str
    model: str
    origin: str
    source_line: int
    text_sha256: str
    delivery_status: Delivery
    measurement_eligible: bool


def segments(text: str) -> list[Segment]:
    result = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        if match.start() > start:
            result.append(Segment(start, match.start(), text[start : match.start()]))
        start = match.end()
    if start < len(text):
        result.append(Segment(start, len(text), text[start:]))
    return result


def structured_names(text: str, context: TextContext) -> list[tuple[str, int, int]]:
    """Extract only list/table name candidates, never claim exhaustive entity recognition."""
    result = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = LIST_LINE.match(line)
        if match:
            raw = match[2]
            start = offset + match.start(2)
        elif line.lstrip().startswith("|"):
            raw = line.lstrip()[1:].split("|", 1)[0].strip()
            start = offset + line.find(raw) if raw else offset
        else:
            offset += len(line)
            continue
        name = re.split(r"\s+[—–:]\s*|\.\s|\|", raw, maxsplit=1)[0].strip().rstrip(".,;:\r\n")
        for left, right in (("**", "**"), ("__", "__"), ("«", "»"), ('"', '"')):
            if name.startswith(left) and name.endswith(right):
                name = name[len(left) : -len(right)]
                start += len(left)
                break
        link = re.fullmatch(r"\[([^]\n]{2,100})\]\(https?://[^\s)]+\)", name)
        if link:
            name = link[1]
            start += 1
        if context.is_protected(start) and not context.brand_only_quote(start, start + len(name)):
            offset += len(line)
            continue
        if (
            2 <= len(name) <= 100
            and len(name.split()) <= 6
            and name[0].isupper()
            and name.casefold() not in {"сервис", "компания", "название"}
            and not set(name) <= set("-: ")
        ):
            result.append((name, start, start + len(name)))
        offset += len(line)
    return result


def annotate(answer: Answer, mechanical: MechanicalAnswer, target: Target) -> Annotation:
    if (
        answer.answer_id != mechanical.answer_id
        or answer.prompt_id != mechanical.prompt_id
        or answer.status != mechanical.status
        or hashlib.sha256(answer.text.encode()).hexdigest() != mechanical.text_sha256
    ):
        raise ValueError("answer and mechanical handoff mismatch")
    base: AnnotationBase = dict(
        answer_id=answer.answer_id,
        prompt_id=answer.prompt_id,
        group=mechanical.group,
        provider=answer.provider,
        model=answer.model,
        origin=answer.origin,
        source_line=mechanical.source_line,
        text_sha256=mechanical.text_sha256,
        delivery_status=answer.status,
        measurement_eligible=mechanical.visibility_eligible and answer.status == "ok",
    )
    if answer.status != "ok":
        return Annotation(
            **base,
            answer_kind=None,
            target_presence=None,
            target_recommended=None,
            target_discouraged=None,
            target_cited=None,
            brands=[],
            reason_codes=["delivery_not_usable"],
            needs_model=False,
            status="not_eligible",
        )
    text = answer.text
    pieces = segments(text)
    context = TextContext(text)
    mentions = [m for m in mechanical.candidates if m.location == "body"]
    unique = [m for m in mentions if not m.ambiguous]
    implicit_domain_advice = mechanical.target_domain_in_body and bool(
        re.search(
            r"\b(?:рекомендую|советую|выбрал)\b",
            text,
            re.I,
        )
    )
    presence: Decision = (
        "yes"
        if unique
        else ("uncertain" if mentions or ANAPHORA.search(text) or implicit_domain_advice else "no")
    )
    brands = []
    if mentions:
        linked = linked_aliases(text, mentions, pieces, context)
        brands.append(
            classify_brand(text, target.name, mentions, presence, pieces, context, linked)
        )
    occupied = [(m.start, m.end) for m in mentions]
    named: dict[str, tuple[str, list[MentionCandidate]]] = {}
    for name, start, end in structured_names(text, context):
        if any(start < right and end > left for left, right in occupied):
            continue
        key = folded(name)[0]
        if key not in named:
            named[key] = (name, [])
        named[key][1].append(
            MentionCandidate(
                alias=name,
                quote=text[start:end],
                start=start,
                end=end,
                ambiguous=False,
                location="body",
            )
        )
    for name, candidates in named.values():
        brands.append(classify_brand(text, name, candidates, "no", pieces, context))
    if brands and mentions:
        recommended = brands[0].recommended
        discouraged = brands[0].discouraged
    else:
        recommended = discouraged = "uncertain" if presence == "uncertain" else "no"
    if presence == "uncertain":
        recommended = discouraged = "uncertain"
    needs_model = (
        presence == "uncertain"
        or recommended == "uncertain"
        or discouraged == "uncertain"
        or any(b.recommended == "uncertain" or b.discouraged == "uncertain" for b in brands)
        or any("conflicting_ranks" in b.reason_codes for b in brands)
    )
    list_count = sum(bool(LIST_LINE.match(line)) for line in text.splitlines())
    kind: Literal["refusal", "list", "prose"] = (
        "refusal" if REFUSAL.search(text) else ("list" if list_count >= 2 else "prose")
    )
    return Annotation(
        **base,
        answer_kind=kind,
        target_presence=presence,
        target_recommended=recommended,
        target_discouraged=discouraged,
        target_cited="yes" if mechanical.target_domain_in_citations else "no",
        brands=brands,
        reason_codes=(
            sorted(
                {
                    reason
                    for b in brands
                    for reason in b.reason_codes
                    if reason != "explicit_rule_baseline"
                }
                | (
                    {"implicit_target_identity"}
                    if presence == "uncertain" and not mentions
                    else set()
                )
            )
            if needs_model
            else ["rule_baseline_only"]
        ),
        needs_model=needs_model,
        status="pending_model" if needs_model else "rules_complete",
    )


def validate_evidence(annotation: Annotation, text: str) -> None:
    if hashlib.sha256(text.encode()).hexdigest() != annotation.text_sha256:
        raise ValueError("annotation text hash mismatch")
    for brand in annotation.brands:
        items = [*brand.presence_evidence, *(claim.evidence for claim in brand.roles)]
        if brand.rank_evidence:
            items.append(brand.rank_evidence)
        if brand.rank_context_evidence:
            items.append(brand.rank_context_evidence)
        items.extend(claim.context_evidence for claim in brand.roles if claim.context_evidence)
        for item in items:
            if text[item.start : item.end] != item.quote:
                raise ValueError("annotation evidence does not match original text")
        if brand.recommended == "yes" and not any(c.role == "recommended" for c in brand.roles):
            raise ValueError("missing positive role evidence")
        if brand.discouraged == "yes" and not any(c.role == "discouraged" for c in brand.roles):
            raise ValueError("missing negative role evidence")
