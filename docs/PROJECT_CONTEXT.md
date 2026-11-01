# Project context

This project provides an evidence-first technical GEO site audit in Python3.12+.
Steps0–1 implement URL/scope validation, pinned HTTP/TLS, robots, bounded sitemap and
HTML discovery/crawl, extraction, technical checks and an offline corpus handoff.
Technical recommendations are deterministic observations, not measured AI visibility.
No company profile, LLM calls or paid API integration is implemented.
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
