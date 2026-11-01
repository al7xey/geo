# Шаг 1: исполнимая матрица требований

Дата: 01.11.2026. Статус документа: спецификация будущей разработки, обход не реализован.
Парный документ: [STEP_1_ARCHITECTURE.md](STEP_1_ARCHITECTURE.md).

## Основание и фактическая исходная точка

Рабочее требование — [подробный алгоритм 1.0](../steps/STEP_1_ALGORITHM_v1.md),
разделы 1–16, включая 8.5 и таблицу §15.1. Исходный PDF в `docs/source/` и его
извлечённый текст сохранены. В исходнике шаг 1 требует 30 страниц, 14 роботов,
robots, sitemap, извлечение и JS fallback; рабочая редакция уже заменила число
роботов документируемым реестром, а три страницы — ориентиром достаточности.
Это принятые ранее уточнения, не новые решения этого задания. Предложения
[ALGORITHM_REVIEW.md](../ALGORITHM_REVIEW.md), особенно §7, не приняты автоматически.

Изучен checkout HEAD `157edf5a714d5ababe8d06fc5a1f3418ff33be87`, пакет 0.3.2,
Python >=3.12; рабочее дерево перед работой чистое. Последний коммит добавил цепочку
промптов и контекст, но не обход. Между `88d3c1b` и HEAD изменились README,
PROJECT_CONTEXT, PROMPTS_STEP_1 и шесть отчётов STEP_0; runtime, схемы и тесты
этим диапазоном не менялись. Более свежие итоги приёмки и ручного smoke учтены;
исторические цифры тестов не выдаются за проверку нового шага.

| Область, просмотренные файлы | Что действительно готово | Что требуется расширить |
|---|---|---|
| `src/geo_audit/{contracts,models,config,schemas}.py`, все 8 `schemas/*.json`, `examples/contracts/` | Strict/frozen/extra-forbid, required nullable, UTC, версии 1.0/1.1/1.2, согласованные схемы и синтетические примеры | Отдельное семейство step1; новые сущности не помещаются в step0 Literal |
| `api.py`, `handoff.py`, `network.py`, `__init__.py`, CLI/interactive | validate_site, FetchResult, prepare_step1/load_step1_input, тот же живой Budget; загрузка без DNS/GET | Оркестратор step1 и Step2Input; Step1Input пока имеет только crawl_permission=unknown |
| `budget.py`, `timing.py`, `policy.py` | 60 s/15 step0, 300 s/150 total; UTC restore и monotonic, Retry-After | Счётчики HTML/sitemap/диагностик как квоты того же прохода |
| `http_client.py`, `connection.py`, `body.py`, `scope.py`, `urls.py`, `psl.py` | Sequential async, public numeric pin/peer/SNI/TLS, ручные цепочки, bounded decoding, scope/PSL | Resource policy, robots перед каждым HTML GET/redirect, служебные redirects, отдельные caps и заголовки |
| `content.py` | Классификация, декодирование, malformed declarations → suspicious_html | Основной текст, роли, soft-404/служебность, quality и provenance; классификатор step0 не экстрактор |
| `models.Header`, `response.headers.raw` | Tuple сохраняет повторы разрешённых полей | `x-robots-tag` отсутствует в allowlist; потерянные step0 headers нельзя восстановить без нового наблюдения |
| `evidence.py`, `secure_io.py`, `redaction.py` | Raw/observation/hash, no-follow, bounded reads, atomic site last, 64 MiB/file, 256 MiB/run, JSON 16 MiB | Отдельная публикация step1, source references, derived representations; старый publish принимает SiteValidationResult |
| `tests/{test_contracts,test_url_policy,test_http_client,test_connection_integration,test_step0_end_to_end,test_acceptance,test_interactive}.py`, `fakes.py` | Контрактные, controlled TCP/TLS, filesystem/race, new-process/no-network, CLI, clocks | Ни одного теста обхода/robots/Step2Input; новые имена ниже являются планом |
| README, `.github/workflows/step0.yml`, `pyproject.toml`, `uv.lock` | Windows/Linux, pins, strict mypy обеих платформ, Ruff/Bandit, contracts, wheel/sdist | Сохранить gates; расширять только вместе с реализацией |
| `tools/generate_step0_artifacts.py`, `check_distribution.py`, `benchmark_step0.py` | 32 детерминированных артефакта, состав пакетов, offline измерения | Новый генератор step1 с тем же принципом; не менять старые fixtures молча |
| `tools/{build_algorithm_pdfs,build_step0_prompts_pdf,check_algorithm_pdfs}.py` | Исторический документальный workflow/проверка исходников | Не runtime-зависимость; не перегенерировать PDF ради спецификации |

## Как читать матрицу

Каждая строка — отдельное проверяемое обязательство. Ссылки § относятся к подробному
алгоритму, L — к таблице лимитов ниже, Q — к вопросам методики архитектуры.
Владелец — модуль/граница ответственности, не человек. Названия модулей без `step1/`
обозначают существующий код; новые владельцы раскрыты в архитектуре.

Статусы: `V` = implemented+verified для компонента шага 0 (есть указанный
существующий тест, не утверждение о готовности step1); `D` = deferred до этапа
реализации; `B` = blocked до решения методики или доказанной безопасной среды.
`implemented+unverified` сейчас не используется: кода новых компонентов нет.
Режим `S` обязателен статическому ядру, `U` условен включённым UA probes, `J`
условен browser. Контракты выключенных режимов обязательны ядру. Все тесты с
префиксом `T` планируются в `tests/test_step1_<владелец>.py`; это не существующие
успешные тесты. В тестовой функции использовать `test_<название>` из ячейки.

