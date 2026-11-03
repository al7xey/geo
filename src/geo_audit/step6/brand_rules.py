"""Local role attribution: literal claims, protected speech and unresolved context."""

import re

from ..step5.extract import folded
from ..step5.models import MentionCandidate
from .context import Segment, TextContext, evidence
from .models import BrandAnnotation, Decision, RoleClaim

POSITIVE = re.compile(
    r"\b(?:рекомендую|рекомендуем|советую|советуем)\s+"
    r"(?:(?:выбрать|использовать|рассмотреть|сервис|биржу)\s+){0,3}$",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(?:не\s+(?:рекомендую|рекомендуем|советую|советуем)|"
    r"не\s+стоит\s+(?:выбирать|использовать)|"
    r"(?:рекомендую|советую)\s+не\s+(?:выбирать|использовать|рассматривать))\s+"
    r"(?:(?:сервис|биржу)\s+)?$",
    re.I,
)
SUFFIX_POSITIVE = re.compile(
    r"^\s*[—–:-]\s*(?:рекомендую|советую)(?=\s+(?:как|для|в|к|при)\b|[.!?,;:]|\s*$)",
    re.I,
)
SUFFIX_NEGATIVE = re.compile(
    r"^\s*[—–:-]\s*(?:не\s+(?:рекомендую|советую)"
    r"(?=\s+(?:как|для|в|к|при)\b|[.!?,;:]|\s*$)|"
    r"(?:рекомендую|советую)\s+не\s+(?:выбирать|использовать|рассматривать))\b",
    re.I,
)
ROLE_LANGUAGE = re.compile(
    r"рекоменд|совету|стоит\s+(?:выб|рассмотр)|я\s+бы\s+выбрал|выбрал\s+бы|"
    r"лучший\s+выбор|предпочтительн|остановил\w*\s+на|топ[-\s]*\d",
    re.I,
)
CAUTION = re.compile(
    r"не\s+(?:могу|готов|можем|считаю|думаю|значит|означает)|если\s+бы|возможно|вероятно|"
    r"мог\s+бы|можно\s+ли|цитат|отзыв|чуж\w*\s+оцен|"
    r"инструкци|игнорируй|написано|по\s+словам|неправда|неверно|ошибочно|"
    r"\b(?:сказа[лн]|говорил|утвержда[лн]|сообщ[аи][лн]|(?:на)?писа[лн])\w*",
    re.I,
)
NEUTRAL = re.compile(
    r"только\s+(?:перечисляю|упомяну)|нейтральный\s+список|"
    r"не\s+даю\s+(?:собственной\s+)?(?:положительной\s+или\s+отрицательной\s+)?"
    r"рекомендац|не\s+присоединяюсь|не\s+рейтинг\s+и\s+не\s+совет|"
    r"перечень\s+для\s+сбора\s+данных",
    re.I,
)
DISAVOWAL = re.compile(r"это\s+чуж\w*\s+оцен|не\s+присоединяюсь", re.I)
PRONOUN = re.compile(r"\b(?:его|её|ее|ему|него|этот|эту|него)\b", re.I)


def linked_aliases(
    text: str, mentions: list[MentionCandidate], pieces: list[Segment], context: TextContext
) -> frozenset[str]:
    result = set()
    for short in (m for m in mentions if m.ambiguous):
        for full in (m for m in mentions if not m.ambiguous):
            pattern = re.compile(
                r"^\s*под\s+"
                + re.escape(short.quote)
                + r"\s+(?:здесь\s+)?понимается\s+(?:именно\s+)?"
                + re.escape(full.quote)
                + r"[.!\s]*$",
                re.I,
            )
            if any(pattern.fullmatch(p.text) and not context.is_protected(p.start) for p in pieces):
                result.add(folded(short.quote)[0])
    return frozenset(result)


def classify_brand(
    text: str,
    name: str,
    mentions: list[MentionCandidate],
    is_target: Decision,
    pieces: list[Segment],
    context: TextContext,
    linked: frozenset[str] = frozenset(),
) -> BrandAnnotation:
    claims: list[RoleClaim] = []
    reasons: set[str] = set()
    dismissed: set[int] = set()
    neutral_mentions = 0
    presence = [evidence(text, m.start, m.end, "literal_name") for m in mentions]
    for mention in mentions:
        if mention.ambiguous and folded(mention.quote)[0] not in linked:
            reasons.add("ambiguous_alias")
            continue
        piece_index = next(
            (i for i, p in enumerate(pieces) if p.start <= mention.start and mention.end <= p.end),
            None,
        )
        if piece_index is None:
            reasons.add("unresolved_segment")
            continue
        piece = pieces[piece_index]
        local_neutral = bool(NEUTRAL.search(piece.text))
        neutral_mentions += local_neutral
        following = pieces[piece_index + 1].text if piece_index + 1 < len(pieces) else ""
        disavowed = bool(DISAVOWAL.search(following))
        quoted = context.is_protected(mention.start) and not context.brand_only_quote(
            mention.start, mention.end
        )
        if piece.text.rstrip().endswith("?"):
            reasons.add("interrogative_advice")
            continue
        if quoted or CAUTION.search(piece.text):
            if local_neutral or disavowed:
                dismissed.add(piece.start)
            else:
                reasons.add("quoted_or_reported_advice")
            continue
        prefix = text[piece.start : mention.start]
        suffix = text[mention.end : piece.end]
        # Strip only paired name wrappers, not arbitrary quotation/directive context.
        for left, right in (("**", "**"), ("__", "__"), ("«", "»"), ('"', '"'), ("“", "”")):
            if prefix.endswith(left) and suffix.startswith(right):
                prefix, suffix = prefix[: -len(left)], suffix[len(right) :]
                break
        negative = NEGATIVE.search(prefix)
        positive = POSITIVE.search(prefix)
        negative_suffix = SUFFIX_NEGATIVE.search(suffix)
        positive_suffix = SUFFIX_POSITIVE.search(suffix)
        item = context.item_at(mention.start)
        if negative or negative_suffix:
            preceding = prefix[: negative.start()] if negative else prefix
            if re.search(r"\bне\b", preceding, re.I):
                reasons.add("negation_scope")
            else:
                claims.append(
                    RoleClaim(
                        role="discouraged",
                        evidence=evidence(
                            text, piece.start, piece.end, "explicit_negative_directive"
                        ),
                    )
                )
        elif positive or positive_suffix:
            if re.search(r"\bне\b", prefix, re.I) and not re.search(
                r"не\s+только\s+рекомендую", prefix, re.I
            ):
                reasons.add("negation_scope")
            else:
                claims.append(
                    RoleClaim(
                        role="recommended",
                        evidence=evidence(
                            text, piece.start, piece.end, "explicit_positive_directive"
                        ),
                    )
                )
        elif item and item.recommendation_header:
            claims.append(
                RoleClaim(
                    role="recommended",
                    evidence=evidence(text, item.start, item.end, "recommended_list_item"),
                    context_evidence=evidence(
                        text, *item.recommendation_header, "recommendation_header"
                    ),
                )
            )
    unresolved_pieces = [
        p
        for p in pieces
        if p.start not in dismissed and ROLE_LANGUAGE.search(p.text) and not NEUTRAL.search(p.text)
    ]
    if not claims and neutral_mentions < len(mentions) and unresolved_pieces:
        reasons.add("unresolved_role_language")
    if any(
        PRONOUN.search(p.text) and not any(p.start <= m.start < p.end for m in mentions)
        for p in unresolved_pieces
    ):
        reasons.add("unresolved_pronoun")
    # A role with explicit evidence remains yes; unresolved additional claims stay visible.
    recommended: Decision = (
        "yes"
        if any(c.role == "recommended" for c in claims)
        else ("uncertain" if reasons else "no")
    )
    discouraged: Decision = (
        "yes"
        if any(c.role == "discouraged" for c in claims)
        else ("uncertain" if reasons else "no")
    )
    unique_claims = {(c.role, c.evidence.start, c.evidence.end): c for c in claims}
    claims = list(unique_claims.values())
    if not claims:
        claims = [RoleClaim(role="mentioned", evidence=presence[0])]
    ranks = [context.rank(m.start) for m in mentions]
    rank, rank_evidence, rank_context = next(
        (r for r in ranks if r[0] is not None), (None, None, None)
    )
    if len({r[0] for r in ranks if r[0] is not None}) > 1:
        rank = rank_evidence = rank_context = None
        reasons.add("conflicting_ranks")
    return BrandAnnotation(
        name=name,
        is_target=is_target,
        presence_evidence=presence,
        roles=claims,
        recommended=recommended,
        discouraged=discouraged,
        rank=rank,
        rank_evidence=rank_evidence,
        rank_context_evidence=rank_context,
        identity_basis="target_dictionary" if is_target != "no" else "structured_name_candidate",
        reason_codes=sorted(reasons) if reasons else ["explicit_rule_baseline"],
    )
