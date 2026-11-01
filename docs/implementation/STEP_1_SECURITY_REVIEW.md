# Step1: повторное ревью безопасности, 01.11.2026

Статус: проверены реализованные модели/runtime/robots/discovery; **весь step1 не
принят**. Это отдельный проход самопроверки по коду и diff, не независимая экспертиза.
База diff — 9f231de; исходная цепочка — 157edf5. Отсутствующие crawler/extraction/
diagnostics/Step2 loader нельзя признать безопасными по отсутствию кода.

## Подтверждённые findings

| ID/severity | Файл/точка | Воспроизведение и эффект | Исправление/проверка |
|---|---|---|---|
| S1/P2, исправлено | step1/models.py:254, SitemapEntry.admission | accepted+url=null или skipped+url=URL принимались. Противоречивый discovery DTO мог вводить consumer в заблуждение | test_sitemap_entry_rejects_contradictory_state: обе мутации сначала DID NOT RAISE, затем pass; согласование state/url; соседние valid/parser cases проходят |
| S2/P2, исправлено | step1/discovery.py:275, DiscoveryService._drain | Если loc0 невалиден, loc1 получает occurrence0 при enumerate только accepted URLs. Неверная ссылка на source occurrence | Привязка к SitemapEntry.ordinal, test_namespace_loc_publisher_metadata_and_exact_ordinals. Replay предшествующей логики в отдельном Python-процессе дал assert0!=1; исправленный код проходит. Worktree при replay не менялся |
| C1/блокер приёмки, открыт | step1/runner.py:411, EXTRACTION_NOT_IMPLEMENTED | Любой успешно проверенный вход остаётся partial, текста для Step2 нет; §§6–14 не реализованы | Не выдавать такой итог за completed step1. Матрица STEP_1_VALIDATION фиксирует каждый незакрытый пункт |
| S3/P1 совместимости, исправлено до публикации | step1/config.py:Step1Config.fingerprint; sitemap.py:MAX_XML_NODES | Добавление max_xml_nodes default без смены config_version меняло fingerprint старых валидных config и ломало чтение публикаций | Golden hash из9f231de сначала не совпал (4e9743… против e55f76…), затем pass. Work cap вынесен в parser constant100000; старый config/schema/hash сохранены. test_legacy_step1_config_fingerprint_remains_readable + contracts roundtrip |

S1/S2/S3 относятся к добавляемому этим изменением расширению sitemap, не к выпущенному
step0. Известных воспроизведённых SSRF/TLS bypass P0/P1 в просмотренных путях не найдено;
это не подтверждение отсутствия уязвимостей во всём будущем pipeline и не закрытие C1.

## Реальные пути исполнения

