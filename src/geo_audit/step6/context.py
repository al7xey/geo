"""Bounded, pre-indexed quote and list context; preserves original evidence offsets."""

import re
from bisect import bisect_right
from dataclasses import dataclass

from .models import Evidence

LIST_LINE = re.compile(r"^\s*(?:(\d{1,3})[.)]|[-*•])\s+(.+)$")
PROTECTED = re.compile(
    r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|"
    r"`[^\n`]*(?:`|$)|«[^»]*(?:»|$)|“[^”]*(?:”|$)|"
    r'"[^"\n]*(?:"|$)|(?m:^[ \t]*>[^\n]*)'
)
RANK_HEADER = re.compile(
    r"^(?:(?:мой|наш|итоговый|предварительный)\s+)?"
    r"(?:рейтинг|ранжирование|итоговый порядок)(?:\s+[^?!.]*)?:?\s*$",
    re.I,
)
REC_HEADER = re.compile(
    r"^рекомендованные\s+(?:сервисы|компании|варианты|биржи)(?:\s+[^?!.]*)?:?\s*$",
    re.I,
)


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ListItem:
    start: int
    end: int
    ordinal: int | None
    recommendation_header: tuple[int, int] | None
    rank_header: tuple[int, int] | None


def evidence(text: str, start: int, end: int, rule: str) -> Evidence:
    return Evidence(quote=text[start:end], start=start, end=end, rule=rule)


class TextContext:
    def __init__(self, text: str):
        self.text = text
        self.protected = [(m.start(), m.end()) for m in PROTECTED.finditer(text)]
        self.protected_starts = [start for start, _ in self.protected]
        self.items: dict[int, ListItem] = {}
        self.line_starts: list[int] = []
        offset = 0
        recommendation_header = rank_header = None
        used = False
        blank_allowed = True
        for raw_line in text.splitlines(keepends=True):
            self.line_starts.append(offset)
            line = raw_line.rstrip("\r\n")
            clean = re.sub(r"^\s*#{1,6}\s+", "", line).strip().strip("*").strip()
            item = LIST_LINE.match(line)
            blocked = self.is_protected(offset + len(line) - len(line.lstrip()))
            if item:
                ordinal = int(item[1]) if item[1] else None
                self.items[offset] = ListItem(
                    offset, offset + len(line), ordinal, recommendation_header, rank_header
                )
                used = True
            elif not clean and not used and blank_allowed:
                blank_allowed = False
            else:
                recommendation_header = (
                    (offset, offset + len(line))
                    if not blocked and REC_HEADER.fullmatch(clean)
                    else None
                )
                rank_header = (
                    (offset, offset + len(line))
                    if not blocked
                    and RANK_HEADER.fullmatch(clean)
                    and not re.search(r"\bне\b", clean, re.I)
                    else None
                )
                used = False
                blank_allowed = True
            offset += len(raw_line)

    def is_protected(self, position: int) -> bool:
        index = bisect_right(self.protected_starts, position) - 1
        return index >= 0 and position < self.protected[index][1]

    def brand_only_quote(self, start: int, end: int) -> bool:
        # Quoted brand spelling is formatting; a quoted entire directive is not.
        index = bisect_right(self.protected_starts, start) - 1
        return (
            index >= 0
            and self.protected[index] == (start - 1, end + 1)
            and any(
                self.text[start - 1 : start] == left and self.text[end : end + 1] == right
                for left, right in (("«", "»"), ('"', '"'), ("“", "”"))
            )
        )

    def item_at(self, position: int) -> ListItem | None:
        index = bisect_right(self.line_starts, position) - 1
        return self.items.get(self.line_starts[index]) if index >= 0 else None

    def rank(self, position: int) -> tuple[int | None, Evidence | None, Evidence | None]:
        item = self.item_at(position)
        if item and item.ordinal and item.rank_header and not self.is_protected(position):
            return (
                item.ordinal,
                evidence(self.text, item.start, item.end, "explicit_ranked_list"),
                evidence(self.text, *item.rank_header, "ranking_header"),
            )
        return None, None, None
