"""GEO audit step 0 foundation: contracts and explicitly unfinished orchestration."""

from .api import FetchResult, validate_site
from .budget import Budget
from .config import Step0Config
from .errors import ErrorDetail, GeoAuditError
from .handoff import Step1Handoff, load_site_result, load_step1_input, prepare_step1
from .http_client import FetchTrace, HttpAttempt, SafeHttpClient
from .models import (
    BudgetSnapshot,
    EvidenceRecord,
    FetchAttempt,
    RedirectHop,
    SiteValidationResult,
    Step1Input,
)
from .timing import TimeServices

__all__ = [
    "Budget",
    "BudgetSnapshot",
    "ErrorDetail",
    "EvidenceRecord",
    "FetchAttempt",
    "FetchTrace",
    "FetchResult",
    "HttpAttempt",
    "GeoAuditError",
    "RedirectHop",
    "SiteValidationResult",
    "SafeHttpClient",
    "Step0Config",
    "Step1Input",
    "Step1Handoff",
    "TimeServices",
    "load_site_result",
    "load_step1_input",
    "prepare_step1",
    "validate_site",
]