## 1. Вход и подготовка

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R01.01 / 1.1 | Принимать только согласованный положительный Step1Handoff; неверный вход → INPUT_ARTIFACT_INVALID | handoff → `test_positive_contract_rejects_inconsistent_artifacts` (contracts) | S/V |
| R01.02 / 1.1 | Проверить версии, scope history, выбранный response/evidence, хэши и representations | handoff → `test_artifact_forgery_rejected` (acceptance) | S/V |
| R01.03 / 1.1 | Не делать повторный GET входа при старте | handoff → `test_success_save_reload_and_in_process_identity_without_network` (step0_end_to_end) | S/V |
| R01.04 / 1.1 | Сохранить identity живого Budget/FetchResult в одном процессе | handoff → тот же существующий тест | S/V |
| R01.05 / 1.1 | Restore учитывает простой/UTC rollback, не обновляет deadline и quotas | budget/handoff → Tbudget:restore_keeps_deadline_and_idle | S/D (основа готова) |
| R01.06 / 1.2 | Создать bounded очередь, origin map, aliases, evidence/check registries и счётчики без I/O при импорте | runner → Trunner:initial_state_import_no_io | S/D |
| R01.07 / 1.2 | Собственный GEOAudit/0.1 и Accept-Language неизменны при запрете; UA override только U | config/network → Tnetwork:no_identity_switch | S/D |
| R01.08 / 1.3 | Сохранённый вход — input_observation, даже когда robots disallowed/unknown | runner → Trunner:entry_observation_under_denial | S/D |
| R01.09 / 1.3 | crawl_permission вычислять по origin/URL отдельно от can_enter_step1 | robots → Trobots:permission_is_url_scoped | S/D |
| R01.10 / 1.4 | Deadline/attempts закрывают проход, внешний URL закрывает только ветвь, storage failure запрещает доверенный итог | runner → Trunner:stop_scope_and_storage | S/D |

## 2. Получение robots

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R02.01 / 2.1 | `/robots.txt` от scheme/host/effective-port, вне path_scope HTML; cache только внутри run | robots/network → Trobots:origin_key_and_path_exemption | S/D |
| R02.02 / 2.1 | Streaming GET, общий Budget, raw/status/headers/time/request_id/hash/original origin | robots/storage → Trobots:fetch_evidence | S/D |
| R02.03 / 2.2 | 200 корректные правила → present | robots → Trobots:present | S/D |
| R02.04 / 2.2 | 200 корректный пустой/204 → empty с наблюдённым статусом | robots → Trobots:empty_200_204 | S/D |
| R02.05 / 2.2 | 404/410 → absent, без явного REP запрета | robots → Trobots:absent_404_410 | S/D |
| R02.06 / 2.2 | 401/403 → access_denied/unknown, остановка новых страниц | robots → Trobots:denied_401_403_no_html | S/D |
| R02.07 / 2.2 | 429 → Retry-After; исчерпание ожидания/попыток → unknown | robots/network → Trobots:rate_limited | S/D |
| R02.08 / 2.2 | 5xx/временная сеть → bounded retry, затем unreachable/unknown; постоянный DNS/security без повторов | robots/network → Trobots:unreachable | S/D |
| R02.09 / 2.2 | Challenge HTML → invalid_response, не absent | robots → Trobots:html_challenge | S/D |
| R02.10 / 2.2,2.4 | 206/incomplete/overflow → incomplete/unknown, не «всё открыто» | robots → Trobots:incomplete_and_cap | S/D |
| R02.11 / 2.3 | До 5 публичных service redirects; правила для первого origin, final URL отдельно | network/robots → Tnetwork:robots_external_redirect | S/D |
| R02.12 / 2.3 | Ни service redirect, ни его PSL alias не расширяет HTML scope; unsafe/loop/limit → unknown | network → Tnetwork:service_does_not_expand_html | S/D |
| R02.13 / 2.4 | 512 KiB decoded cap, partial bytes и truncated сохраняются | network/storage → Trobots:truncated_preserved | S/D |
| R02.14 / 2.4 | Плохие строки дают warnings с номерами, корректные строки разбираются; HTML не парсится как REP | robots → Trobots:line_errors_not_html_rules | S/D |
| R02.15 / 2.4 | Различать ROBOTS_FORBIDS/UNREACHABLE/ACCESS_DENIED/TRUNCATED/INVALID_RESPONSE | contracts → Tcontracts:robots_reason_not_prohibition | S/D |

## 3. Решения по правилам

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R03.01 / 3.1 | Pin Protego и compatibility policy; не заменять regex-поиском Disallow | robots → Trobots:pinned_parser_api | S/D |
| R03.02 / 3.1 | Для каждого selected URL вычислить собственный token и все registry tokens | robots → Trobots:all_tokens_per_url | S/D |
| R03.03 / 3.2 | Решение содержит original origin, URL/token, evidence, group/rule lines, reason, parser version | contracts/robots → Trobots:decision_fields | S/D |
| R03.04 / 3.2 | Нет ограничения → пустые decisive lines; disagreement parser/explainer → unknown | robots → Trobots:explanation_mismatch | S/D |
| R03.05 / 3.3 | Token case/specific vs */multiple groups проверены | robots → Trobots:groups_case_merge | S/D |
| R03.06 / 3.3 | Empty Disallow, longest match, Allow tie, `*`, `$` проверены | robots → Trobots:rule_precedence_patterns | S/D |
| R03.07 / 3.3 | Percent encoding, Unicode и query совпадают у parser и explainer | robots → Trobots:encoded_path_query | S/D |
| R03.08 / 3.4 | Crawl-delay для своего token усиливает min interval; прочие extensions raw, без обещаний провайдерам | network/robots → Trobots:crawl_delay | S/D |
| R03.09 / 3.4 | Запрещённый URL → skipped_robots, evidence, ноль GET; sitemap не даёт исключения | runner → Trunner:no_get_on_disallow | S/D |

