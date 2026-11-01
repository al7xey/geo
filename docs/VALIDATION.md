# Technical acceptance

Release candidate 0.4.0, 2026-11-02. Baseline: 8e5fbde (runtime, robots and sitemap
discovery existed; the complete static pipeline did not). This is a code/path review
and controlled acceptance, not an independent security assessment or a public audit.
Personal source material and authoring reports are archived outside the public tree
in ignored `private/`; runtime and tests do not depend on that archive.

## Implemented scope

Strict contracts and independent step1 versions; shared runtime/budget; deterministic
crawl; bounded XML/gzip; raw-before-transform extraction; directives, TLS/access and
language/geographic observations; optional UA probes; material/description; committed
publication and offline corpus consumer; deterministic technical recommendations.
Old valid step0 artifacts/gates remain readable. Browser capability gate is delivered;
successful rendering/private-egress integration is **deferred and unverified**.
No LLM, company profile or measured visibility is delivered.

## Scenario evidence

| Scenario | Direct controlled evidence |
|---|---|
| Useful one-page site; three business pages | `test_step1_pipeline.py`: limited/usable, exact corpus and attempts |
| Three long legal pages | same: insufficient, no can_start_step2 |
| URL-specific robots; 404 vs 503; external robots redirect | `test_step1_robots.py`, `test_step1_robots_integration.py`: rule lines, transport logs, original origin |
| Specific/merged/wildcard groups, encodings, tie, empty rule, delay | robots tests: parser/explainer agreement and source evidence |
| Missing/damaged/cyclic/namespace/XXE/gzip-bomb sitemap | `test_step1_sitemap.py`: bounded parse and continuing links |
| Parameter traps, aliases, exact exclusions, scope/redirect | `test_step1_crawler.py`, runtime tests: deterministic slots, no forbidden GET |
| Multivalue/addressed directives; noindex vs unread unknown | pipeline/modes tests: complete evidence, truncation never gives pass |
| Google-Extended | registry/robots/modes: policy present, HTTP UA null, no probe |
| TLS vs HTTP access, 403, timeout, fallback | modes and connection integration: independent checks and matching evidence |
| Extraction: short/malformed/encoding/table/dual methods/limits | `test_step1_modes.py`: no duplicate concatenation, repeated table values retained |
| User description | pipeline/real TLS tests: own provenance, technical fail unchanged |
| Disabled modes; enabled unavailable browser | pipeline/modes: CONFIG_DISABLED vs SAFE_EGRESS_UNAVAILABLE |
| JS adds text | only pure gain boundaries tested; real DOM/egress branch deferred |
| UA 403 vs baseline 200 | modes: observed environment difference, common conditions, max3x3 |
| Budget/Retry-After/cancel/disk/repeated commit | runtime/modes tests: no timer reset, completed observations retained, write failure explicit |
| Offline consumer: unknown version/broken hash/offset/material | pipeline and subprocess integration: socket/DNS/extractor forbidden |
| Real HTTP counts and source preservation | TLS + saved-handoff CLI integration: logs equal attempts, no second entry GET, source snapshot unchanged |
| Technical recommendations | `test_recommendations.py`: offline, references, deterministic recomputation, tamper rejection |

## Review decisions and residual risks

All request purposes share SafeHttpClient with numeric pin, peer check, original
Host/SNI and certificate verification. A purpose policy is a smaller security surface
than a second transport. Service robots redirects do not grant HTML scope; service
sitemap CDN requires its own allowlist and robots. Extraction receives content only;
it does not call a library URL loader. No browser is launched.

Fixed findings have negative regressions: pre-DOM work bomb (P1); Retry-After bypass
via another probe identity (P1); metadata truncation giving false pass (P2); header
evidence mismatch (P2); cancellation losing completed probes (P2); self-link source
loss (P2); Windows CLI encoding (compatibility); repeated table value loss (correctness).
Installed-wheel testing also exposed a clock-sampling race in saved handoff startup:
wall time sampled after the monotonic snapshot could falsely reject a valid budget.
A deterministic 20ms scheduler-delay regression reproduced the failure. A repeated
wheel run also caught Windows wall/monotonic precision differences. The detached
budget now must carry its exact verified restoration snapshot; after restore its
monotonic clock is authoritative. Restoration work is charged, freshness/reset gates
are retained, and a newly constructed budget cannot impersonate a restored one.
No known open P0/P1 remains in the delivered static scope. Hashes establish integrity,
not a signature against an owner replacing the entire directory and all hashes.
Native parsing deadlines are cooperative with strict size/work caps, not a hard CPU
or RAM sandbox. No safe-browser claim is inferred from mocks or route interception.

