# Шаг 1: архитектура и совместимость

Дата: 01.11.2026. Проектирование по HEAD `157edf5`, geo-audit 0.3.2.
Это план реализации, не описание существующего обхода. Обязательства и тестовые
ID заданы в [матрице](STEP_1_REQUIREMENTS.md); `T…` ниже — будущие проверки.

### Реализованный каркас, 01.11.2026

Промпт 3 реализован в `step1/runner.py` и `step1/storage.py`: `Step1Runtime` принимает
verified handoff; `from_directory` использует прежний loader без обновления deadline.
Контекст сохраняет честный `partial/SKELETON_ONLY`: автоматические robots/очередь и
extraction ещё не подключены. Реальные тесты и границы приёмки перечислены в матрице.

Уточнение минимального плана API: отдельный клиент/Step1FetchTrace не создавались.
Общий runtime-only FetchTrace допускает метку step1-1.0, а default остаётся step0-1.2;
persisted step0 Literal/version gates неизменны. HttpAttempt сохраняет прежние
response_headers и дополнительно resource_purpose/resource_headers/authorization
для нового режима. Последние содержат все разрешённые field lines, включая повторные
X-Robots-Tag; старый Header не расширен. RequestPolicy — keyword-only параметр get.
Для step1 запрещён автоматический HTTP fallback; авторизация вне transport lock,
перед каждой попыткой/redirect; TLS/pin/peer/ports/caps не отключаются профилем.
Общий host pacing усиливается per-origin Crawl-delay из authorization; новый процесс
консервативно восстанавливает host timestamps по завершению наблюдений step0.

Выбран один layout: новая `step1-<uuid>` публикация вне source. Импортируются все
verified source evidence (для сохранения TLS/history), с новыми локальными ID/path
и original_evidence_id/source_run_id; source никогда не открывается на запись.
site.json — byte-exact snapshot. Source bytes учитываются в том же логическом cap
256 MiB, imported copies не выдают новый запас; physical publication тоже ≤256 MiB.
Metadata ≤16 MiB/file, evidence ≤64 MiB/file; новые данные резервируются до записи.
run.json — PublicationCommit (расширение RunResult с commit_version и фиксированным
набором artifact hashes), записываемый последним. Sealed publication запрещает новые
записи/повторный commit, interrupted generation не переиспользуется. Offline inspector
проверяет все ссылки/хэши; он не является ещё готовым consumer Step2Input.

Ручная сборка detached Step1Handoff не разрешает свежий Budget с прежними счетчиками:
проверяются исходные timestamps, counters и elapsed с учётом UTC idle; live режим
также требует identity Budget/FetchResult. Assessment и bytes совпадают с source.
Synchronous bounded_phase ограничивает bytes и проверяет время до/после; parser
обязан отдельно ограничить nodes/depth/work. Прерывание CPU посреди вызова не обещается.
При отмене raw завершённых наблюдений сохраняется, pending URL и budget пишутся в
partial commit; CancelledError распространяется. Ошибка диска явная; JSON не обещается
при неработающем диске. Исходные программные исключения не подменяются cleanup ошибкой.