## 4. Реестр

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R04.01 / 4.1 | Запись: id/operator/token/purpose/applicability/nullable UA/source/verified_at/version/notes | registry → Tregistry:required_fields | S/D |
| R04.02 / 4.1 | Восемь исходных tokens из §4.1, собственный token отдельно; нет требования «14» | registry → Tregistry:initial_tokens | S/D |
| R04.03 / 4.1,4.3 | Реальные UA только по источнику; нет подтверждённой полной строки → null/needs_verification | registry → Tregistry:no_fabricated_ua | S/D |
| R04.04 / 4.2 | Training/search/user purposes раздельно; ChatGPT-User имеет оговорку применимости | checks → Tchecks:token_purpose_limits | S/D |
| R04.05 / 4.2 | Google-Extended: policy есть, HTTP UA null, probe not_applicable | diagnostics → Tdiagnostics:extended_no_probe | S/D |
| R04.06 / 4.2 | robots_policy и observed_http_response независимы; устаревшие сведения ограничивают вывод | checks → Tchecks:policy_not_observed_access | S/D |
| R04.07 / 4.3 | Snapshot реестра и его hash сохраняются; новая версия не переписывает старый run | storage/registry → Tregistry:snapshot_stable | S/D |
| R04.08 / 4.3 | Отчёт не выводит причинную GEO-невидимость из запрета токену | checks → Tchecks:no_visibility_causation | S/D |

## 5. Sitemap

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R05.01 / 5.1 | Входные ссылки первыми; затем валидные Sitemap directives | discovery → Tdiscovery:source_order | S/D |
| R05.02 / 5.1 | Если directives нет/не дали URL, только sitemap.xml и sitemap_index.xml в остатке L08 | sitemap → Tsitemap:fallback_paths_bounded | S/D |
| R05.03 / 5.1 | Все источники обнаружения сохраняются, повтор не стирает первый | discovery → Tdiscovery:merge_provenance | S/D |
| R05.04 / 5.2 | urlset/sitemapindex namespace/loc и gzip поддержаны | sitemap → Tsitemap:namespaces_and_gzip | S/D |
| R05.05 / 5.2 | DTD/entities/external I/O запрещены; incremental parsing, bounded gzip и глубина XML | sitemap → Tsitemap:xxe_bombs_depth | S/D |
| R05.06 / 5.2 | lastmod/priority/changefreq — raw publisher assertions | sitemap → Tsitemap:publisher_metadata | S/D |
| R05.07 / 5.2 | 5 файлов/5 MiB/5000 loc/depth 3; visited не допускает cycles/refetch | sitemap → Tsitemap:limits_cycle | S/D |
| R05.08 / 5.2 | 404, retries, redirects стандартных путей входят в total HTTP | budget → Tbudget:sitemap_counts | S/D |
| R05.09 / 5.3 | loc HTML вне scope → skip; CDN только в allowed_sitemap_hosts, без расширения HTML | network/sitemap → Tsitemap:external_cdn_is_service_only | S/D |
| R05.10 / 5.3 | Relative/некорректные loc пропускать с raw/reason, без скрытого inferred URL | sitemap → Tsitemap:invalid_loc | S/D |
| R05.11 / 5.4 | Missing/invalid/unreachable sitemap не останавливает ссылки | runner → Trunner:links_without_sitemap | S/D |
| R05.12 / 5.4 | Truncation: parsed/skipped-count (nullable если неизвестен), order, stop reason; нет вывода «всего N страниц» | coverage → Tcoverage:sitemap_truncated | S/D |

## 6. Очередь и coverage

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R06.01 / 6.1 | Candidate: URL/sources/role+basis/language/priority/ordinal | contracts → Tcontracts:candidate_fields | S/D |
| R06.02 / 6.1 | Приоритеты 0 entry/home, 1 about, 2 services, 3 prices, 4 contacts, 5 FAQ, 6 rest | discovery → Tdiscovery:role_order | S/D |
| R06.03 / 6.1 | Устойчивый tie-break и порядок admission; replay одинаковых входов → одинаковый selection | discovery → Tdiscovery:deterministic_replay | S/D |
| R06.04 / 6.2 | 30 selected включая вход; failed расходует слот; retry не создаёт Candidate | coverage/runner → Tcoverage:selected_slots | S/D |
| R06.05 / 6.2 | Observed redirect aliases и точные duplicates не повышают unique pages | discovery → Tdiscovery:aliases_and_duplicates | S/D |
| R06.06 / 6.2 | 5000 stored candidates; 2 query variants/path → skipped_parameter_limit | discovery → Tdiscovery:parameter_trap | S/D |
| R06.07 / 6.2 | Fragment убрать; query не удалять и не сортировать для запроса | urls/discovery → Tdiscovery:query_order_preserved | S/D |
| R06.08 / 6.3 | Не отправлять формы; configured token/path exclusions без случайных substring matches | discovery → Tdiscovery:exclusions_boundaries | S/D |
| R06.09 / 6.3 | Для нового разрешённого origin получить его robots до HTML | network → Tnetwork:new_origin_robots_first | S/D |
| R06.10 / 6.3 | Каждый HTML redirect: scope, robots точного target, public pin, budget до GET | network → Tnetwork:redirect_target_gate | S/D |
| R06.11 / 6.3 | Внешние URLs → pending_external_urls, другие ветви продолжаются | runner → Trunner:external_branch_only | S/D |
| R06.12 / 6.4 | Раздельные discovered/selected/successes/unique/extracted/useful/skipped/failed со списками IDs | coverage → Tcoverage:recompute_all | S/D |
| R06.13 / 6.4 | Canonical/equal hash — duplicate signals; сомнение сохраняет отдельные pages | discovery → Tdiscovery:no_unjustified_merge | S/D |