| Угроза | Просмотренный путь и подтверждение | Предел вывода |
|---|---|---|
| URL/DNS/rebinding/peer/SNI/TLS | discovery._read → runtime.fetch → SafeHttpClient.get/_fetch_once → check_endpoint → PinnedBackend/PinnedStream/PinnedTransport. Numeric connect, peer сверяется до HTTP, исходные Host/SNI, CERT_REQUIRED. Реальный TLS CDN-тест и существующий connection suite | Только общий static client; браузерного backend нет |
| Redirect/robots | Scope до authorizer; authorizer для каждого нового target; REP отдельный scheme/host/port. Service robots не рекурсивен; CDN maps требуют explicit service hosts и собственного REP. test_map_redirect_checks_cdn_robots_before_destination, реальные robots integration | Generic runtime.fetch — доверенный seam, требующий policy; публичный fetch_html всегда own REP. Новый production caller обязан использовать этот путь |
| Методы/обход барьеров | PinnedTransport принимает GET; никакого POST/form submit/UA bypass для sitemap. Честный GEOAudit/0.1, no env/proxy/cookies/keepalive/HTTP2. Challenge/truncated REP unknown | Crawler exclusions и optional probes ещё отсутствуют |
| XML/XXE/expansion | defusedxml0.7.1 iterparse, явно forbid_dtd/entities/external. test_hostile_xml_never_opens_network запрещает socket/DNS, включая file:// entity и DTD. Нет XML fetch API | Нет утверждения о schema validation полного протокола; namespace-free compatibility помечается |
| Gzip/CPU/RAM | Существующий LimitedBody: max_length+1, bounded buffers, eof/unused_data. HTTP и file gzip раздельны;5×5 MiB на слой; общий evidence cap. XML depth64/nodes100000/loc5000; HTML href5000, bounded problems | Cooperative time checks, не гарантированное прерывание синхронного parser посередине одного токена; полный crawler ещё не ограничивает 2 query variants |
| Budget/reset/Retry-After | Тот же Budget; reserve после успешной DNS-проверки перед transport. Redirect/retry charged; DNS rejection не HTTP. Robots/sitemap делят pacing. Повтор discovery cache, interrupted ledger нельзя restart. Retry-After999 не вызывает fallback | Истёкший handoff не обновляется. Полный run-status/material ещё skeleton |
| Cancel/disk | Observer сохраняет завершённые попытки, runtime shields cleanup; disk failure не обещает JSON; исходная ошибка сохраняется. Runtime unit/real TLS cancel и ENOSPC/EACCES regression suite | Нельзя обещать публикацию при неработающем диске |
| Traversal/symlink/hardlink/races | Все writes/reads через EvidenceStore/secure_io; Windows handles без reparse/delete sharing, POSIX dirfd/O_NOFOLLOW, fstat regular+nlink1. run.json commit last, новая UUID namespace | Локальный Windows symlink test skipped (1314), POSIX tests здесь skipped; cross-platform CI для нового HEAD ещё не подтверждён |
| Evidence/provenance | Commit hashes, ID graph, source read-only/import, per-file64 MiB/run256 MiB, JSON16 MiB; loader checks actual bytes. Namespace/cycle/gzip fixtures reload publication | Hash — integrity, не подпись против атакующего, способного заменить весь каталог. Offline Step2 exact text slices ещё не реализованы |
| Secrets/log injection | Resource headers allowlist, Set-Cookie не сохраняется; network log redactor скрывает URL/headers/exceptions. JSON encoding не интерполирует сайт в команды. Inventory pattern scan без вывода содержимого | Raw evidence может содержать чувствительные данные сайта/URL; это локальные приватные данные, не публичный отчёт. Heuristic secret scan не доказывает отсутствие любых секретов |
| Обход клиента библиотеками | sitemap.py импортирует только bounded decoder/defusedxml/normalizer. Парсер получает bytes, нет requests/httpx/urlopen/fetch_url. Discovery использует только runtime.fetch | Trafilatura/BS/browser пока не установлены/не реализованы. Их будущий сетевой путь надо проверять заново |

## Проверки и версии

Python3.12.10, HTTPX0.28.1/httpcore1.0.9, Pydantic2.12.3, Protego0.7.0,
defusedxml0.7.1; Ruff0.14.3, mypy1.18.2 strict, Bandit1.9.4, pip-audit2.10.1,
uv0.9.7. Runtime/dev pins вместе с uv.lock; source register — STEP_1_ARCHITECTURE.

Выполнены `.venv/Scripts/python.exe -m ruff check src tests tools`,
`-m mypy --platform win32`, `--platform linux`, `-m bandit -r src tools -f json
-o tmp/bandit-step1.json`, `uv export --locked --no-emit-project --format
requirements-txt --output-file tmp/audit-step1-requirements.txt --quiet`,
`-m pip_audit --require-hashes -r tmp/audit-step1-requirements.txt
--cache-dir tmp/audit-step1-cache --progress-spinner off`.
Все успешны; advisory DB доступна, No known vulnerabilities found; suppressions
и TLS bypass не добавлялись. Сырые логи только ignored tmp. Финальный tracked-only pytest:500 passed,
3 skips; все последние sitemap/config regressions включены. Mock-only XML vectors дополнены
реальным TLS CDN/evidence/forbidden-GET сценарием; это не browser integration.

Настоящий browser DOM/private-egress/rebinding integration не запускался.
Безопасного egress adapter в проекте нет; даже обязательный enabled-unavailable gate
пока не реализован. Успешная browser ветвь остаётся deferred/blocked и должна быть
принята controlled JS + реальными blocked private destinations/redirects/iframes/XHR,
а не единственным route mock. Новая proxy/container платформа не создавалась.