Selected library versions: HTTPX0.28.1/httpcore1.0.9, Protego0.7.0,
defusedxml0.7.1, trafilatura2.3.1, BeautifulSoup4.15.0, lxml6.1.3 (lock).
Official references reviewed during implementation (2026-11-01/02):
[RFC9309](https://www.rfc-editor.org/rfc/rfc9309.html),
[Protego](https://github.com/scrapy/protego),
[Sitemaps](https://www.sitemaps.org/protocol.html),
[defusedxml](https://github.com/tiran/defusedxml),
[trafilatura API](https://trafilatura.readthedocs.io/en/latest/corefunctions.html),
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/),
[lxml parser](https://lxml.de/parsing.html),
[Google directives](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).
Operator URLs, checked date and interpretation limits also travel in the versioned
runtime registry. Sources are dated observations, not permanently current guarantees.

## Performance and cleanliness

`tools/benchmark_step1.py`: same fake transport fixtures, full chain/save/offline
consumer, three repetitions. Real wall clock measured separately from tracemalloc;
fake clock controls budget logic only. Samples stay in ignored tmp. Median milliseconds
and maximum Python allocation peak MiB (candidate baseline, not a claimed speedup):

| Fixture | Wall ms | Peak MiB | Attempts | Selected |
|---|---:|---:|---:|---:|
| one page | 1300.9 | 67.6 | 2 | 1 |
| 30 pages | 7239.8 | 64.1 | 31 | 30 |
| large sitemap | 24000.9 | 68.5 | 32 | 30 |
| parameter trap | 10033.2 | 64.7 | 3 | 2 |
| robots error | 1610.4 | 64.1 | 4 | 2 |
| deadline/Retry-After | 1484.0 | 64.1 | 3 | 2 |

Selection, text hashes, material, coverage and attempt invariants agree on replays.
Peak is Python allocations, not OS RSS. Pre-pipeline baseline cannot be meaningfully
compared for full-pipeline speed. Proven alias optimization removes one duplicate
target GET (5 to4 attempts in its regression); slots remain3, unique page remains1.
No concurrency or speculative abstraction was added for benchmark appearance.

Only authoring documents/PDF tools and PDFs were removed from the public tree;
originals preserved privately. Runtime, PSL/provenance, lock, tests/fixtures, generated
contracts and CI remain. No unknown user file was deleted. Existing history was not
rewritten. Distribution allowlist excludes private/docs/tests/audit runs/venvs.

## Execution record

Commands are reproduced in README. Local Windows: Python3.12.10, uv0.9.7,
Ruff0.14.3, strict mypy1.18.2 (both platform settings), Bandit1.9.4,
pip-audit2.10.1. Full final tracked-only run and installed-wheel acceptance are
recorded below after execution; local skips require CI runtime evidence, not mypy.

- Tracked-only fresh archive, no previous venv/cache: `uv --no-cache sync --locked
  --python 3.12`, full `pytest -q -ra`: **559 passed, 3 skipped**, 456.19 seconds.
  Subsequent clock fixes: **40 passed** (runtime + real pipeline + local web API).
- Ruff src/tests/tools, strict mypy Windows/Linux, Bandit src/tools passed;
  generators checked32/28 artifacts, lock and pip compatibility passed.
- Fresh hashed runtime/dev dependency audit: **No known vulnerabilities found**;
  advisory DB available. No advisory was suppressed.
- Wheel/sdist allowlist:53/55 files. Isolated installed import is under site-packages,
  never checkout src; installed-package controlled acceptance: **59 passed**, 104.45s.
  After the optional-description UI addition, the installed UI and saved-handoff
  subprocess paths were rechecked separately (2 passed).
- Local skips: Windows symlink privilege1314; POSIX FIFO and directory-FD tests.
  CI retains mandatory real symlink execution and both OS runtime jobs. Their live
  outcome is reported separately; pending is not passed.
- Final public inventory before the web addition:146 tracked, no unknown untracked,
  no tracked audit output or secret-pattern matches. This scan is not a proof that
  arbitrary secrets cannot exist. Ignored files are explicitly excluded from packaging.

The local web presentation uses Python3.12 `HTTPServer` bound only to127.0.0.1,
Host/Origin/token checks, CSP, JSON/textContent, bounded input and one active audit.
No filesystem-serving route or remote binding is offered. It is a local development
UI, not a public production server ([official API](https://docs.python.org/3.12/library/http.server.html),
checked2026-11-02). API/CSRF/host/path/queue/output tests use real loopback HTTP;
frontend JavaScript syntax is checked with Node24.19.0. Visual browser automation
has not been performed. Closing cancels the task so the existing runtime can save
completed observations; forced termination/native-call deadlines remain limitations.
