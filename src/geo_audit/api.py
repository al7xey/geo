"""Public orchestration seam. No network runner is shipped in this foundation."""

from .budget import Budget
from .config import Step0Config
from .errors import problem
from .evidence import EvidenceStore
from .models import SiteValidationResult
from .network import DestinationChecker, Step0Client


async def validate_site(
    input_url: str,
    config: Step0Config,
    client: Step0Client | None = None,
    budget: Budget | None = None,
    *,
    destination_checker: DestinationChecker | None = None,
    store: EvidenceStore | None = None,
) -> SiteValidationResult:
    """Reserved async API; raises explicitly before I/O until policy runner is implemented.

    An unavailable implementation is not a failed observation of the requested site.
    Therefore no fabricated SiteValidationResult/site.json is returned or saved.
    """
    raise problem(
        "IMPLEMENTATION_NOT_AVAILABLE",
        "implementation",
        "Step 0 network orchestration is not implemented in this foundation.",
    )
