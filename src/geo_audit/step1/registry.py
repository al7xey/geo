"""Versioned operator documentation snapshot; no fetching or probes at import/runtime."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from ..contracts import Contract, UTCDateTime
from ..models import URL
from .config import fingerprint

REGISTRY_VERSION = "robots-2026-11-01.1"
GOOGLE_EXTENDED_PRODUCT = "Google-Extended"
OPENAI_DOC = "https://developers.openai.com/api/docs/bots"
CLAUDE_DOC = (
    "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-"
    "and-how-can-site-owners-block-the-crawler"
)
GOOGLE_DOC = "https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers"


class RobotEntry(Contract):
    robot_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    operator: Literal["OpenAI", "Anthropic", "Google"]
    product_token: str = Field(pattern=r"^[A-Za-z_-]+$")
    purpose: tuple[Literal["search", "training", "user_fetch", "indexing", "grounding"], ...] = (
        Field(min_length=1)
    )
    robots_applicability: Literal["documented", "may_not_apply", "usage_control"]
    http_user_agent: str | None
    documentation_url: URL
    verified_at: UTCDateTime
    registry_version: Literal["robots-2026-11-01.1"] = "robots-2026-11-01.1"
    registry_entry_state: Literal["verified", "needs_verification"]
    interpretation_notes: str

    @model_validator(mode="after")
    def http_identity(self) -> Self:
        if self.product_token == GOOGLE_EXTENDED_PRODUCT and self.http_user_agent is not None:
            raise ValueError("Google-Extended has no HTTP UA")
        if self.http_user_agent is not None and (
            not self.http_user_agent.isascii()
            or not self.http_user_agent
            or any(ord(c) < 32 or ord(c) == 127 for c in self.http_user_agent)
        ):
            raise ValueError("HTTP UA must be printable ASCII")
        return self


class RobotRegistry(Contract):
    schema_version: Literal["robot-registry-1.0.0"] = "robot-registry-1.0.0"
    registry_version: Literal["robots-2026-11-01.1"] = "robots-2026-11-01.1"
    entries: tuple[RobotEntry, ...]

    @model_validator(mode="after")
    def unique_tokens(self) -> Self:
        if len({e.robot_id for e in self.entries}) != len(self.entries) or (
            len({e.product_token.lower() for e in self.entries}) != len(self.entries)
        ):
            raise ValueError("duplicate registry identity/token")
        return self

    def fingerprint(self) -> str:
        return fingerprint(self)


def robot_registry() -> RobotRegistry:
    """Verification date has day precision, represented by UTC midnight."""
    definitions = (
        (
            "oai-searchbot",
            "OpenAI",
            "OAI-SearchBot",
            ("search",),
            "documented",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 "
            "Safari/537.36; compatible; OAI-SearchBot/1.4; "
            "+https://openai.com/searchbot",
            OPENAI_DOC,
            "Documented UA example; version may change. Search policy is distinct from training.",
        ),
        (
            "gptbot",
            "OpenAI",
            "GPTBot",
            ("training",),
            "documented",
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
            "GPTBot/1.4; +https://openai.com/gptbot",
            OPENAI_DOC,
            "Documented UA example; version may change. Training opt-out is "
            "not proof of search exclusion.",
        ),
        (
            "chatgpt-user",
            "OpenAI",
            "ChatGPT-User",
            ("user_fetch",),
            "may_not_apply",
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
            "ChatGPT-User/1.0; +https://openai.com/bot",
            OPENAI_DOC,
            "User-triggered retrieval: robots may not apply; not the Search opt-out token.",
        ),
        (
            "claude-searchbot",
            "Anthropic",
            "Claude-SearchBot",
            ("search",),
            "documented",
            None,
            CLAUDE_DOC,
            "Role/token and robots documented. No complete HTTP UA on this "
            "source; probes unavailable.",
        ),
        (
            "claudebot",
            "Anthropic",
            "ClaudeBot",
            ("training",),
            "documented",
            None,
            CLAUDE_DOC,
            "Training role; no complete HTTP UA on this source. Crawl-delay "
            "support is operator-specific.",
        ),
        (
            "claude-user",
            "Anthropic",
            "Claude-User",
            ("user_fetch",),
            "documented",
            None,
            CLAUDE_DOC,
            "User retrieval; owner robots choices documented. No complete HTTP UA on this source.",
        ),
        (
            "googlebot",
            "Google",
            "Googlebot",
            ("indexing", "search"),
            "documented",
            "Googlebot/2.1 (+http://www.google.com/bot.html)",
            GOOGLE_DOC,
            "Documented rare UA variant, not representative of every "
            "Googlebot variant or real bot infrastructure.",
        ),
        (
            "google-extended",
            "Google",
            "Google-Extended",
            ("training", "grounding"),
            "usage_control",
            None,
            GOOGLE_DOC,
            "No separate HTTP UA. Usage control does not determine "
            "inclusion/ranking in Google Search.",
        ),
    )
    return RobotRegistry.model_validate(
        dict(
            entries=tuple(
                dict(
                    robot_id=identity,
                    operator=operator,
                    product_token=token,
                    purpose=purpose,
                    robots_applicability=applicability,
                    http_user_agent=ua,
                    documentation_url=url,
                    verified_at=datetime(2026, 11, 1, tzinfo=UTC),
                    registry_entry_state="verified",
                    interpretation_notes=notes,
                )
                for identity, operator, token, purpose, applicability, ua, url, notes in definitions
            )
        )
    )
