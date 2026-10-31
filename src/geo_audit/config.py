"""Versioned, deterministic configuration; never read environment implicitly."""

import hashlib
import json
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .contracts import ConfigVersion, Contract, PositiveSeconds, SchemaVersion
from .urls import normalize_host


class Step0Config(Contract):
    schema_version: SchemaVersion = "1.1.0"
    config_version: ConfigVersion = "step0-config-1.1.0"
    allow_http_fallback: bool = True
    allowed_hosts: tuple[str, ...] = ()
    allowed_extra_ports: tuple[int, ...] = ()
    path_scope: tuple[str, ...] = ()
    step0_deadline_seconds: PositiveSeconds = 60.0
    total_deadline_seconds: PositiveSeconds = 300.0
    step0_max_attempts: int = Field(default=15, ge=1)
    total_max_attempts: int = Field(default=150, ge=1)
    connect_timeout_seconds: PositiveSeconds = 5.0
    read_timeout_seconds: PositiveSeconds = 15.0
    write_timeout_seconds: PositiveSeconds = 10.0
    pool_timeout_seconds: PositiveSeconds = 5.0
    max_redirect_hops: int = Field(default=5, ge=0)
    max_retries: int = Field(default=2, ge=0)
    retry_delays_seconds: tuple[PositiveSeconds, ...] = (1.0, 2.0)
    retry_jitter_seconds: float = Field(default=0.2, ge=0)
    max_encoded_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    max_decoded_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    sniff_bytes: int = Field(default=4096, ge=1, le=4096)
    min_host_interval_seconds: PositiveSeconds = 1.0
    user_agent: str = Field(default="GEOAudit/0.1", min_length=1)
    accept_language: str = Field(default="ru,en;q=0.8", min_length=1)
    source_timezone: str = "Europe/Moscow"
    tls_verify: Literal[True] = True
    trust_env: Literal[False] = False
    proxy_mode: Literal["disabled"] = "disabled"
    cookie_mode: Literal["disabled"] = "disabled"
    dns_policy: Literal["public_only_pinned"] = "public_only_pinned"

    @field_validator("allowed_hosts")
    @classmethod
    def hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(normalize_host(h) != h for h in value):
            raise ValueError("allowed_hosts must be unique canonical exact ASCII hosts")
        return value

    @field_validator("source_timezone")
    @classmethod
    def timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown IANA timezone") from None
        return value

    @field_validator("user_agent", "accept_language")
    @classmethod
    def header(cls, value: str) -> str:
        if not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("header must be printable ASCII")
        return value

    @model_validator(mode="after")
    def limits(self) -> Self:
        if self.step0_deadline_seconds > self.total_deadline_seconds:
            raise ValueError("step 0 deadline exceeds shared deadline")
        if self.step0_max_attempts > self.total_max_attempts:
            raise ValueError("step 0 attempts exceed shared limit")
        if len(self.retry_delays_seconds) != self.max_retries:
            raise ValueError("supply one delay per retry")
        if any(p < 1 or p > 65535 for p in self.allowed_extra_ports):
            raise ValueError("invalid extra port")
        if len(set(self.allowed_extra_ports)) != len(self.allowed_extra_ports):
            raise ValueError("duplicate extra port")
        if any(not p.startswith("/") or "?" in p or "#" in p for p in self.path_scope):
            raise ValueError("path_scope requires absolute path prefixes")
        return self

    def fingerprint(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()