## 7. Текст и provenance

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R07.01 / 7.1 | Raw записать до extraction; encoded/entity/decoded/DOM — разные representations | storage → Tstorage:evidence_before_transform | S/D |
| R07.02 / 7.1 | Каждый transform: source ID/version/hash/size; исходник не заменяется | extraction → Textraction:transform_chain | S/D |
| R07.03 / 7.1 | Manifest хранит URLs/time/status/charset/completeness/redirects и multivalue allowlist | storage → Tstorage:response_metadata | S/D |
| R07.04 / 7.1 | Set-Cookie не попадает в публичные журналы/сохраняемые headers | network/redaction → Tnetwork:header_privacy | S/D |
| R07.05 / 7.2.1 | Trafilatura из уже декодированного HTML; lists/tables сохранены, comments выключены | extraction → Textraction:primary_tables_lists | S/D |
| R07.06 / 7.2.2 | Пустое/navigation/cookie-only/потерянные main blocks → quality reason | extraction → Textraction:unusable_primary | S/D |
| R07.07 / 7.2.3 | BS fallback: удалить script/style/служебные блоки, main/article при наличии | extraction → Textraction:fallback | S/D |
| R07.08 / 7.2.4 | Сохранить различающиеся candidates и причину выбора; не конкатенировать | extraction → Textraction:alternative_not_concatenated | S/D |
| R07.09 / 7.2.5,7.3 | Версионная Unicode whitespace normalization; raw extraction отдельно при смене offsets | extraction → Textraction:normalization_offsets | S/D |
| R07.10 / 7.3 | Page: IDs, URL/origin/fetch/evidence, title/h1/h2/lists/tables, method/version/chars/hash | contracts → Tcontracts:page_content_fields | S/D |
| R07.11 / 7.3 | Page: lang/hreflang/canonical/directives, quality/reasons, provenance и discovered_from | contracts → Tcontracts:page_metadata_fields | S/D |
| R07.12 / 7.3 | Не суммировать повтор заголовков/таблиц и разных representations для sufficient chars | material → Tmaterial:no_double_count | S/D |
| R07.13 / 7.4 | Citation: page/evidence/text_hash, codepoint start/end и точный fragment | handoff → Thandoff:quote_offsets | S/D |
| R07.14 / 7.4 | Сайт передаётся как недоверенные данные; команды внутри не исполняются | handoff → Thandoff:untrusted_text | S/D |

## 8. Директивы и 8.5 HTTPS

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R08.01 / 8.1 | Все X-Robots-Tag field lines без comma-collapse, raw/recipient/tokens/evidence | directives → Tdirectives:repeated_headers | S/D |
| R08.02 / 8.1 | Все общие/адресованные meta, canonical/lang/hreflang/data-nosnippet | directives → Tdirectives:html_metadata | S/D |
| R08.03 / 8.1 | Static и rendered directives раздельно | directives → Tdirectives:representation_scope | S/D |
| R08.04 / 8.2 | Пять kinds: robots_policy/indexing/snippet/canonical/observed_access | checks → Tchecks:kind_separation | S/D |
| R08.05 / 8.2 | noindex/none с адресатом; snippet restrictions не равны общему AI ban | directives → Tdirectives:documented_semantics | S/D |
| R08.06 / 8.3 | Конфликты сохранять; объединять только документированной operator policy, иначе unknown | directives → Tdirectives:conflicts | S/D |
| R08.07 / 8.3 | Незагруженная/неполная страница → unknown; отсутствие noindex утверждать только в наблюдённой области | checks → Tchecks:missing_not_pass | S/D |
| R08.08 / 8.4 | Check хранит page role, число затронутых observed URL, evidence | checks → Tchecks:affected_observed_scope | S/D |
| R08.09 / 8.4 | External canonical не вызывает GET/scope expansion; отсутствие запретов не доказывает индексирование | checks → Tchecks:canonical_no_fetch | S/D |
| R08.10 / 8.5 | Reuse TLS/redirect evidence step0 без GET; certificate fail не скрыт HTTP fallback | checks → Tchecks:reuse_tls_fallback | S/D |
| R08.11 / 8.5 | Явный HTTP без HTTPS observation: один диагностический URL той же path/query, bounded retries общего бюджета | diagnostics → Tdiagnostics:https_alternative | S/D, граница Q04 |
| R08.12 / 8.5 | TLS verified и HTTP 403 независимы; timeout/DNS не доказывают отсутствие HTTPS | checks → Tchecks:tls_vs_access | S/D |
| R08.13 / 8.5 | Downgrade warning; разные схемы/тексты/evidence не смешивать и корпус не заменять | checks → Tchecks:scheme_difference | S/D |
| R08.14 / 8.5 | Robots HTTP не переносится на HTTPS; диагностика не начинает recursion | diagnostics → Tdiagnostics:https_not_new_crawl | S/D |
| R08.15 / 8.5 | Нет бюджета → unknown; явно reused_evidence; не обещать полного TLS-аудита | checks → Tchecks:https_limitations | S/D |
| R08.16 / 8.1 + фактический Header | Старый step0 не сохранил X-Robots-Tag: headers observation incomplete; нет ложного pass и refetch ради восстановления | directives → Tdirectives:legacy_header_coverage | S/D |

