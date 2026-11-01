# Step1 completion audit — 01.11.2026

**Финальный gate: BLOCKED. Шаг1 не завершён.** Дефицит реализации — обязательные
crawler (§6), extraction (§7), directives/HTTPS/language/geography (§8/10), optional
diagnostics states (§9), material/CLI/offline Step2 loader (§11–14). Это не допустимое
исключение browser: кроме успешного browser пути отсутствуют обязательные static
компоненты. Нельзя скрывать их как implemented+verified. Шаг2 не реализован.

## Исходное и фактическое состояние

Вход в этот проход: main9f231de, версия пакета0.3.2; незакоммиченные модели/config/
runner и новые sitemap/discovery, pyproject+uv.lock, схемы/examples. Прочитаны цепочка
PROMPTS_STEP_1 (1–13), рабочая редакция алгоритма §§1–16/8.5, принятая матрица R/A/L,
контекст и первоначальный обзор. Исходные PDF/алгоритмы сохранены.

Проверка реальных путей подтвердила: runner.finish жёстко создаёт одну входную Page,
EXTRACTION_NOT_IMPLEMENTED и partial/SKELETON_ONLY. Step2Input существует как модель,
но его offline consumer отсутствует. load_step1_result проверяет commits/файлы
каркаса, а не готовый корпус с проверенными точными slices. Старый CLI step0 и
load-step1 не переписаны; новых команд step1/0→1 пока нет.

В этом проходе доведён §5: bounded discovery/sitemap, exact provenance ordinals,
XML/gzip caps/XXE policy, CDN service scope/own REP,33 новых теста (включая1 realTLS).
Не выдаём эти изменения за выполнение ранее порученных §§6–14.

## Каждое задание цепочки

| Промпт | Подтверждение | Статус |
|---|---|---|
|1|REQUIREMENTS/ARCHITECTURE, атомарные R/A/L, принятые Q, сохранённые источники|implemented+verified для спецификации|
|2|config/models, отдельные версии/hash names, contracts tests/generators|implemented+verified для моделей; точный text slice deferred|
|3|runtime/storage/live+saved handoff, реальные TLS/cancel/disk/expired tests|implemented+verified для каркаса|
|4|robots/registry/Protego, origin/URL REP и source lines; реальные TLS tests|implemented+verified для REP; probes отдельно deferred|
|5|discovery/sitemap/defusedxml,33 direct tests, layered gzip/CDN TLS|implemented+verified для подсистемы|
|6|нет queue/select/fetch loop; schema не заменяет код|deferred, обязательный блокер|
|7|нет trafilatura/BS/extraction/business relevance|deferred, обязательный блокер|
|8|нет интерпретатора directives/HTTPS diagnostic/language/geo|deferred, обязательный блокер|
|9|config off есть, но CONFIG_DISABLED/SAFE_EGRESS_UNAVAILABLE outputs отсутствуют|deferred static gate; browser success blocked|
|10|нет material orchestrator/CLI/user-description intake/Step2 consumer|deferred, обязательный блокер|
|11|отдельный self-review доступных путей,2 исправления, security report+scanners (S1–S3)|частично; не независимая экспертиза отсутствующих ветвей|
|12|inventory/distribution checks, discovery aggregates|частично; нет baseline полного step1/30 pages, оптимизация не заявлена|
|13|эта повторная матрица честно выявляет неполноту, регрессия/lock/scanners/build|completion blocked, не completed|

## Фактически выполненные команды

Локально Python3.12.10/uv0.9.7; зависимости из uv.lock. Пути .venv/Scripts/python.exe
и .bootstrap/Scripts/uv.exe эквивалентны README uv-командам в этом workspace.

| Проверка | Результат |
|---|---|
|uv lock --check; uv pip check|60 packages compatible|
|python -m pytest -q -ra, final tracked-only copy|500 passed/3 skipped,222.52s; полный step0 включён, все последние исправления|
|python -m ruff check src tests tools|pass|
|python -m mypy --platform win32; --platform linux|strict pass,36 source files на каждой платформе|
|оба generate_step{0,1}_artifacts.py --check|32/25 artifacts, pass; без сети|
|Bandit -r src tools, JSON only ignored tmp|pass, findings0|
|uv export --locked --no-emit-project + pip-audit --require-hashes|runtime/dev hashed audit pass; advisory DB доступна; No known vulnerabilities found|
|uv build --out-dir tmp/step1-package; check_distribution.py|pass, wheel43/sdist45 ordinary files|
|git diff --check; source/PDF diff|pass; source/steps без изменений|

