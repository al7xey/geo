"""Public report data excludes execution prompts and raw API envelopes."""

from typing import Literal

from ..contracts import Contract
from ..step9.models import Aggregates
from ..step10.models import Gaps
from ..step11.models import Findings, Plan


class ReportData(Contract):
    schema_version: Literal["report-data-1.0"] = "report-data-1.0"
    aggregates: Aggregates
    findings: Findings
    plan: Plan
    gaps: Gaps
    site_context_status: Literal["not_supplied", "verified_step1"]
    model_calls: Literal[0] = 0
    network_requests: Literal[0] = 0


SCHEMA_MODELS = (ReportData,)