## 9. Browser и JS

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R09.01 / 9.1 | JS candidates по shell/placeholder/extractor disagreement, не просто script | diagnostics → Tdiagnostics:js_selection | S/D |
| R09.02 / 9.1 | До 5 по диагностической ценности с reason и устойчивым tie-break | diagnostics → Tdiagnostics:browser_selection_cap | J/D |
| R09.03 / 9.1 | Нет пакета/Chromium/egress → enabled unknown; static продолжает работать | diagnostics → Tdiagnostics:browser_unavailable | S/D |
| R09.04 / 9.1 | Robots/challenge/login запрещают rendering как обход барьера | diagnostics → Tdiagnostics:browser_policy_skip | J/D |
| R09.05 / 9.2 | Изоляция без personal profile/auth/cookies; язык/base headers совпадают или различие записано | browser → Tbrowser:isolated_context | J/B: нет принятого backend |
| R09.06 / 9.2 | TLS verify; downloads/popups/service workers блокированы | browser → Tbrowser:block_side_effects | J/B |
| R09.07 / 9.2 | Только GET/HEAD, POST blocked и limitation, не вывод «сайт непригоден» | browser → Tbrowser:method_limits | J/B |
| R09.08 / 9.2–9.3 | Caps bytes/subrequests/time и фактический egress всех origins/iframe/XHR/redirects; route недостаточно | browser → Tbrowser:actual_peer_rebinding | J/B |
| R09.09 / 9.3 | Ресурс CDN не становится HTML candidate; без проверенного egress browser disabled | browser → Tbrowser:resource_scope | J/B |
| R09.10 / 9.4 | DOM отдельный evidence, extraction той же версии | extraction → Textraction:rendered_provenance | J/D |
| R09.11 / 9.4 | Gain ≥500 chars и ≥2× при static<1000; static=0 только абсолютный порог | diagnostics → Tdiagnostics:gain_boundaries | S/D (чистая функция) |
| R09.12 / 9.4 | observed_gain/no_material_gain/inconclusive; timeout → inconclusive | diagnostics → Tdiagnostics:js_outcomes | S/D |
| R09.13 / 9.4 | До 20 s/page, 5 pages, 200 attempts/run; bounded stability вместо unbounded networkidle | browser → Tbrowser:deadline_subrequests | J/B |
| R09.14 / 9.3,12.3 | Disabled check → not_applicable/CONFIG_DISABLED, JS capability остаётся unknown; не делать partial только из-за выключения | checks → Tchecks:disabled_vs_capability | S/D |

## 10. UA, язык, география

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R10.01 / 10.1 | UA probes default false; ≤3 pages × ≤3 подтверждённых UA | config/diagnostics → Tdiagnostics:ua_default_and_caps | S/D |
| R10.02 / 10.1 | Сравнимые Accept-Language/base headers, timestamps; reuse baseline только при совпадении | diagnostics → Tdiagnostics:baseline_conditions | U/D, Q05 |
| R10.03 / 10.1 | Общий клиент/бюджет; own robots и применимая policy до каждой probe/redirect | network → Tnetwork:ua_policy_no_bypass | U/D |
| R10.04 / 10.1 | Сравнить status/final URL/type/challenge/text; hash difference недостаточен для blocking claim | diagnostics → Tdiagnostics:ua_observation_not_robot_claim | U/D |
| R10.05 / 10.2 | Lang/hreflang по page; короткий текст unknown; library detection лишь с method/version | extraction → Textraction:multilingual_signals | S/D |
| R10.06 / 10.3 | Адрес компании и service geography с цитатами отдельно от TLD/phone clues | extraction → Textraction:geography_provenance | S/D |
| R10.07 / 10.3 | Market — user config; не страна машины; противоречия/несколько стран сохраняются | material → Tmaterial:geography_not_inferred_market | S/D |
| R10.08 / 10.3 | Нет сигналов → values=[]/unknown, не выдуманное значение | contracts → Tcontracts:empty_signals | S/D |

## 11. Лимиты и завершение

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R11.01 / 11.1 | Все L01–L11 соблюдаются как квоты общего прохода 0–1 | budget/runner → Tbudget:all_limit_boundaries | S/D |
| R11.02 / 11.2 | Reserve перед transport start, в том числе connect failure; DNS rejection не HTTP attempt | network/budget → Tbudget:journal_transport_reconciliation | S/D (step0 основа V) |
| R11.03 / 11.2 | Redirect и retry расходуют attempts, cache hit не requests_sent | budget → Tbudget:redirect_retry_cache | S/D |
| R11.04 / 11.2 | Sequential async достаточно; ≤2 active не требует параллельности, не запускать 30 задач | runner → Trunner:sequential_max_active_one | S/D |
| R11.05 / 11.3 | Общая transient policy; permanent 4xx/TLS/challenge/robots не повторять другой личностью | network → Tnetwork:retry_classification | S/D |
| R11.06 / 11.3 | Retry-After ≥ remaining → RETRY_DEFERRED без укороченного sleep/GET | network → Tnetwork:retry_after_shared_deadline | S/D |
| R11.07 / 11.4 | Deadline закрывает streams/browser, completed raw сохраняется | runner → Trunner:cancel_preserves_completed | S/D |
| R11.08 / 11.4 | run содержит stop reason/unfinished/spent/remaining; enabled checks без бюджета unknown | runner → Trunner:finalize_pending | S/D |
| R11.09 / 11.4 | Повтор — новая publication, без silent append/reset; истёкший исходный Budget требует нового step0 | storage → Tstorage:no_resume_reset | S/D |
| R11.10 / 11.4 | Сводка описывает observed coverage, не «весь сайт проверен» | CLI/checks → Tchecks:bounded_summary | S/D |

## Все пределы §11.1 и владельцы

Все значения — default config методики, изменения сохраняются в config hash.
Вход step0 с иными лимитами сохраняет их; увеличить total на входе step1 нельзя.
Снижение локальной квоты допускается только как явное дополнительное ограничение,
не как переопределение исходного deadline/total counters.

