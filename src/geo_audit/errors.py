"""Public error envelope: no exception repr, traceback, URL or credentials."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .contracts import Contract, Identifier

ErrorCode = Literal[
    "INPUT_URL_INVALID",
    "INPUT_SCHEME_UNSUPPORTED",
    "INPUT_CREDENTIALS_FORBIDDEN",
    "INPUT_PORT_INVALID",
    "INPUT_ARTIFACT_INVALID",
    "TARGET_NOT_PUBLIC",
    "TARGET_DNS_REBINDING",
    "TARGET_PORT_FORBIDDEN",
    "TARGET_PATH_FORBIDDEN",
    "TARGET_SECURITY_UNAVAILABLE",
    "DNS_NXDOMAIN",
    "DNS_TEMPORARY",
    "NETWORK_CONNECT_ERROR",
    "NETWORK_READ_ERROR",
    "NETWORK_TIMEOUT",
    "NETWORK_CANCELLED",
    "HTTPS_TLS_CERTIFICATE_ERROR",
    "HTTPS_TLS_HANDSHAKE_ERROR",
    "HTTPS_DOWNGRADE",
    "REDIRECT_INVALID_LOCATION",
    "REDIRECT_LOOP",
    "REDIRECT_LIMIT",
    "REDIRECT_EXTERNAL_HOST",
    "FALLBACK_NO_USABLE_RESULT",
    "FALLBACK_USED",
    "HTTP_DENIED",
    "HTTP_MISSING",
    "HTTP_UNAVAILABLE",
    "HTTP_UNEXPECTED_STATUS",
    "CONTENT_EMPTY",
    "CONTENT_NON_HTML",
    "CONTENT_TOO_LARGE",
    "CONTENT_INCOMPLETE",
    "CONTENT_ENCODING_UNSUPPORTED",
    "CONTENT_DECODE_ERROR",
    "CONTENT_SUSPICIOUS",
    "CONTENT_JS_CANDIDATE",
    "ACCESS_LOGIN",
    "ACCESS_CHALLENGE",
    "BUDGET_DEADLINE",
    "BUDGET_ATTEMPTS",
    "RETRY_DEFERRED",
    "OUTPUT_WRITE_FAILED",
    "IMPLEMENTATION_NOT_AVAILABLE",
]


class ErrorDetail(Contract):
    code: ErrorCode
    category: Literal[
        "input", "target", "transport", "http", "content", "budget", "output", "implementation"
    ]
    message: str = Field(min_length=1)
    retryable: bool
    attempt_id: Identifier | None

    @model_validator(mode="after")
    def classification(self) -> Self:
        prefix = self.code.split("_", 1)[0]
        expected = {
            "INPUT": "input",
            "TARGET": "target",
            "DNS": "transport",
            "NETWORK": "transport",
            "HTTPS": "transport",
            "HTTP": "http",
            "REDIRECT": "http",
            "FALLBACK": "transport",
            "CONTENT": "content",
            "ACCESS": "content",
            "BUDGET": "budget",
            "RETRY": "budget",
            "OUTPUT": "output",
            "IMPLEMENTATION": "implementation",
        }[prefix]
        if self.category != expected:
            raise ValueError("error category differs from code family")
        if self.retryable and self.code not in {
            "DNS_TEMPORARY",
            "NETWORK_CONNECT_ERROR",
            "NETWORK_READ_ERROR",
            "NETWORK_TIMEOUT",
            "HTTP_UNAVAILABLE",
        }:
            raise ValueError("this error cannot be retried automatically")
        return self


class GeoAuditError(Exception):
    def __init__(self, detail: ErrorDetail):
        self.detail = detail
        super().__init__(detail.message)


def problem(code: ErrorCode, category: str, message: str) -> GeoAuditError:
    # Internal helper still validates the public envelope; arbitrary categories are rejected.
    return GeoAuditError(
        ErrorDetail.model_validate(
            {
                "code": code,
                "category": category,
                "message": message,
                "retryable": False,
                "attempt_id": None,
            }
        )
    )
