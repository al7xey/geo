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

Collection preflight shares workflow.validate_layout with offline analysis:
destinations must be new and disjoint from all source folders; existing path
ancestors are checked with pinned_directory before any paid collector process.
Unavailable filesystem roots fail without looping. The portable launcher reports
interpreter/process startup OSError as a short message and exit2.

Steps9-12 are implemented on feature/steps-9-12. step9 replays step8 and computes
equal-question rates with question-cluster bootstrap, missingness and explicit
provenance; stdlib only, no new dependencies. step10 selects lexical gap candidates
and emits one disabled bounded request; imported text is a draft with page/quote/
fact/number validation. step11 makes evidence-linked findings and a fixed-period
plan. step12 renders escaped offline HTML and report_data.json with numeric registry
validation. step12.workflow binds all adjacent publications and final downloads.
run.py finish and demo --through12 expose the offline chain. run.py sources requires
explicit --allow-network, reuses the unchanged pinned/robots-aware crawler and limits
inspection to five selected pages; it is never invoked by demo/finish. All model
executors remain disabled. See STEPS_9_12.md. No live audits or paid APIs were run.

React/TypeScript frontend: frontend/src uses app/pages/widgets/features/entities/shared
with original itworx CSS and bundled local fonts. run.py ui-setup builds assets;
run.py ui starts loopback app_web server. Test-only identity; no real accounts.
Default mode uses synthetic answers; --live explicitly enables fixed HTTPS provider
adapters configured from ignored .env. Live collection is bounded to 20 questions,
one repeat per provider, 800 output tokens/request, zero retries. Per-project
exclusive attempt ledger prevents repeat billing. Provider failures are persisted
and remaining questions for that provider skipped; no fixture substitution.
Existing steps0-4 source is unchanged. The UI prepares approved-format questions
from the GoGetLinks scenario or accepts existing collector exports, then uses
steps5-12 for analysis. Site crawling, search tools and independent model review
are not enabled by --live. All private inputs, raw responses and usage stay private.
See FRONTEND.md.

Frontend integration follow-up: fresh live projects start without fixture questions.
Explicit prepare invokes unchanged geo_pipeline via tools/ui_prepare.py (cheap,
5 pages, no extra web brief), then upstream step3_review validation/save on human
confirmation. The bounded web collector reads that exact approved handoff; the
original CLI collector/search/repeat semantics remain separately available.
Step1 context is passed to step9. Explicit live runs with generated profiles also
use a bounded OpenAI blind-review executor (20 tasks, no retries, quotes validated)
and one Claude gap-drafting job when sufficient site evidence exists. Step6 remains
rules; step7 measures agreement with rules, not an invented Haiku primary run.
No new calls were made after the user's instruction to finish configuration only.
Gemini configuration moved to gemini-3.5-flash-lite from the supplied upstream
routing; the revised connection was not exercised. Test results stay private.
