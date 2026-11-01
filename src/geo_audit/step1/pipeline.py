"""Public 0→1 and saved-handoff execution; no LLM or company profile creation."""

from pathlib import Path

from ..api import validate_site
from ..budget import Budget
from ..config import Step0Config
from ..handoff import prepare_step1
from ..http_client import SafeHttpClient
from ..models import SiteValidationResult
from ..timing import TimeServices
from .checks import https_alternative
from .config import Step1Config
from .diagnostics import optional
from .material import description_input, save_description
from .models import Step1Result
from .runner import Step1Runtime


async def run_step1(
    runtime: Step1Runtime, *, description: str | None = None, description_file: Path | None = None
) -> Step1Result:
    description = description_input(text=description, file=description_file)
    async with runtime:
        if description is not None:
            runtime.user_materials = (save_description(runtime, description),)
        await runtime.crawl_pages()
        runtime.diagnostics = await https_alternative(runtime)
        await optional(runtime)
        runtime.pipeline_completed = True
        return runtime.finish(runtime._stop_reason or "COMPLETED")


async def audit_site(
    url: str,
    *,
    config: Step0Config | None = None,
    step1_config: Step1Config | None = None,
    output_dir: Path = Path("private/runs"),
    description: str | None = None,
    description_file: Path | None = None,
    client: SafeHttpClient | None = None,
    timing: TimeServices | None = None,
) -> Step1Result | SiteValidationResult:
    description = description_input(text=description, file=description_file)
    config = config or Step0Config()
    clock = timing or (client.time if client else TimeServices())
    shared = Budget(config, timing=clock)
    client = client or SafeHttpClient(config, timing=clock)
    site = await validate_site(url, config, client, shared, output_dir=output_dir, timing=clock)
    if not site.can_enter_step1:
        return site
    if not isinstance(site._run_directory, Path):
        raise ValueError("step0 did not return a persisted source directory")
    runtime = Step1Runtime(
        prepare_step1(site, budget=shared, timing=clock),
        source_directory=site._run_directory,
        output_dir=output_dir,
        config=step1_config,
        client=client,
        timing=clock,
    )
    return await run_step1(runtime, description=description)