| ID | Предел | Единственный владелец/место enforcement | Учёт и проверка |
|---|---|---|---|
| L01 | 300 s всего 0–1 | существующий Budget/TimeServices | static, ожидания, parsing, storage и browser живут в исходном deadline; Tbudget:restore_keeps_deadline_and_idle |
| L02 | 150 non-browser HTTP attempts | Budget.reserve_attempt(step=1) | остаток 150 минус step0 и все следующие starts; DNS отдельно; Tbudget:journal_transport_reconciliation |
| L03 | 30 selected HTML incl entry | discovery/RunCounters | failure/robots skip после selection расходует slot; retry нет; Tcoverage:selected_slots |
| L04a | ≤2 active connections | общий сетевой исполнитель | первая версия sequential максимум 1; browser только после static и с общим gate; Trunner:sequential_max_active_one |
| L04b | starts не чаще 1/s/host | тот же SafeHttpClient pacing map | robots/sitemap/HTML/probes, max(1 s,Crawl-delay), без нового клиента; Tnetwork:cross_purpose_pacing |
| L05 | ≤2 дополнительных retries | policy/http_client | на URL в цепочке; redirect reset как step0; общий L02/L01 сильнее; Tnetwork:retry_classification |
| L06 | ≤5 redirect hops/chain | network resource policy | HTML, robots и sitemap независимо по цепочкам, но общий L02; Tnetwork:redirect_boundaries |
| L07a | HTML ≤5 MiB decoded | LimitedBody, policy HTML | encoded cap также 5 MiB из step0, сохранён; Tnetwork:html_caps |
| L07b | robots ≤512 KiB decoded | LimitedBody, policy robots | encoded cap min(step0 encoded cap,512 KiB), overflow unknown; Trobots:truncated_preserved |
| L08a | ≤5 различных sitemap files incl index | sitemap/RunCounters | резервировать файл до первой попытки (404 тоже слот), retries/redirects не новый file slot; Tsitemap:limits_cycle |
| L08b | ≤5 MiB unpacked/file | sitemap + LimitedBody | HTTP decoding и gzip file decoding оба bounded, без double unbounded decode; Tsitemap:namespaces_and_gzip |
| L08c | ≤5000 URL loc суммарно | sitemap | до admission в очередь, повторы loc тоже entries для bounded work; Tsitemap:loc_cap |
| L08d | sitemap reference depth ≤3 | sitemap | root depth=1; visited до GET; Tsitemap:depth_boundary |
| L09a | ≤5000 normalized candidates stored | discovery | reject/drop-tail bounded summary; Tdiscovery:parameter_trap |
| L09b | ≤2 query variants на path | discovery | key=(scheme,host,effective port,path), query exact, no-query один variant; Tdiscovery:query_order_preserved |
| L10a | browser ≤5 pages | RunCounters, browser | subset selected corpus, не новый HTML crawl; Tbrowser:deadline_subrequests |
| L10b | browser ≤20 s/page | browser/TimeServices | min(20,remaining total), включая bounded stabilization; тот же тест |
| L10c | browser ≤200 HTTP(S) attempts/run | RunCounters общий lifetime + egress | separately from L02, redirects/subresources/retries входят; тот же тест |
| L11a | probes ≤3 pages | diagnostics/RunCounters | subset selected eligible pages, не discovery; Tdiagnostics:ua_default_and_caps |
| L11b | probes ≤3 agents | diagnostics/RunCounters | до 9 comparisons; их retries/redirects ограничены L02/L01, baseline новый GET тоже L02; тот же тест |

Дополнительные существующие ограничения: step0 60 s/15 attempts не блокируют step1
после их исчерпания при положительном handoff и остатке total; connect/read/write/pool
5/15/10/5 s, 5 MiB encoded, sniff≤4096 bytes, no env/proxy/cookies/HTTP2/keepalive,
первый проверенный IP. Storage 64 MiB/file, 256 MiB уникальных evidence bytes
логического run, JSON≤16 MiB: не увеличивать ради 30 страниц. Preflight и incremental
reserve storage до записи; при исчерпании — явное ограничение, без неподтверждённого
положительного commit. XML element depth default 64 — защитный config cap, не
глубина sitemap=3. Все caps проверять на N−1/N/N+1. Это не новый Budget step1:
RunCounters не имеет собственного start/deadline/remaining HTTP.

## 12. Материал и статусы

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R12.01 / 12.1 | Useful: full HTML, ≥300 normalized chars, без barrier/soft-404/service dominance | material → Tmaterial:useful_boundaries | S/D, Q01 для heuristic |
| R12.02 / 12.1 | Business relevance отдельно от длины; длинная legal page не даёт usable | material → Tmaterial:three_legal_pages | S/D |
| R12.03 / 12.1 | Короткая полезная contacts → supporting, не увеличивает useful | material → Tmaterial:supporting_contact | S/D |
| R12.04 / 12.1 | ≥3 useful business → usable; 1–2 → limited, оба могут step2 | material → Tmaterial:usable_and_one_page | S/D |
| R12.05 / 12.1 | Текст без business → insufficient; нет материала → needs_description; step2=false | material → Tmaterial:insufficient_vs_none | S/D |
| R12.06 / 12.1 | Пригодное описание → user_supported, step2 gate не только page count | material → Tmaterial:user_support_gate | S/D, Q02 |
| R12.07 / 12.2 | Аргумент/файл без input(); author_type=user/time/text/source_type=user_provided | handoff → Thandoff:user_material_provenance | S/D |
| R12.08 / 12.2 | Пустое/бессодержательное описание требует уточнения; нет fake page/citation | material → Tmaterial:invalid_description | S/D, Q02 |
| R12.09 / 12.2 | Описание не исправляет robots/TLS/indexing failures | checks/material → Tmaterial:user_does_not_fix_checks | S/D |
| R12.10 / 12.3 | ok/partial/needs_input/failed вычисляются отдельно от checks pass/fail | runner → Trunner:terminal_precedence | S/D |
| R12.11 / 12.3 | Disabled → not_applicable/CONFIG_DISABLED; enabled unavailable → unknown/limitation | checks → Tchecks:disabled_vs_unavailable | S/D |