Skips: Windows symlink privilege1314 (test_acceptance.py:384), POSIX FIFO
(test_contracts.py:338), POSIX directory-FD semantics (:368). Linux mypy — проверка
типов, не Linux runtime. CI факты для публикуемого commit фиксируются отдельно ниже;
новый HEAD нельзя объявлять cross-platform verified по прошлому CI.

## Сценарии §15 без пропусков

| ID | Фактическое подтверждение/незакрытый пункт |
|---|---|
|A01 useful one-page|deferred: нет extraction/material; skeleton не limited|
|A02 three legal pages|deferred: нет business heuristic,300 chars не принимаются за доказательство|
|A03 URL-specific REP|verified component: test_html_redirect_checks_new_origin_and_url_before_get; sitemap forbidden-page test; complete crawl deferred|
|A04a404/A04b503|verified component: test_complete_status_table_and_cached_unknown; test_limited_standard_paths_and_unknown_keeps_html_links|
|A05 external REP redirect|verified: test_external_robots_redirect_binds_initial_origin_without_html_scope_expansion; real robots TLS tests|
|A06a merged/A06b overlapping|verified: test_rfc_vectors_and_exact_lines; decisive-line/mismatch regressions|
|A07a absent/A07b damaged/A07c cycle|verified discovery: test_limited_standard_paths_and_unknown_keeps_html_links; test_malformed_map_entries_are_not_admitted_and_links_survive; test_cycles_duplicates_depth_and_global_file_limit; full crawler deferred|
|A08 query trap|deferred: discovery5000 bound tested;2 query variants/path не enforced|
|A09 multivalue X-Robots-Tag|capture в runtime tested; recipient interpretation/technical checks deferred|
|A10a noindex/A10b missing page|deferred: нет indexing interpreter; никаких новых pass не выдумано|
|A11 Google-Extended|registry policy/null UA verified, probe not_applicable output deferred|
|A12a JS gain/A12b real egress|blocked/deferred; настоящий browser не запускался, mocks не принимаются|
|A13a absent browser/A13b unavailable egress|deferred: даже capability result gate ещё нет; небезопасный browser не запускался|
|A14 UA403/base200|deferred: probes отсутствуют/off по config|
|A15 user description|deferred: модель user_provided есть, intake/semantic quality/check immutability ещё нет|
|A16a deadline/A16b Retry-After|runtime unit+realTLS verified; shared Budget/partial evidence. Candidate terminalization всего crawler deferred|
|§15.2 actual counters|runtime/robots/maps фактические transport journals сверены; full crawler partition нет|
|§15.2 integrity/versions|contracts+storage reload/unknown version/mixed generation tested; exact Step2 text slice/no-network consumer deferred|
|§15.2 cancel/disk/source0|runtime cancel/ENOSPC/EACCES/repeated commit/readonly source tests verified|
|§15.3 readiness|BLOCKED: понятный согласованный skeleton не заменяет согласованный готовый корпус|

## Безопасность, расходы и чистота

См. [SECURITY_REVIEW](STEP_1_SECURITY_REVIEW.md) и
[PERFORMANCE](STEP_1_PERFORMANCE.md). Исправлены противоречивый SitemapEntry и source
ordinal; отключений gates/TLS/suppressions нет. Secret-pattern/track-run scan пуст,
tracked удалений нет. Неизвестные пользовательские untracked/ignored не тронуты;
PDF/PSL/docs/tests/tools сохранены. Полный performance comparison не выполнен:
30 discovered не равно30 fetched/extracted. Browser integration и полный Step2 e2e
не выполнены и не принимаются по mock. C1 — известный обязательный blocker доставки;
утверждения «нет незакрытых P0/P1 и всё готово» нет.

## Короткое воспроизведение доступных компонентов

```powershell
uv sync --locked --python 3.12
uv run --locked pytest -q tests/test_step1_contracts.py tests/test_step1_runtime.py tests/test_step1_sitemap.py tests/test_step1_sitemap_integration.py
uv run --locked python tools/generate_step1_artifacts.py --check
```

Это тесты, без публичных аудитов/LLM. Рабочую CLI цепочку0→1→Step2 пока привести нельзя.
Истёкший handoff требует нового step0, таймер не обновлять. Следующий обязательный
этап реализации — prompt6, затем7–10; только после этого финальные security/perf/
clean-checkout/browser capability acceptance могут закрыть completion.

## Публикация и изолированная проверка

Новый venv tmp/step1-wheel-env:59 runtime/dev dependencies из hashed export, wheel
установлен --no-deps; Python -I подтвердил импорт geo_audit из site-packages, не src.
После S3 wheel пересобран/переустановлен:7 passed (6 realTLS runtime/robots/sitemap +
golden legacy config hash),31 deselected,1 PytestAssertRewriteWarning для ранее
импортированного anyio. Это предупреждение не является browser/Step2 тестом.

