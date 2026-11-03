# Project context

This project provides an evidence-first technical GEO site audit in Python3.12+.
Steps0–1 implement URL/scope validation, pinned HTTP/TLS, robots, bounded sitemap and
HTML discovery/crawl, extraction, technical checks and an offline corpus handoff.
Technical recommendations are deterministic observations, not measured AI visibility.
The supplied tools/ programs implement profile/questions (step2), human approval
(step3) and model-answer collection (step4); their source is kept unchanged.
`geo-audit serve` provides a minimal loopback-only web UI over the same audit pipeline;
it displays coverage, technical checks, signals and evidence-linked recommendations.

Runtime is under `src/geo_audit`, controlled tests under `tests`, generated schemas
and synthetic contract examples under `schemas` and `examples/contracts`.
`uv.lock` pins dependencies; README lists install, CLI and verification commands.
Private authoring documents and audit data belong only in ignored `private/`.

Safety invariants: a single pinned client and shared0–1 Budget, per-target robots,
no scope expansion through service resources/canonical, bounded parsing/decoding,
read-only source and new publication namespaces, hashes/IDs/quotes verified offline.
Browser/UA modes are off by default. Browser rendering is unavailable without a
verified actual-egress adapter; enabling it returns unknown, never an unsafe launch.

Integration 0-8: the supplied upstream runtime and tools for steps 0-4 are imported
unchanged; hashes are pinned in UPSTREAM_0_4_SHA256.json. Only steps 5-8 adapt their
exports: step5.collector validates approved questions/profile, selects the latest
collector slot, preserves branded exclusions/ambiguity and marks systems failing
the collector's strict >20% rule as collection_eligible=false. No raw API envelopes
are opened. step5.workflow runs/publishes/verifies the whole offline tail; bounded
per-operation replay reuse accelerates steps6/7 without skipping file integrity.
tools/steps0_8.py delegates prepare/review/collect to the unchanged upstream scripts,
then analyzes saved answers. No paid calls occur in analyze; live tools require
normal upstream credentials and operator invocation. Personal prompts stay private.
See INTEGRATION_0_8.md. Model review executors and percentages remain future work.

Portable source-checkout launcher: run.py setup installs pinned uv into .bootstrap
and locked runtime dependencies into .venv. Other commands do not install packages.
run.py demo converts the tracked synthetic fixture into collector-shaped input,
runs/verifies steps5-8, and prints the local report without network or browser use.
See PORTABLE_RUN.md. CI covers Windows, macOS and Linux; controlled Windows tests
use SelectorEventLoop only for the TLS-handshake deadline regression; subprocess
tests retain the default Proactor loop. No upstream runtime files are changed.