## 13. Контракты

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R13.01 / 13.1 | Все step1 envelopes: own versions/run/config_hash/registry/start/finish/evidence refs; UTC + display timezone | contracts → Tcontracts:envelope | S/D |
| R13.02 / 13.1 | Atomic outputs только после обязательных evidence; run.json commit last | storage → Tstorage:commit_order | S/D |
| R13.03 / 13.2 | Page schema полная, текст всегда external ref (единый режим), нет случайного inline | contracts → Tcontracts:page_external_text | S/D |
| R13.04 / 13.2 | crawl включает structure/directives/roles/sitemap/language/geography/coverage/material | contracts → Tcontracts:crawl_shape | S/D |
| R13.05 / 13.3 | Check: id/kind/URL/token/status/observation/evidence/lines/reason/limitation/observed_at/rule_version/scope | contracts → Tcontracts:check_shape | S/D |
| R13.06 / 13.3 | Уникальные page/check/evidence IDs, no foreign-run refs, hashes связаны с normalization version | contracts → Tcontracts:references_unique | S/D |
| R13.07 / 13.3 | Coverage только recompute, supplied inconsistent counters отвергать | coverage → Tcoverage:reject_forged_counts | S/D |
| R13.08 / 13.3 | fail=наблюдённое препятствие, warning=ограничение, unknown=не установлено | checks → Tchecks:status_semantics | S/D |

## 14. Оркестрация, хранение, следующий вход

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R14.01 / 14.1.1–2 | Verify source → store preflight → robots; до этого ноль crawl GET | runner → Trunner:phase_order | S/D |
| R14.02 / 14.1.3–5 | Discover/select/fetch/persist/extract/add-links в bounded deterministic порядке | runner → Trunner:bounded_loop | S/D |
| R14.03 / 14.1.6–7 | Static directives/TLS reuse до optional diagnostics в остатке | runner → Trunner:diagnostics_after_static | S/D |
| R14.04 / 14.1.8–9 | Summarize/recompute/validate/persist понятный terminal result | runner → Trunner:final_consistency | S/D |
| R14.05 / 14.2 | При unknown/disallowed новые HTML не читать; entry evidence и checks не терять | runner → Trunner:robots_stop_with_entry | S/D |
| R14.06 / 14.3 | Source step0 read-only; отдельная новая step1 publication; ID-based filenames | storage → Tstorage:source_hashes_unchanged | S/D |
| R14.07 / 14.3 | Derived DOM/text typed source_evidence_id; manifest перед crawl/checks/run | storage → Tstorage:derived_refs_resolve | S/D |
| R14.08 / 14.3 | Evidence verification failure не даёт trusted checks; disk error явный, другой run/PDF не перезаписывать | storage → Tstorage:corrupt_and_disk_failure | S/D |
| R14.09 / 14.4 | Step2Input: useful/supporting texts, IDs, coverage limits, user material, language/geo, technical checks | handoff → Thandoff:step2_complete | S/D |
| R14.10 / 14.4 | Loader Step2Input offline, no empty substitutes, проверяет commit/refs/hashes/versions/gate | handoff → Thandoff:load_step2_no_network | S/D |
| R14.11 / 14.4 | Не генерировать профиль/LLM; ограничения и uncertain geography проходят дальше неизменными | handoff → Thandoff:no_profile_or_fact_invention | S/D |

## 15. Приёмка: все строки исходной таблицы

Каждый A — интеграционная фикстура `tests/test_step1_acceptance.py`, тест
`test_<название>`. Статус всех A = D, кроме browser egress A12b = B. Ни одна строка
не считается принятой по наличию документа/мока.

| ID / §15.1 scenario | Атомарные assertions | Связанные требования / planned test |
|---|---|---|
| A01 полезный одностраничник | material=limited; can_start_step2=true; нет требования ещё 2 страниц | R12.04 / one_page |
| A02 три служебные страницы | chars не скрывают business insufficiency; step2=false | R12.02,R12.05 / legal_only |
| A03 robots main allowed/services denied | разные decisions с rule lines; services GET=0 | R03.02,R03.09 / per_url_denial |
| A04a robots 404 | absent; разрешение без выдуманной Allow line | R02.05 / robots_404 |
| A04b robots 503 | bounded retries; unknown; новых HTML нет | R02.08 / robots_503 |
| A05 external robots redirect | original origin binding; final URL отдельно; HTML scope unchanged | R02.11–12 / robots_external |
| A06a несколько групп | parser/explainer совпадают; source line numbering корректен | R03.05 / merged_groups |
| A06b пересекающиеся правила | precedence/wildcard/encoding cases; mismatch→unknown | R03.04,R03.06–07 / overlapping_rules |
| A07a sitemap absent | links продолжаются | R05.11 / sitemap_missing |
| A07b sitemap malformed | ошибка доказана; links продолжаются; no XML network | R05.05,R05.11 / sitemap_malformed |
| A07c sitemap cycle | no refetch, ≤5 files/depth3/5000 loc | R05.07 / sitemap_cycle |
| A08 query trap | ≤5000 stored, ≤2 variants/path, bounded skip accounting | R06.06 / parameter_trap |
| A09 repeated X-Robots-Tag | все field lines и адресаты сохранены | R08.01 / repeated_headers |
| A10a service noindex | indexing fail в observed scope | R08.05 / service_noindex |
| A10b другая страница не прочитана | indexing unknown, не pass | R08.07 / missing_page_directives |
| A11 Google-Extended | policy есть, ноль probe, not_applicable | R04.05 / google_extended |
| A12a JS добавляет текст | оба representations/transform refs; observed_gain на границах | R09.10–12 / rendered_gain (J) |
| A12b реальная browser сеть | private/rebinding/subresources блокируются до запрещённого соединения | R09.08 / browser_egress (J, B) |
| A13a browser package/Chromium absent | enabled unknown; static работает | R09.03 / missing_browser |
| A13b нет egress | browser не запускается, JS unknown; disabled check отдельный | R09.14 / unavailable_egress |
| A14 probe403/base200 | конкретные UA/time/environment observations, нет вывода о реальном bot | R10.04 / ua_difference (U) |
| A15 user description | user_provided без page_id; technical fail unchanged | R12.07,R12.09 / user_material |
| A16a deadline | completed evidence сохранены, pending terminalized, streams closed | R11.07–08 / deadline_partial |
| A16b Retry-After вне deadline | RETRY_DEFERRED, ноль следующего GET | R11.06 / retry_deferred |