Две отдельные копии только index tree (git write-tree + git archive), без неизвестных
untracked/ignored: uv --no-cache sync --locked --python3.12 создал новые venv и скачал
60 packages. Первая candidate-копия:499 passed/3 skipped,240.55s; Ruff/оба strict mypy/
оба generators pass. После исправления S3 создана новая копия final-check; её итог
фиксируется ниже. Последние изменения отчётов после snapshot не меняют runtime/tests.

CI workflow сохранил прежние step0 checks, pinned actions/read-only permissions/
15min timeout и добавил только step1 generator --check. CI нового HEAD здесь не
объявлен passed: после push фактический статус сообщается отдельно. До реальных
Windows/Linux job results кроссплатформенная приёмка нового кода не подтверждена.

Staged diff проверен адресно: исходники/тесты/генерируемые schemas/lock/docs/CI,
без tmp/venv/cache/run data, без пользовательских удалений. Автор local alexey,
189361628+al7xey@users.noreply.github.com; origin https://github.com/al7xey/geo.git,
main, без force. Временные test environments остались ignored: auto-review отклонил
recursive cleanup (blocked by policy); маленькие свои scripts удалены адресно.

Итог final-check:500 passed/3 skips,222.52s; Ruff, strict mypy win32/linux,
generators32/25 pass из нового окружения. Runtime/tests/config этой копии совпадают
со staged версиями; последующие изменения — только эти фактические итоги в docs.
Политика свежести Step2 ещё не задана вместе с отсутствующим consumer; live300s
deadline не выдаётся за будущую offline freshness policy.

## Поатомарная сверка матрицы

Ниже у каждого исходного R есть статус. «Модель» подтверждает только контракт,
не поведение pipeline; компонентный V не является сквозной приёмкой всего step1.