Повторно проверены 01.11.2026: RFC 9309 §2.3.1.2
(https://www.rfc-editor.org/rfc/rfc9309.html), HTTPX 0.28.1 raw headers/streaming
(https://raw.githubusercontent.com/encode/httpx/0.28.1/httpx/_models.py,
https://www.python-httpx.org/async/), Python 3.12 asyncio timeout
(https://docs.python.org/3.12/library/asyncio-task.html#timeouts).
Pins/uv.lock не менялись; public audit-сеть не запускалась.

## Принятые инженерные решения

Не меняем исходный алгоритм/PDF. Последовательный async — основной исполнитель:
верхняя граница двух соединений не требует параллельности. Не добавляем task pool,
второй HTTP-клиент, новый независимо отсчитываемый Budget или новую инфраструктуру
ради включённых по умолчанию выключенных диагностик. Scope шага 0 — исходное
доказанное множество; служебные разрешения хранятся отдельно.

Новые типы и функции сначала реализуются офлайн. Библиотечный вход будущего runner:

```python
async def read_site(
    handoff: Step1Handoff,
    config: Step1Config,
    *,
    source_store: EvidenceStore,
    output_directory: Path,
    client: SafeHttpClient,
    timing: TimeServices,
    user_description: str | None = None,
) -> Step1Result: ...

def load_step2_input(directory: Path) -> Step2Input: ...
```

`source_store` сверяется с source run, не принимается как произвольная подмена.
Config/client/clock/budget должны быть согласованы. Никаких DNS/GET/input() в loader,
моделях, import и material evaluation. Step2Input передаёт данные, профиль не строит.

## Минимальные модули и сущности

Новый пакет `src/geo_audit/step1/`; не нужен файл/класс на каждую строку матрицы.
Один владелец каждой сущности указан ниже. Используются существующие Contract,
UTC/SHA/URL primitives, secure_io, LimitedBody, TimeServices и проверенные сетевые
границы. Новые persistable сущности — строгие frozen Pydantic модели с запретом extra,
required nullable там, где неизвестность важна; enum/reason поля проверяемы схемами.

| Владелец; сущность | Вход → выход | Инварианты | Исключения/недоступность | Точки тестирования |
|---|---|---|---|---|
| `config.py`; Step1Config | source Step0Config + настройки чтения → versioned config/hash | source hash неизменен; total caps не повышаются; browser/UA=false; exact service hosts | ValidationError до I/O | Tcontracts:config_versions, Tbudget:all_limit_boundaries |
| `models.py`; Step1Envelope, Step1Result, CrawlResult, ChecksResult, RunResult | validated observations → crawl/checks/run | own schema/algorithm/config versions, run_id source, publication_id отдельный, UTC, config_hash/registry_version | ValidationError, unknown versions отвергать | Tcontracts:envelope, references_unique |
| `models.py`; Step1Header, Step1Attempt, Step1Redirect | transport observations → immutable history | header sequence, resource purpose отдельно от attempt cause; source/parent/chain IDs; checked target на sent | typed transport/policy errors; программные исключения propagate | Tnetwork:default_step0_unchanged, repeated_headers |
| `robots.py`; RobotsObservation, RobotsDecision, origin cache | origin + FetchTrace/body/token/URL → REP state/decision/lines/delay | key scheme/host/effective port; правила относятся к original origin; unknown не Disallow | fetch states из R02; parser/explainer disagreement→unknown | Trobots:origin_key_and_path_exemption, explanation_mismatch |
| `registry.py`; RobotEntry, RobotRegistry | embedded versioned data → eight token records/snapshot/hash | documented source/date/purpose, UA nullable, нет выдуманных UA | needs_verification → ограничение interpretation/probe | Tregistry:initial_tokens, no_fabricated_ua |
| `sitemap.py`; SitemapObservation | service URL + bounded bytes → loc records/metadata/errors | namespaces, no entities/DTD/network; visited; depth/files/entries caps | XML/encoding/gzip errors → observation, links продолжаются | Tsitemap:xxe_bombs_depth, limits_cycle |
| `discovery.py`; Candidate, discovery sources, aliases | entry links/sitemap/links → bounded ordered queue | deterministic admission/selection; exact URL key, no arbitrary scope expansion | exclusions/parameter/scope limits → skip records; malformed URL не crash | Tdiscovery:deterministic_replay, parameter_trap |
| `extraction.py`; Page, TextRepresentation, QuoteRef, language/geo signals | saved HTML/DOM → main text/structure/metadata/quality | raw неизменен; source ID/hash/version; offsets в normalized text; no double counting | extractor None/parse failure→fallback/unknown с reason; defect не network error | Textraction:fallback, normalization_offsets |
| `directives.py`; DirectiveObservation | header field lines + HTML → raw/addressed normalized directives | all occurrences; static/rendered отдельно; operator scope | conflict/неподдерживаемая semantics → unknown, не выбираем удобную строку | Tdirectives:repeated_headers, legacy_header_coverage |
| `checks.py`; TechnicalCheck | robots/directives/TLS/diagnostics → scoped findings | status pass/fail/warning/unknown/not_applicable; time/rule_version/evidence | нет страницы/headers → unknown, disabled → CONFIG_DISABLED | Tchecks:missing_not_pass, status_semantics |
| `material.py`; Coverage, MaterialAssessment, UserMaterial | terminal candidates/pages/user text → counts/state/gate | recompute; site/user provenance раздельно; useful не только chars | insufficient/needs_description → next_action request_description | Tcoverage:terminal_partition, Tmaterial:user_does_not_fix_checks |
| `diagnostics.py`; DiagnosticResult, bounded selection | corpus + remaining quotas → HTTPS/UA/JS observations | no recursive crawl; comparison conditions; disabled != unavailable | enabled unavailable → unknown; deadline inconclusive; no supported UA→N/A | Tdiagnostics:ua_default_and_caps, js_outcomes |
| Будущий `browser.py`; BrowserCapability/adapter | eligible page + approved egress → DOM and network journal | реальный peer контроль всех соединений; TLS/методы/bytes/time; общий deadline | нет доказанной capability → не launch, unknown; backend сейчас blocked | Tbrowser:actual_peer_rebinding + controlled DOM integration |
| `storage.py`; Step1Store, Step1Evidence, SourceRef, commit descriptor | source read-only + new/derived observations → verified publication | bounded writes, unique IDs, no-follow, hashes, manifest before outputs, run last | OUTPUT_WRITE_FAILED, corruption → failed/no trusted commit | Tstorage:source_hashes_unchanged, commit_order, full_reload |
| `runner.py`; RunCounters, phase coordinator | verified handoff → terminal Step1Result | единственный общий Budget, квоты не reset, no await user; all selected terminalized | cancel сохраняет completed; arbitrary defects rethrow; disk fail explicit | Trunner:phase_order, cancel_preserves_completed |
| `handoff.py`; Step2Input + loader | verified committed publication → useful/supporting/user material + checks/coverage/signals | no network; same run refs; can_start_step2 derivation; exact text/hash offsets | invalid commit/evidence/version/gate→INPUT_ARTIFACT_INVALID | Thandoff:load_step2_no_network, quote_offsets |

Pure модели могут оставаться в `models.py`; owner строки — ответственность поведения,
а не требование циклически импортировать models из всех модулей. `__init__.py`
экспортирует только стабильные API после их реализации. Ошибки step1 отдельным
Literal/моделью; старый ErrorCode не расширять произвольно ради новых терминов.

## Совместимость версий и заголовков

Сейчас SchemaVersion/AlgorithmVersion/ConfigVersion в `contracts.py` содержат только
step0 1.0/1.1/1.2. Проверка SiteValidationResult увязывает schema, config, budget,
attempts, evidence, chronology; Step1Input требует schema==site.schema и
crawl_permission=Literal['unknown']. `FetchTrace.algorithm_version` также step0-1.2.
Поэтому менять глобальные CURRENT_* на step1 или подставлять step1 version в старые
модели нельзя. Старые JSON, URN и генератор должны остаться читаемыми и воспроизводимыми.

| Слой | Выбранная версия будущей реализации | Совместимость |
|---|---|---|
| Старый step0/source handoff | как сейчас 1.0.0/1.1.0/1.2.0, текущая запись 1.2.0 | Старые gates без расширения на step1; historical чтение сохраняется |
| Новые output/schema | `step1-1.0.0`, URN `urn:geo-audit:Step1Result:1.0.0` и аналогичные | Новый отдельный Step1SchemaVersion, не SchemaVersion step0 |
| Методика | `step1-1.0` + explicitly versioned heuristic policies | Отдельно от package version и registry |
| Config | `step1-config-1.0.0` | Полный source_step0_config_sha256 + настройки шага; config_hash на canonical JSON |
| Registry | `robots-2026-11-01.1` | Snapshot + SHA, verified_at/source per entry; stale не обновляется в старом run |
| Normalization | `text-ws-1` | Hash/offsets связаны с этой версией |
| Future handoff | `step2-input-1.0.0`, source_step=1 | Только input, не реализация шага 2 |

Step1Config **не наследуется** от Step0Config с заменой Literal: композиция с exact
source fingerprint. Budget остаётся привязан к исходному Step0Config; клиент получает
этот же config, resource policy задаёт лишь purpose-specific ограничения. Старый
`schemas.MODELS` и generated schema IDs не переименовывать. Добавить отдельный
step1 registry/generator; tests проверяют unchanged старые 32 artifacts и новые схемы.

Header уже tuple, но allowlist step0 исключает X-Robots-Tag. Решение: Step1Header
с теми же полями и добавленным `x-robots-tag`; shared transport берёт `.headers.raw`,
сохраняет field-line order и все повторения, применяет allowlist профиля. Default
step0 проекция строго прежняя. `dict(headers)`, `.get()` и comma-splitting не являются
представлением persisted headers. Для step1 raw trace нужна отдельная модель/typed
projection; не выдавать Step1FetchTrace с algorithm_version=step0-1.2.
HTTPX 0.28.1 `get_list(name, split_commas=False)` допустим для просмотра одного имени,
но исходные field lines сохраняются через raw. См. S02/S03 ниже.

Унаследованный entry step0 **не содержит наблюдения X-Robots-Tag**. Сохранённые
headers нельзя объявить полным набором HTTP directives. Для entry фиксировать
`header_capture_complete=false` и missing `x-robots-tag`; meta можно анализировать,
положительный noindex по meta остаётся fail, отсутствие meta не даёт полного pass.
Не делать refetch только ради дополнения. При будущем step0 producer нового формата
потребуется отдельная обратно совместимая версия и regression fixtures, не тихая
смена 1.2.0. Set-Cookie/authorization и секреты в журналы не добавляются.

## Один бюджет и счётчики

`handoff.budget` — единственный authoritative Budget. В одном процессе проверяется
identity; `prepare_step1` запрещает подмену. В другом процессе только существующий
`load_step1_input`/Budget.restore: elapsed=max(saved elapsed,UTC now−started), затем
прибавляется новый monotonic elapsed. UTC < captured_at, expired или exhausted total
отвергаются; чтение файлов/простой не возвращают время. Snapshot step0 не переписывать.

Все step1 GET вызываются с `step=1`: 60 s/15 attempts step0 больше не ограничивают их,
но total 300 s/150 attempts и spent step0 сохраняются. Конфигурация не получает новый
start time. `RunCounters` держит только квоты L03/L08/L09/L10/L11, cache hits и observed
states; не имеет `remaining_seconds`/своего HTTP лимита. Reserve происходит до начала
попытки транспорта; precheck DNS rejection — observation без charge, connect failure
после reserve — charge. Counters, request_sent и transport journal сверяются отдельно:
существующее request_sent означает зарезервированную transport attempt, не гарантию
отправки HTTP bytes на wire. Не переименовывать его семантику старым данным.

Browser attempts считаются отдельно от 150 non-browser, но время и host pacing общие;
200 — не дополнительное время и не HTTP fallback бюджет. Пока нет backend, browser
не стартует. После static browser работает последовательно и его egress исполнитель
должен доказать общий предел active connections и host interval, включая ресурсы.
Все точные лимиты/владельцы перечислены L01–L11 матрицы.

Перед/после сетевых awaits, chunks, observers и CPU parsing phases проверять остаток.
`asyncio.timeout` не прерывает синхронный CPU разбор; bounded bytes/tree/depth/entries
ограничивают работу, проверки до/после не позволяют принять поздний результат как
выполненный вовремя. Если понадобится жёсткая CPU изоляция, это отдельный измеренный
этап, а не обещание от timeout. Завершение записи completed evidence может превысить
deadline ради целостности; новый GET и доверенный timed-success после срока запрещены.

## Сетевая граница: purpose/policy вместо второго клиента

Существующий `get(url, scope, budget, step=0, allow_http_fallback=False, observer=None)`
сохранить совместимым. Добавить keyword-only `policy: RequestPolicy | None = None`.
`None` означает точное старое поведение; отдельный обязательный профиль нужен для
step1. Resource purpose (`html`, `robots`, `sitemap`, `https_diagnostic`, `ua_probe`)
не заменяет attempt cause (`initial`, `retry`, `redirect`, `http_fallback`).

RequestPolicy — immutable доверенный объект, не произвольные headers/SSRF-bypass flags:

| Поле/владелец | Правило |
|---|---|
| resource_purpose | Принимается только известный профиль; записывается в Step1Attempt |
| allowed service hosts | Только robots redirect безопасное публичное назначение либо explicit sitemap allowlist; HTML scope объект не мутирует |
| encoded/decoded caps | Профиль может ужесточить исходные caps; robots 512 KiB; gzip sitemap отдельно bounded |
| user_agent override | Только UA diagnostic, из verified registry, безопасный ASCII; остальные headers/TLS/Host/SNI не подменяются |
| before_request authorization | Точный URL до **каждой** попытки, включая redirect и retry; возвращает typed allowed/denied/unknown с evidence |
| min interval | max(config min interval, own-token Crawl-delay origin), общий host pacing map |
| header projection | Step0 default прежний; step1 сохраняет multivalue X-Robots-Tag |

Профиль **не может** выключить TLS/public-IP pin/peer/SSRF, trust_env/cookie/proxy
изоляцию, budget checks или поднять caps. Исключение service path — явная проверенная
ветвь, не `scope=None` и не `allowed_hosts=*`.

Почему не отдельный клиент: новый экземпляр обнулит `_last_started`, усложнит
разделение cookies/env/TLS, продублирует retries и может уйти на непроверенный
transport. Один SafeHttpClient и общий Budget позволяют сохранить уже проверенные
IP/peer/Host/SNI, delays, limits и observer cleanup. Риск policy — случайное расширение
scope; поэтому purpose-specific typed policy, default compatibility и отрицательные
тесты обязательны. Отдельный браузер не маскируется этим API: ему нужен egress backend.

### Redirect gate без deadlock

Сейчас `_lock` держится на **всю** цепочку `get`. Callback, который внутри lock
загружает robots через тот же client, зависнет. При реализации переносить lock на
одну transport attempt: authorization/получение robots выполняются **вне** lock;
затем внутри lock вновь budget/pacing/endpoint pin/reserve/send/close. Body/observer
завершаются и lock освобождается до разрешения следующего target. Все старые
неявные transport retries остаются выключены. Сначала regression step0, потом step1.

Порядок для HTML (также UA и последующих redirects HTTPS diagnostic):

1. Resolve Location/normalize; проверить hops, loop, frozen HTML scope/ports/path.
2. Если target host не подтверждён — pending_external_urls, без target GET; следующая ветвь.
3. Для нового origin разрешённого host получить robots через **robots policy**.
4. Оценить own token для точного target URL; denied/unknown → skipped_robots/policy_unknown.
5. Budget/pacing, fresh DNS public-only numeric pin/peer/TLS, reserve/send, сохранить observation.
6. Redirect — снова с пункта 1, даже тот же origin, другой path; retry — повторный gate.

Robots policy не вызывает robots для robots (нет рекурсии). Его 5 service redirects
проверяются по публичности/портам/схеме/loop/budget и source origin binding, **без**
HTML path_scope. CDN target robots не добавляется в HTML allowed hosts; PSL www alias
из service chain тоже ничего не разрешает. Sitemap service — только explicit hosts,
без HTML scope expansion; discovered loc вновь проходит обычный HTML gate.
Новые www aliases HTML не разрешать автоматически сверх уже доказанных step0 hosts:
R06.11 оставляет их pending. Это сохраняет frozen область текущего handoff.

При переносе lock `last_trace` остаётся диагностическим последним результатом, не
источником истины runner: runner использует локальный возвращённый trace. Вложенный
robots GET не должен менять parent attempt ID HTML. Concurrency API не обещается;
контролируемые фазы последовательны. Новые tests проверяют отсутствие reentrant lock
deadlock и общее pacing между разными purposes.

### Обязательные regression gates при сетевом изменении

Не ослаблять ни один из существующих suites: `test_http_client.py`,
`test_connection_integration.py`, `test_url_policy.py`, `test_acceptance.py`,
`test_step0_end_to_end.py`. В частности:

- `test_redirect_get_count_host_sni_and_cookie_env_isolation`,
  `test_no_get_to_forbidden_redirect`, mixed DNS, changing redirect DNS, wrong peer,
  wrong hostname/untrusted CA, real IPv4/IPv6 и handshake timeout.
- `test_fifteen_attempts_include_retries`, Retry-After seconds/date, slow drip,
  observer deadline, explicit cancel, stream close, no TLS fallback after HTTP status.
- Encoded/decoded caps, gzip exact boundary/multiple members/deflate; no extra GET
  при load; code defects не преобразуются в site unavailable.
- Все старые schemas/examples побайтово прежние при default policy; legacy 1.0,
  mixed/unknown versions, tampering, unsafe paths, FIFO/growing file/no-follow,
  platform races и redacted logs остаются отрицательными.

Новые negative traces: разрешённый HTML → redirect forbidden path (ноль target GET),
redirect new-origin → robots503 (ноль HTML), external robots → public service success
но ноль HTML CDN GET, private service redirect (ноль forbidden connect), step0=15 и
total<150 позволяет step1, total=150 не позволяет никакой purpose, default step0
signature/projection unchanged, nested robots не deadlock, shared host pacing ≥1 s.

## Selection и terminal states

Discovery использует canonical normalization из urls без fragment и без сортировки
query. URL identity = normalized exact string; parameter quota key = origin/path,
значение query exact (пустой query тоже вариант). Sources ordered/deduplicated по
source type + source ID + occurrence index; входные links читаются в DOM order,
sitemap directives/loc в document order. Admission до 5000, отказанные entries
считаются с bounded reasons, не сохраняются неограниченным списком.

Queue key `(role_priority, discovery_ordinal, normalized_url)`; ordinal назначается
один раз при первом admission. Candidate IDs `candidate-00001`, page IDs по selection
ordinal, а UUID evidence остаётся независимым. Повторный source не меняет ordinal;
повышение роли возможно только до selection с recorded basis. Выбор зависит от
наблюдённых входов/версий/config, не от случайного hash order/времени ответа.

| Сущность | Переходы и конечные состояния |
|---|---|
| Candidate | discovered→queued→selected→fetching→`fetched`, `failed`, `skipped_robots`, `skipped_policy_unknown`, `skipped_scope`, `skipped_external`, `skipped_excluded`, `skipped_parameter_limit`, `duplicate`, `not_attempted_budget`, `cancelled` |
| До admission | rejected/skipped records bounded; не входят в selected; dropped counts + examples, overflow явно помечен |
| Page | Только наблюдение; fetch success может иметь extraction failed/empty/supporting/useful; page не равна retry attempt |
| Check | pass/fail/warning/unknown/not_applicable; reason/evidence обязательны, unknown может иметь пустой evidence только с причиной no observation |
| Run phase | preparing→robots→discovery/fetch/extract→static checks→enabled diagnostics→material→persisting→terminal |
| Run terminal | ok / partial / needs_input / failed; cancelled lifecycle отдельно с сохранением partial evidence, API CancelledError propagate |

Selected всегда ≤30 и каждый selected имеет ровно одно terminal state, включая
вход `fetched` из saved response. Retry не создаёт page; redirect final/alias — один
selected slot. Coverage.selected = sum(selected terminal states); fetched counts не
равны unique_html_pages; extracted/useful — подмножества. Unselected queued после
остановки имеют `not_selected_limit`/`not_selected_budget`, не притворяются failed.
Discovered означает сохранённые unique admitted URL; отдельно observed_links и
discarded entries, чтобы bounded очередь не обещала точное число всех URL сайта.

Run status precedence: invalid input/нет достоверного результата/storage failure →
failed (либо исключение до создания output); нужен description/host без usable
material → needs_input; пригодный материал + incomplete branch/budget/enabled check
unavailable → partial; otherwise bounded completed pass с пригодным материалом → ok.
Confirmed noindex/TLS fail сами не делают execution failed. Выключенный режим не
даёт partial. Внешний URL не требует needs_input, если остального материала достаточно;
он остаётся pending. can_start_step2 независимо true только для usable/limited с
business evidence либо user_supported; технические limitations всегда сохраняются.

## Publication и read-only source

Нельзя дописывать step1 evidence в исходный `raw/manifest.json`: loader step0 сравнивает
его с site.evidence и config/budget, а изменение нарушит старый verified handoff.
Нельзя вызывать `EvidenceStore.publish(SiteValidationResult)` для step1.

Выбор: новый output directory с уникальным publication_id, source run_id в envelopes
остаётся прежним. Создание new-only, без silent append/перезаписи. На верхнем уровне
`site.json` — byte-exact snapshot исходного site, `crawl.json`, `checks.json`,
`run.json` и `raw/manifest.json` нового семейства. Это **step1 publication**, не
step0 directory для старого `load_site_result` (тот проверяет имя каталога/run_id).
SourceRef содержит source_run_id, site/config/manifest hashes и evidence refs.

Для переносимости скопировать **только необходимые проверенные source evidence** в
новый raw с новыми локальными внутренними filenames; manifest хранит original ID,
source_run_id и hash, а не абсолютный путь машины. Site snapshot остаётся оригинальным
и его paths не переписываются: Step1 loader разрешает imported refs через SourceRef
mapping, старый loader используется только для исходной папки. Файлы копируются
через confined bounded reads, без hardlinks/symlinks; source никогда не открывается
на запись. Неперенесённые нерелевантные attempts имеют metadata provenance, но не
считаются локально доступным body evidence. Все реально используемые checks/text
должны иметь локальные проверенные bytes, иначе unknown/invalid input.

Новые derived types: extracted_raw, main_text, rendered_dom, user_material,
robots_text/decision, sitemap_observation; не расширять старый EvidenceFile Literal
так, чтобы старый loader начал принимать неизвестные representations. Text всегда
UTF-8 external file; main_text_ref единый режим для всех pages. hash text — bytes
UTF-8; offsets — Python Unicode codepoints `[start,end)`, не HTML byte offsets.
Нормализация `text-ws-1`: CRLF/CR→LF, Unicode whitespace внутри строки→ASCII space,
collapse spaces, trim каждой строки, collapse пустых строк, trim документа; без
case-fold/Unicode NFC, чтобы не менять сам текст незаметно.

Write order: source verify → output preflight → reserve storage/write each completed
raw → manifest/checkpoint → derived bytes → validate all refs/counters/hashes →
final raw manifest → crawl/checks/config/registry snapshots → `run.json` last.
run.json — commit marker с hashes всех итоговых metadata и manifest; self hash не
содержится. Loader не принимает directory без final commit или с mismatched hashes.
Checkpoint не является final success. Сбой посередине оставляет диагностические
файлы без доверенного commit; ошибка диска явно возвращается вызывающему.

Storage caps 64/256/16 MiB сохраняются; 256 MiB применяется к уникальным evidence
файлам логического 0–1 run (source + новые; imported identical bytes не считаются
дважды в логическом ledger). Physical publication bytes тоже bounded 256 MiB;
import copies не дают дополнительный запас. При 30×5 MiB и нескольких representations
лимит может остановить проход раньше 30 страниц — это честный partial, не причина
снять cap. Перед записью файлы резервируются; positive commit с over-cap недопустим.

## Future Step2Input

Модель содержит source_step=1, own version, run/publication IDs и source commit hash;
material_state/can_start_step2; useful и supporting TextRefs; QuoteRefs и role/quality;
UserMaterial с author_type=user/source_type=user_provided/time; Coverage со всеми
limits/stop reasons; language/geography variants с evidence, nullable uncertainty;
TechnicalChecks и registry/config/normalization versions. Нет искусственного page_id
для описания пользователя. Не передаётся новый Budget: шаг 2 не получает права
тратить оставшиеся HTTP/LLM ресурсы по наличию input.

Loader проверяет unique IDs, local files/hash/complete representations, source mapping,
foreign-run rejection, quotes bounds/text equality, recomputed coverage и gate.
Input для unable state можно inspect, но API выдачи ready Step2Input отвергает
can_start_step2=false с next_action, не подменяет пустым профилем. После 300 секунд
истёкшее право продолжать crawl не запрещает читать готовый статический результат
для будущего профиля: load_step2_input не восстанавливает сетевой Budget и ничего
не продлевает. Свежесть бизнеса при будущем использовании — отдельная policy шага 2.

## Первичные источники: проверка 01.11.2026

Просмотр официальной документации — единственная сеть исследования этого этапа;
audit requests/UA probes/browser/платные API не запускались. Даты относятся к проверке,
не к гарантии будущего поведения. Никакие пакеты здесь не установлены и lock не изменён.

| ID | URL и выбранная версия | Проверенное основание / решение |
|---|---|---|
| S01 | https://www.rfc-editor.org/rfc/rfc9309.html — RFC 9309, 2022 | REP groups/path matching, redirects к original authority, parsing/errors; консервативные 401/403 и truncated unknown — product policy, не универсальный RFC вывод |
| S02 | https://www.rfc-editor.org/rfc/rfc9110.html — RFC 9110 §5.2, 2022 | Field lines не терять; семантика отдельного поля определяет допустимость комбинации |
| S03 | https://raw.githubusercontent.com/encode/httpx/0.28.1/httpx/_models.py и https://www.python-httpx.org/api/ — HTTPX 0.28.1 | raw field lines; get_list с split_commas=False. Shared HTTPX/httpcore pins остаются 0.28.1/1.0.9; transport уже реализован локально |
| S04 | https://pypi.org/project/Protego/0.7.0/ и https://github.com/scrapy/protego — Protego 0.7.0 | Protego.parse(text), can_fetch(url,user_agent), crawl_delay(token), sitemaps; fetching/errors/cache не выполняет. Source entry: https://raw.githubusercontent.com/scrapy/protego/0.7.0/src/protego/__init__.py |
| S05 | https://trafilatura.readthedocs.io/en/latest/corefunctions.html и https://pypi.org/project/trafilatura/2.3.1/ — trafilatura 2.3.1 | extract(filecontent, include_tables=True, include_comments=False, output_format='txt') → str/None; только extraction API, fetch/crawler helpers запрещены |
| S06 | https://www.crummy.com/software/BeautifulSoup/bs4/doc/ и https://pypi.org/project/beautifulsoup4/4.15.0/ — beautifulsoup4 4.15.0 | BeautifulSoup(decoded,'html.parser'), decompose/get_text; explicit stdlib parser, не случайный установленный backend. В header docs указан 4.14.3, body — 4.15.0; release pin проверен по PyPI, compatibility tests обязательны |
| S07 | https://pypi.org/project/defusedxml/0.7.1/ и https://raw.githubusercontent.com/tiran/defusedxml/v0.7.1/defusedxml/ElementTree.py — defusedxml 0.7.1 | ElementTree iterparse, forbid_dtd/entities/external=True; clearing завершённых nodes + explicit size/depth/time caps; не cElementTree/global monkeypatch |
| S08 | https://www.sitemaps.org/protocol.html — protocol 0.9 | XML urlset/index namespace/loc/gzip; project 5 MiB/5000 и file/depth caps строже протокола, не выдавать их за стандарт |
| S09 | https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec — checked 01.11.2026 | Google interpretation отдельно от own policy; Crawl-delay не универсален |
| S10 | https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag — checked 01.11.2026 | Addressed/repeated X-Robots-Tag/meta/snippet policies; ограничивать интерпретацию Google |
| S11 | https://developers.openai.com/api/docs/bots — checked 01.11.2026 | Search/training/user fetch roles различаются; ChatGPT-User robots may not apply; UA strings только verified registry |
| S12 | https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler — checked 01.11.2026 | Три роли Claude, robots и Crawl-delay; token не равен автоматически полной HTTP UA строке |
| S13 | https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers — checked 01.11.2026 | Googlebot и Google-Extended; последний не имеет отдельной HTTP UA и не управляет Search inclusion |
| S14 | https://playwright.dev/python/docs/network — rolling docs, checked 01.11.2026 | route не доказательство фактического peer/pinning; Service Workers могут скрывать events. Playwright/Chromium версия пока НЕ выбрана/не установлена: browser backend blocked до egress design/integration |
| S15 | https://docs.python.org/3.12/library/asyncio-task.html — Python 3.12 (проект/CI 3.12.10) | asyncio.timeout/cancellation не заменяют границ синхронного parsing |

Проектные existing pins: AnyIO 4.15.1, Pydantic 2.12.3, certifi 2026.10.23,
idna 3.15, tzdata 2025.2 из pyproject/lock. Не требуется обновление этих API.
Выбранные новые библиотечные pins — проектное решение для следующего задания,
не утверждение об уже разрешённой установке/пройденном dependency audit. При добавлении
проверить Python 3.12 Windows/Linux resolution, lock/hash, license/security и actual API
smoke; невозможность pin или API mismatch — явный gate, не автоматический upgrade.
Доступ к versioned Pydantic docs при этом исследовании не получен; конкретные новые
Pydantic API не предполагаются, используются уже работающие Contract/model validators.

## Вопросы, меняющие методику (не молчаливые решения)

| ID | Неопределённость | Что можно делать сейчас / gate |
|---|---|---|
| Q01 | Как доказать business relevance, soft-404 и predominating service content без модели? | 01.11.2026 пользователь принял консервативные версионируемые эвристики: явные фразы о деятельности с цитатами, спорное unknown. Конкретные правила/fixtures и ограничения фиксировать при реализации; ≥300 само по себе не business |
| Q02 | Что считать пригодным пользовательским описанием кроме непустой строки? | 01.11.2026 пользователь принял: пустое отклонять, непустое без подтверждённого содержания просить уточнить. Сохранять user provenance, не придумывать новый обязательный порог длины |
| Q03 | «Существенное» различие экстракторов и диагностическая ценность не имеют численного правила | Сохранять оба текста, причины и delta; selection/config policy предложить отдельно с границами. Статический fallback при None/empty можно реализовать без решения |
| Q04 | Нужен ли own robots до самого первого альтернативного HTTPS diagnostic URL? | 01.11.2026 пользователь принял robots перед каждым диагностическим GET, включая первый; unknown не разрешает GET. Это может ограничить наблюдение доступности; сохранять причину |
| Q05 | «Достаточная близость времени» для reuse UA baseline не определена | По умолчанию U=false. В U сначала предложить freshness window; безопасная реализация без reuse делает новый baseline в том же bounded comparison batch, учитывая бюджет |
| Q06 | Browser resource byte/per-request caps, platform egress и Chromium build не заданы полностью | Оставить browser выключенным, capability unknown; принять backend и caps отдельно, затем controlled actual-egress tests. Route-only не закрывает обязательный security gate включённого режима |

Эти вопросы не останавливают models, storage, robots, discovery и static extraction.
Они блокируют только утверждения о соответствующих положительных эвристических
решениях/включённых режимах. Не превращать предложение в принятую методику из-за
зелёного теста, который повторяет придуманное правило.

## Порядок разработки и gates

Следовать `docs/PROMPTS_STEP_1.md` 1–13; эта архитектура не заменяет их подробные gates.
После текущей спецификации: contracts/config/Step2Input → shared network policies и
regression step0 → robots/registry → discovery/sitemap → extraction/directives →
bounded runner → optional diagnostics → CLI/handoff → security → performance →
tracked-only/distribution acceptance. Проверки конкретного prompt из цепочки сильнее
этого краткого плана. Ни один обязательный B/D не отмечать готовым по наличию файлов.

Для documentation gate: проверить ID uniqueness, покрытие §§1–16/8.5 и всех 16
сценариев §15.1, L01–L11, локальные ссылки, git diff whitespace, unchanged source,
и существующий генератор `--check`. Implementation gates сохраняют README commands,
controlled offline tests, обе платформы mypy, Ruff/Bandit и старые contract artifacts.
Network изменение требует полного regression suite; финал — чистый tracked-only
checkout, fresh lock installation, wheel/sdist import/CLI/new-process handoff.
CI/scanner/browser, которые фактически недоступны или не запускались, записываются
как unavailable/not run, а не passed. Commit/push после каждого завершённого задания
с автором alexey, без generated runs, caches, force push и Codex co-author.
