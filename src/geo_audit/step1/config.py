"""Step 1 settings compose, rather than change, the source step 0 configuration."""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator

from ..contracts import SHA256, Contract
from ..urls import normalize_host


def fingerprint(value: Contract) -> str:
    return hashlib.sha256(
        json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Step1Config(Contract):
    schema_version: Literal["step1-1.0.0"] = "step1-1.0.0"
    config_version: Literal["step1-config-1.0.0"] = "step1-config-1.0.0"
    source_step0_config_sha256: SHA256
    max_candidates: int = Field(default=5000, ge=1, le=5000)
    max_selected: int = Field(default=30, ge=1, le=30)
    max_query_variants: int = Field(default=2, ge=1, le=2)
    max_sitemap_files: int = Field(default=5, ge=0, le=5)
    max_sitemap_urls: int = Field(default=5000, ge=1, le=5000)
    max_sitemap_depth: int = Field(default=3, ge=1, le=3)
    max_sitemap_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=5 * 1024 * 1024)
    max_robots_bytes: int = Field(default=512 * 1024, ge=1, le=512 * 1024)
    max_xml_depth: int = Field(default=64, ge=1, le=64)
    max_skip_examples: int = Field(default=100, ge=0, le=1000)
    allowed_sitemap_hosts: tuple[str, ...] = ()
    excluded_path_segments: tuple[str, ...] = (
        "logout",
        "cart",
        "basket",
        "account",
        "login",
        "search",
        "calendar",
    )
    browser_enabled: bool = False
    ua_probes_enabled: bool = False
    max_browser_pages: int = Field(default=5, ge=1, le=5)
    browser_page_seconds: float = Field(default=20.0, gt=0, le=20)
    max_browser_attempts: int = Field(default=200, ge=1, le=200)
    max_probe_pages: int = Field(default=3, ge=1, le=3)
    max_probe_agents: int = Field(default=3, ge=1, le=3)
    useful_chars: int = Field(default=300, ge=1)
    gain_chars: int = Field(default=500, ge=1)
    gain_ratio: float = Field(default=2.0, gt=1)
    gain_static_ceiling: int = Field(default=1000, ge=1)
    normalization_version: Literal["text-ws-1"] = "text-ws-1"

    @field_validator("allowed_sitemap_hosts")
    @classmethod
    def hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(normalize_host(h) != h for h in value):
            raise ValueError("service hosts require unique canonical exact names")
        return value

    @field_validator("excluded_path_segments")
    @classmethod
    def segments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s or not s.isascii() or not s.isalnum() or s != s.lower() for s in value):
            raise ValueError("exclusions require lower-case complete ASCII path segments")
        return value

    def fingerprint(self) -> str:
        return fingerprint(self)