| ID | Статус | Прямое подтверждение или ограничение |
|---|---|---|
| R01.01 | verified component | runner/handoff; runtime tests: same_process_reuses_identity, saved_input_new_process, invalid_input, detached_handoff |
| R01.02 | verified component | runner/handoff; runtime tests: same_process_reuses_identity, saved_input_new_process, invalid_input, detached_handoff |
| R01.03 | verified component | runner/handoff; runtime tests: same_process_reuses_identity, saved_input_new_process, invalid_input, detached_handoff |
| R01.04 | verified component | runner/handoff; runtime tests: same_process_reuses_identity, saved_input_new_process, invalid_input, detached_handoff |
| R01.05 | verified component | runner/handoff; runtime tests: same_process_reuses_identity, saved_input_new_process, invalid_input, detached_handoff |
| R01.06 | deferred | Полной runtime queue/final stop семантики нет, только skeleton. |
| R01.07 | verified component | robots.evaluate/fetch_html; test_html_redirect_checks_new_origin_and_url_before_get, registry no_probes |
| R01.08 | verified component | robots.evaluate/fetch_html; test_html_redirect_checks_new_origin_and_url_before_get, registry no_probes |
| R01.09 | verified component | robots.evaluate/fetch_html; test_html_redirect_checks_new_origin_and_url_before_get, registry no_probes |
| R01.10 | deferred | Полной runtime queue/final stop семантики нет, только skeleton. |
| R02.01 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.02 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.03 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.04 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.05 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.06 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.07 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.08 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.09 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.10 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.11 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.12 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.13 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.14 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R02.15 | verified component | robots.py/http_client; complete_status_table, failure_resources, five_safe_redirects + real robots TLS |
| R03.01 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.02 | deferred | Нет всех selected URLs, потому что crawler не реализован. |
| R03.03 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.04 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.05 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.06 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.07 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.08 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R03.09 | verified component | ParsedRobots/RobotsService; rfc_vectors_and_exact_lines, parser_disagreement, crawl_delay, HTML denial tests |
| R04.01 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R04.02 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R04.03 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R04.04 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R04.05 | deferred | Registry/policy есть; отдельного observed_access/probe N/A результата нет. |
| R04.06 | deferred | Registry/policy есть; отдельного observed_access/probe N/A результата нет. |
| R04.07 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R04.08 | verified component | registry.py/robots.checks; test_registry_roles_null_ua_snapshot_and_no_probes |
| R05.01 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.02 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.03 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.04 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.05 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.06 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.07 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.08 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.09 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.10 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.11 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R05.12 | verified component | discovery.py/sitemap.py; прямое соответствие R05→tests в REQUIREMENTS,33 cases |
| R06.01 | verified model | Candidate; test_roundtrip_schema_and_distinct_versions, graph_invariants |
| R06.02 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.03 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.04 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.05 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.06 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.07 | verified component | urls.resolve_location/normalize_input; test_namespace_loc_publisher_metadata_and_exact_ordinals (query order); full queue deferred |
| R06.08 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.09 | verified component | runtime.fetch_html/robots.authorize/http_client; new_origin/redirect tests + real TLS |
| R06.10 | verified component | runtime.fetch_html/robots.authorize/http_client; new_origin/redirect tests + real TLS |
| R06.11 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.12 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R06.13 | deferred | Нет deterministic queue/crawler/aliases/2 query variants/terminal coverage loop. |
| R07.01 | verified component | storage.observe/write_evidence; runtime privacy/multivalue/readonly/observer tests; производный gzip chain test; extraction deferred |
| R07.02 | verified component | storage.observe/write_evidence; runtime privacy/multivalue/readonly/observer tests; производный gzip chain test; extraction deferred |
| R07.03 | verified component | storage.observe/write_evidence; runtime privacy/multivalue/readonly/observer tests; производный gzip chain test; extraction deferred |
| R07.04 | verified component | storage.observe/write_evidence; runtime privacy/multivalue/readonly/observer tests; производный gzip chain test; extraction deferred |
| R07.05 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.06 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.07 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.08 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.09 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.10 | verified model | Page fields; contracts roundtrip/nullable/extra/refs; extraction значений отсутствует |
| R07.11 | verified model | Page fields; contracts roundtrip/nullable/extra/refs; extraction значений отсутствует |
| R07.12 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.13 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R07.14 | deferred | Нет extraction/normalization/material; bounds модели не проверяют actual text slices. |
| R08.01 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.02 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.03 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.04 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.05 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.06 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.07 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.08 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.09 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.10 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.11 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.12 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.13 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.14 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.15 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R08.16 | deferred | Raw fields доступны; нет интерпретатора directives/HTTPS diagnostic/checks. |
| R09.01 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.02 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.03 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.04 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.05 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.06 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.07 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.08 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.09 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.10 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.11 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.12 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.13 | blocked | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R09.14 | deferred | Нет optional diagnostics runner/capability result gate; browser не реализован. |
| R10.01 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.02 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.03 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.04 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.05 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.06 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.07 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R10.08 | deferred | Config/types существуют, но probes/language/geo consumers не реализованы. |
| R11.01 | deferred | Каркас budgets есть, полные quotas/final status/CLI summary отсутствуют. |
| R11.02 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.03 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.04 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.05 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.06 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.07 | verified component | Budget/SafeHttpClient/runtime;15 starts,total cap,cancel,Retry-After,real TLS runtime tests; browser deferred |
| R11.08 | deferred | Каркас budgets есть, полные quotas/final status/CLI summary отсутствуют. |
| R11.09 | verified component | runtime/Step1Store; repeated publish/expired/detached handoff tests |
| R11.10 | deferred | Каркас budgets есть, полные quotas/final status/CLI summary отсутствуют. |
| R12.01 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.02 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.03 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.04 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.05 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.06 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.07 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.08 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.09 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.10 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R12.11 | deferred | Нет material/business heuristic/intake user description/status orchestrator. |
| R13.01 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.02 | verified component | Step1Store.publish; write_failure/mixed_generation tests; manifest before commit |
| R13.03 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.04 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.05 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.06 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.07 | verified model | models.py; contracts roundtrip/evidence_and_identity/graph_invariants/terminal_gates |
| R13.08 | deferred | Check status interpretation не реализован. |
| R14.01 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.02 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.03 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.04 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.05 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.06 | verified component | storage.py/secure_io; readonly_source, write_failure, mixed_generation, layered gzip reload |
| R14.07 | verified component | storage.py/secure_io; readonly_source, write_failure, mixed_generation, layered gzip reload |
| R14.08 | verified component | storage.py/secure_io; readonly_source, write_failure, mixed_generation, layered gzip reload |
| R14.09 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.10 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R14.11 | deferred | Нет сквозного pipeline/Step2 loader; модель не заменяет consumer. |
| R15.01 | deferred | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R15.02 | deferred | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R15.03 | deferred | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R15.04 | deferred | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R15.05 | blocked | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R15.06 | deferred | Полная приёмка отсутствующего pipeline не выполнена; компонентные тесты указаны выше. |
| R16.01 | verified component | source diff unchanged; readonly_source + registry snapshot tests; принятые Q/архитектура |
| R16.02 | deferred | Источники/versions проверены для реализованного; будущие policy/extractors ещё нет. |
| R16.03 | deferred | Источники/versions проверены для реализованного; будущие policy/extractors ещё нет. |
| R16.04 | verified component | source diff unchanged; readonly_source + registry snapshot tests; принятые Q/архитектура |
| R16.05 | verified component | source diff unchanged; readonly_source + registry snapshot tests; принятые Q/архитектура |
