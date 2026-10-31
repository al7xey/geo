"""GEO audit step 0 foundation: contracts and explicitly unfinished orchestration."""

from .api import validate_site
from .budget import Budget
from .config import Step0Config
from .errors import ErrorDetail, GeoAuditError
from .http_client import FetchTrace, HttpAttempt, SafeHttpClient
from .models import (
    BudgetSnapshot,
    EvidenceRecord,
    FetchAttempt,
    RedirectHop,
    SiteValidationResult,
    Step1Input,
)

__all__ = [
    "Budget",
    "BudgetSnapshot",
    "ErrorDetail",
    "EvidenceRecord",
    "FetchAttempt",
    "FetchTrace",
    "HttpAttempt",
    "GeoAuditError",
    "RedirectHop",
    "SiteValidationResult",
    "SafeHttpClient",
    "Step0Config",
    "Step1Input",
    "validate_site",
]