| ID / оставшиеся пункты §15 | Отдельный критерий готовности | Владелец → тест / статус |
|---|---|---|
| R15.01 / 15.2 | Attempt counters = transport-start journal; retry не page | budget → Tbudget:journal_transport_reconciliation / D |
| R15.02 / 15.2 | Сумма terminal selected states объясняет coverage, queued не скрыты | coverage → Tcoverage:terminal_partition / D |
| R15.03 / 15.2 | Все evidence hashes/refs разрешаются; disabled ≠ unknown | contracts/storage → Tstorage:full_reload / D |
| R15.04 / 15.2 | Core tests offline; real TCP/TLS только controlled endpoints | acceptance → A01–A16 + local transport suite / D |
| R15.05 / 15.2 | Browser DOM и egress integration реально выполнены либо явно not run, не accepted по unit mocks | browser → A12a–b / B |
| R15.06 / 15.3 | JSON согласованы, пределы соблюдены, каждая страница/check объяснены | acceptance → Trunner:final_consistency / D |

## 16. Источники и изменения методики

| ID / источник | Атомарное решение | Владелец → проверка | Режим / статус |
|---|---|---|---|
| R16.01 / 16.1 | PDF/source и рабочий алгоритм не перезаписывать | docs → `git diff -- docs/source docs/steps` пуст / V для этого задания |
| R16.02 / 16.2 | API/protocol/vendor источники проверять заново, фиксировать URL/date/version | registry/docs → source register архитектуры; Tregistry:required_fields / D для runtime |
| R16.03 / 16.3 | Selection/normalization/material/directive/browser policy меняют algorithm version/config hash | contracts → Tcontracts:version_fingerprints / D |
| R16.04 / 16.3 | Исходные измерения не мигрировать выдуманными данными, registry snapshot сохранять | storage → Tstorage:no_silent_migration / D |
| R16.05 / 16 + задание | Вопросы Q01–Q06 отделены от принятых инженерных решений | docs → review architecture questions / выполнено документом |

## Граница готовности текущего поручения

### Фактический прогресс цепочки 1–13

| Промпт | Факт / gate | Статус |
|---|---|---|
| 1 | 170 уникальных R, §§1–16/8.5, A01–A16, L01–L11, локальные ссылки, unchanged sources, 32 старых artifacts; commit 70eba84, push main | completed |
| 2 | `step1/config.py`, `models.py`, `schemas.py`, 19 synthetic artifacts и `tests/test_step1_contracts.py`; новые версии раздельны, refs/coverage/material/quotes/UTC/extra проверены; повторная проверка исправила cross-entity collisions/derivation/aliases | completed для моделей; disk/точный text slice и runner ещё deferred |
| 3–13 | Сеть/robots/обход/публикация/Step2 loader/security/performance/final acceptance ещё не реализованы | deferred |

Проверки промпта 2 после исправлений: полный `pytest -q -ra` — 384 passed / 3 skipped,
`pytest tests/test_step1_contracts.py` — 33 passed; strict mypy win32/linux — pass;
Ruff src/tests/tools, Bandit src/tools, uv lock --check, uv pip check, оба generators
--check и git diff --check — pass. Skips: Windows symlink privilege 1314, POSIX FIFO
и directory FD; это не новая приёмка Linux/CI. До моделей полный baseline был
351 passed / 3 skipped. Зависимости не менялись, pyproject/uv.lock неизменны.

Конкретные model gates: `test_roundtrip_schema_and_distinct_versions`,
`test_invalid_examples_are_rejected`, `test_evidence_and_identity_invariants`,
`test_nullable_extra_finite_utc_and_terminal_gates`,
`test_multivalue_headers_do_not_widen_step0_allowlist`,
`test_quote_offsets_and_user_provenance_have_no_fake_page`,
`test_step2_corpus_gate_and_synthetic_not_live_handoff`,
`test_graph_invariants_before_any_files_are_opened`, `test_generated_step1_artifacts_match`.
Все в `tests/test_step1_contracts.py`; parametrization проверяет отдельные мутации.
JSON Schema не выражает все graph invariants: Python validator обязателен дополнительно.
Синтетические invalid примеры хранят компактные mutations к result.json; пример
Step2Input не имеет действующего commit и не является готовым audit handoff.

Строки R с полной future pipeline проверкой пока остаются D: наличие модели не
реализует сетевое/extraction поведение. Подтверждена контрактная часть R06.01,
R07.10–11, R10.08, R13.01/03–07, R14.09 и совместимость R16.03; тесты модели не
заменяют будущие integration tests с указанными T-именами.

Документальная спецификация готова при наличии полной трассировки разделов выше,
таблицы L, архитектуры владельцев/API/ошибок/compatibility и явных Q. Это не готовность
обхода. Следующая реализация начинается с моделей и offline fixtures; новые строки
D/B переводятся в implemented+verified только после конкретных фактически выполненных
тестов. Browser/UA выключены по умолчанию; статическое ядро обязано корректно
публиковать их disabled/unavailable состояния. Новая сеть аудита этим заданием не разрешена.
