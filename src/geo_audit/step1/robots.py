"""REP fetch policy and independent line explanation, checked against pinned Protego."""

from __future__ import annotations

import asyncio
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlsplit

from protego import Protego

from ..http_client import FetchTrace
from ..request_policy import RequestAuthorization, RequestPolicy
from ..urls import normalize_input
from .models import RobotsDecision, RobotsObservation, TechnicalCheck
from .registry import robot_registry
from .storage import encoded_json

if TYPE_CHECKING:
    from .runner import Step1Runtime

PARSER_VERSION = "protego-0.7.0/geo-rfc-lines-1"


def canonical_path(value: str, *, pattern: bool = False) -> str:
    """Normalize UTF-8/unreserved escapes; retain encoded reserved delimiters."""

    def escape(match: re.Match[str]) -> str:
        char = chr(int(match[1], 16))
        return char if re.fullmatch(r"[A-Za-z0-9_.~-]", char) else "%" + match[1].upper()

    value = re.sub(r"%([0-9a-fA-F]{2})", escape, value)
    anchor = "$" if pattern and value.endswith("$") else ""
    if anchor:
        value = value[:-1]
    return quote(value, safe="/%?:@!&'()+,;=-._~" + ("*" if pattern else "")) + anchor


def pattern_matches(pattern: str, path: str) -> bool:
    """Anchored wildcard matching using bounded substring scans, not a backtracking regex."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    parts = pattern.split("*")
    if not path.startswith(parts[0]):
        return False
    position = len(parts[0])
    if len(parts) == 1:
        return not anchored or position == len(path)
    for part in parts[1:-1]:
        found = path.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    if anchored:
        return len(path) - len(parts[-1]) >= position and path.endswith(parts[-1])
    return path.find(parts[-1], position) >= 0


@dataclass(frozen=True)
class Rule:
    line: int
    allow: bool
    pattern: str

    @property
    def priority(self) -> int:
        anchor = "$" if self.pattern.endswith("$") else ""
        value = self.pattern[:-1] if anchor else self.pattern
        return len(quote(value, safe="/%=*") + anchor)


@dataclass
class Group:
    tokens: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    delays: list[tuple[int, float]] = field(default_factory=list)
    has_records: bool = False


@dataclass(frozen=True)
class ExplainedMatch:
    decision: Literal["allowed", "disallowed", "unknown"]
    group_lines: tuple[int, ...]
    rule_lines: tuple[int, ...]
    reason: str
    delay: float | None


class ParsedRobots:
    """Only canonical RFC fields and Crawl-delay enter Protego. Other raw lines remain evidence.

    The canonical input preserves line numbers. Tolerant aliases and malformed lines
    are explicitly ignored with warnings, rather than silently extending the protocol.
    """

    def __init__(self, text: str, check_time: Callable[[], None] = lambda: None):
        self.check_time = check_time
        self.groups: list[Group] = []
        self.warnings: list[str] = []
        self.sitemaps: list[str] = []
        self.valid_fields = 0
        canonical = []
        current: Group | None = None
        for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
            if number % 128 == 0:
                check_time()
            canonical.append("")
            line = raw.partition("#")[0].strip()
            if not line:
                continue
            name, colon, value = line.partition(":")
            name, value = name.strip().lower(), value.strip()
            if not colon or name not in {
                "user-agent",
                "allow",
                "disallow",
                "sitemap",
                "crawl-delay",
            }:
                self.warnings.append(f"IGNORED_LINE:{number}")
                continue
            if name == "user-agent":
                if current is None or current.has_records:
                    current = Group()
                    self.groups.append(current)
                if not re.fullmatch(r"[A-Za-z_-]+|\*", value):
                    self.warnings.append(f"INVALID_USER_AGENT:{number}")
                    continue
                current.tokens.append(value.lower())
                current.lines.append(number)
            elif name == "sitemap":
                if not value:
                    self.warnings.append(f"INVALID_SITEMAP:{number}")
                    continue
                self.sitemaps.append(value)
            else:
                if current is None or not current.tokens:
                    self.warnings.append(f"RULE_WITHOUT_GROUP:{number}")
                    continue
                current.has_records = True
                if name == "crawl-delay":
                    try:
                        delay = float(value)
                    except ValueError:
                        delay = -1.0
                    if not math.isfinite(delay) or delay < 0:
                        self.warnings.append(f"INVALID_CRAWL_DELAY:{number}")
                        continue
                    current.delays.append((number, delay))
                elif value:
                    if not value.startswith(("/", "*")) or re.search(r"%(?![a-fA-F0-9]{2})", value):
                        self.warnings.append(f"INVALID_PATH:{number}")
                        continue
                    current.rules.append(
                        Rule(number, name == "allow", canonical_path(value, pattern=True))
                    )
            canonical[-1] = f"{name}: {value}"
            self.valid_fields += 1
        self.parser = Protego.parse("\n".join(canonical))
        self._selected: dict[str, tuple[tuple[int, ...], tuple[Rule, ...], float | None]] = {}
        check_time()

    def _selection(self, token: str) -> tuple[tuple[int, ...], tuple[Rule, ...], float | None]:
        token = token.strip().lower()
        if token in self._selected:
            return self._selected[token]
        names = dict.fromkeys(t for group in self.groups for t in group.tokens)
        matching = [
            name
            for name in names
            if name != "*"
            and re.search(
                r"(?<![A-Za-z0-9_-])" + re.escape(name),
                token,
            )
        ]
        chosen = max(matching, key=len) if matching else "*"
        groups = [g for g in self.groups if chosen in g.tokens]
        lines = tuple(sorted({line for g in groups for line in g.lines}))
        rules = tuple(
            sorted(
                (r for g in groups for r in g.rules),
                key=lambda r: (-r.priority, not r.allow, r.line),
            )
        )
        delays = sorted(d for g in groups for d in g.delays)
        result = lines, rules, delays[-1][1] if delays else None
        self._selected[token] = result
        return result

    def evaluate(self, url: str, token: str) -> ExplainedMatch:
        self.check_time()
        lines, rules, delay = self._selection(token)
        parsed = urlsplit(url)
        path = canonical_path(
            (parsed.path or "/") + ("?" + parsed.query if "?" in url.split("#")[0] else "")
        )
        if "%3D" in path or any(
            "%3D" in rule.pattern
            or rule.allow
            and rule.pattern.endswith("/index.html")
            and path == rule.pattern.removesuffix("index.html")
            or rule.pattern.endswith("$")
            and "%24" in path
            for rule in rules
        ):
            return ExplainedMatch("unknown", (), (), "ROBOTS_PARSER_MISMATCH", None)
        winner = None
        if parsed.path != "/robots.txt":
            for index, rule in enumerate(rules):
                if index % 128 == 0:
                    self.check_time()
                if pattern_matches(rule.pattern, path):
                    winner = rule
                    break
        allowed = winner is None or winner.allow
        # Public API only: a disagreement invalidates the explanation, not just its line number.
        if self.parser.can_fetch(url, token) != allowed or self.parser.crawl_delay(token) != delay:
            return ExplainedMatch("unknown", (), (), "ROBOTS_PARSER_MISMATCH", None)
        self.check_time()
        return ExplainedMatch(
            "allowed" if allowed else "disallowed",
            lines,
            (winner.line,) if winner else (),
            "ROBOTS_FORBIDS"
            if not allowed
            else "ROBOTS_ALLOW_RULE"
            if winner
            else "ROBOTS_NO_MATCH",
            delay,
        )


@dataclass(frozen=True)
class CachedRobots:
    observation: RobotsObservation
    parser: ParsedRobots | None
    text_evidence_id: str | None
    reason: str


class RobotsService:
    def __init__(self, runtime: Step1Runtime):
        self.runtime = runtime
        self.registry = robot_registry()
        ua = runtime.client.config.user_agent
        self.own_token = ua.split("/", 1)[0].split(" ", 1)[0]
        if not re.fullmatch(r"[A-Za-z_-]+", self.own_token):
            raise ValueError("configured crawler UA needs a valid explicit product token")
        self.cache: dict[str, CachedRobots] = {}
        self.decisions: dict[tuple[str, str], RobotsDecision] = {}
        self.observations: list[RobotsObservation] = []
        runtime.check_time()
        snapshot = runtime.store.write_evidence(
            encoded_json(self.registry.model_dump(mode="json")),
            representation="registry_snapshot",
            complete=True,
        )
        self.registry_evidence_id = snapshot.evidence_id

    async def get(self, origin: str) -> CachedRobots:
        origin = normalize_input(origin).origin
        self.runtime.check_time()
        if origin in self.cache:
            self.runtime.note_cache_hit()
            return self.cache[origin]
        try:
            trace = await self.runtime.fetch(
                origin + "/robots.txt",
                RequestPolicy(
                    "robots",
                    allowed_sitemap_hosts=frozenset(self.runtime.config.allowed_sitemap_hosts),
                ),
            )
        except asyncio.CancelledError:
            cancelled_trace = self.runtime.client.last_trace
            if cancelled_trace is not None:
                self._record(origin, cancelled_trace)
            raise
        return self._record(origin, trace)

    def _record(self, origin: str, trace: FetchTrace) -> CachedRobots:
        last = trace.attempts[-1] if trace.attempts else None
        ids = self.runtime.store.attempt_evidence.get(last.attempt_id, ()) if last else ()
        status = last.status_code if last else None
        state: Literal[
            "present",
            "empty",
            "absent",
            "access_denied",
            "rate_limited",
            "unreachable",
            "invalid_response",
            "incomplete",
        ]
        warnings: list[str] = []
        parser = None
        text_id = ids[1] if len(ids) > 1 else None
        truncated = bool(trace.error and trace.error.code == "CONTENT_TOO_LARGE")
        if (
            truncated
            or status == 206
            or last
            and last.status_code is not None
            and not last.body_complete
        ):
            state, reason = "incomplete", "ROBOTS_TRUNCATED" if truncated else "ROBOTS_INCOMPLETE"
        elif status in {401, 403}:
            state, reason = "access_denied", "ROBOTS_ACCESS_DENIED"
        elif status == 429:
            state, reason = "rate_limited", "ROBOTS_RATE_LIMITED"
        elif trace.error or status is None or status >= 500 or 300 <= status < 400:
            state, reason = "unreachable", "ROBOTS_UNREACHABLE"
        elif status in {404, 410}:
            state, reason = "absent", "ROBOTS_ABSENT"
        elif status == 204:
            state, reason = "empty", "ROBOTS_EMPTY"
        elif status != 200 or last is None:
            state, reason = "invalid_response", "ROBOTS_INVALID_RESPONSE"
        else:
            try:
                text = last.decoded_body.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = None
            if (
                text is None
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x85\u2028\u2029]", text)
                or re.search(
                    r"<(?:!doctype|html|head|body|title|script|form|div)\b",
                    text,
                    re.I,
                )
            ):
                state, reason = "invalid_response", "ROBOTS_INVALID_RESPONSE"
            else:
                text_record = self.runtime.store.write_evidence(
                    text.encode("utf-8"),
                    representation="robots_text",
                    complete=True,
                    source_evidence_id=text_id,
                    transform_version="robots-utf8-lines-1",
                )
                text_id = text_record.evidence_id
                ids = (*ids, text_id)
                parser = self.runtime.bounded_phase(
                    last.decoded_body,
                    lambda _: ParsedRobots(text, self.runtime.check_time),
                    max_bytes=self.runtime.config.max_robots_bytes,
                )
                warnings.extend(parser.warnings)
                nonempty = any(line.partition("#")[0].strip() for line in text.splitlines())
                if not nonempty:
                    state, reason = "empty", "ROBOTS_EMPTY"
                elif not parser.valid_fields:
                    state, reason, parser = "invalid_response", "ROBOTS_INVALID_RESPONSE", None
                else:
                    state, reason = "present", "ROBOTS_NO_MATCH"
                types = [v for n, v in last.resource_headers if n == "content-type"]
                if len(types) != 1 or not types[0].lower().startswith("text/plain"):
                    warnings.append("ROBOTS_MEDIA_TYPE_MISMATCH")
        observation = RobotsObservation(
            observation_id="robots-" + uuid.uuid4().hex,
            origin=origin,
            final_url=last.requested_url if last else None,
            state=state,
            evidence_ids=ids,
            status_code=status,
            observed_at=self.runtime.time.utcnow(),
            parser_version=PARSER_VERSION,
            warnings=tuple(warnings),
            sitemap_urls=tuple(parser.sitemaps) if parser else (),
            truncated=truncated,
        )
        cached = CachedRobots(observation, parser, text_id, reason)
        self.cache[origin] = cached
        self.observations.append(observation)
        self.runtime.store.checkpoint("running")
        return cached

    async def evaluate(self, url: str, token: str) -> RobotsDecision:
        normalized = normalize_input(url)
        key = normalized.url, token.lower()
        self.runtime.check_time()
        if key in self.decisions:
            self.runtime.note_cache_hit()
            return self.decisions[key]
        cached = await self.get(normalized.origin)
        if cached.observation.state == "present" and cached.parser:
            match = cached.parser.evaluate(normalized.url, token)
        elif cached.observation.state in {"empty", "absent"}:
            match = ExplainedMatch("allowed", (), (), cached.reason, None)
        else:
            match = ExplainedMatch("unknown", (), (), cached.reason, None)
        result = RobotsDecision(
            decision_id="rep-" + uuid.uuid4().hex,
            observation_id=cached.observation.observation_id,
            evaluated_url=normalized.url,
            origin=normalized.origin,
            product_token=token,
            decision=match.decision,
            robots_evidence_id=cached.text_evidence_id,
            matched_group_lines=match.group_lines,
            decisive_rule_lines=match.rule_lines,
            reason_code=match.reason,
            parser_version=PARSER_VERSION,
            crawl_delay=match.delay,
        )
        self.decisions[key] = result
        return result

    async def evaluate_all(self, url: str) -> tuple[RobotsDecision, ...]:
        return tuple(
            [
                await self.evaluate(url, token)
                for token in (
                    self.own_token,
                    *(e.product_token for e in self.registry.entries),
                )
            ]
        )

    async def authorize(self, url: str) -> RequestAuthorization:
        decisions = await self.evaluate_all(url)
        own = decisions[0]
        return RequestAuthorization(
            own.decision,
            own.reason_code,
            (own.robots_evidence_id,) if own.robots_evidence_id else (),
            own.crawl_delay or 0.0,
        )

    def checks(self) -> tuple[TechnicalCheck, ...]:
        entries = {e.product_token.lower(): e for e in self.registry.entries}
        results = []
        for decision in self.decisions.values():
            entry = entries.get(decision.product_token.lower())
            results.append(
                TechnicalCheck(
                    check_id="check-" + decision.decision_id,
                    kind="robots_policy",
                    url=decision.evaluated_url,
                    page_id=None,
                    robot_id=entry.robot_id if entry else "geo-audit",
                    status="pass"
                    if decision.decision == "allowed"
                    else "fail"
                    if decision.decision == "disallowed"
                    else "unknown",
                    observation=decision.decision,
                    evidence_ids=tuple(
                        i for i in (decision.robots_evidence_id, self.registry_evidence_id) if i
                    ),
                    decisive_rule_lines=decision.decisive_rule_lines,
                    reason_code=decision.reason_code,
                    limitation=(
                        entry.interpretation_notes
                        if entry
                        else "Policy for our configured crawler only."
                    )
                    + " Not an HTTP probe or proof of visibility in generated answers.",
                    observed_at=self.runtime.time.utcnow(),
                    rule_version=PARSER_VERSION,
                    scope=decision.origin + " URL " + decision.evaluated_url,
                )
            )
        return tuple(results)
