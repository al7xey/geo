# Security review фактической реализации шага 0

01.11.2026. Проверены исходный commit 0fa9d78 и исправления пакета 0.3.2.
Предыдущие отчёты не использовались как доказательство. Локально Windows 11/NTFS,
Python 3.12.10, обычный пользователь: 351 passed / 3 skipped. Внешний CI run 41310258426:
Windows 2025 и Ubuntu 24.04 successful, по 352 passed / 2 platform-only skipped;
реальный file symlink отдельно без skip. Предыдущие counts ниже — история проверок.

## Модель угроз

Недоверенные данные: введённый URL, DNS, каждый redirect/Location, HTTP-заголовки,
body/content-encoding, пути и содержимое ранее сохранённого каталога. Возможны SSRF,
rebinding, scope/TLS/proxy bypass, decompression bomb, slow drip, лог-инъекция,
утечка credentials/query/cookies, traversal/link/race, повреждение/подмена evidence.

Доверяем коду процесса, его конфигурации, ОС/SSL, прямому сетевому маршруту и UTC часам.
Корень вывода выбирает вызывающая сторона. Защищаемся от подмены имён/ссылок на пути,
но SHA-256 не аутентифицирует владельца, способного переписать весь каталог и хэши.
Администратор, изменение кода/памяти процесса, компрометация CA/маршрутизатора вне модели.
Никакие инструкции из HTML не исполняются. Десериализация — только bounded JSON.

## Подтверждённые дефекты и исправления

| ID / серьёзность | Место и условие | Безопасное воспроизведение | Исправление и результат |
|---|---|---|---|
| S01 / высокая | secure_io._win_open: directory handle с FILE_READ_ATTRIBUTES не блокировал rename, несмотря на отсутствие FILE_SHARE_DELETE. Атакующий с правом менять имена мог переименовать проверенный каталог и подставить reparse point перед pathname-based replace | A:test_windows_directory_swap_fails_while_pinned: исходник позволил rename; после исправления rename запрещён до close и разрешён после него | GENERIC_READ (включает FILE_LIST_DIRECTORY), FILE_SHARE_READ|WRITE без DELETE. Child writes продолжают работать; directory swap/hardlink/junction, atomic publication и crash tests проходят |
| S02 / средняя | http_client.get/_fetch_once: asyncio timeout сам не замечает продвижение инъецируемого clock или длительную синхронную observer-работу до следующего await; возможен положительный результат после deadline | A:test_virtual_slow_stream_stops_at_shared_deadline; test_observer_work_cannot_extend_deadline | Проверка remaining_seconds на каждом chunk, после finish и observer; fail без нового GET; потоки закрыты. Реальный slow-drip/read timeout тест также проходит |
| S03 / средняя | models.SiteValidationResult; handoff._scope_history/load_step1_input: принимались смешанные известные версии; история проверялась по конечному scope; хэши отдельных файлов не доказывали соответствие decoded text исходному HTML | A:test_artifact_forgery_rejected: подмена text/encoded/observation с обновлённым hash, версии/scope/path; E:corrupted/unknown/expiry; I:new_process | Версии связаны; переходы и причины retry/fallback проверены хронологически; observation сверяется с attempt; bounded content decoding и повторная классификация связывают representations. INPUT_ARTIFACT_INVALID, без сети/пустого HTML |
| S04 / низкая, корректность | http_client: широкий catch TimeoutError маскировал дефект resolver; finally observer мог заменить исходное исключение Pydantic ValidationError | A:test_timeout_programming_bug_is_not_network_failure (исходно падал другим исключением), E:test_programming_defect_is_not_site_unavailability | Только expired asyncio timeout становится BUDGET_DEADLINE; unexpected defect не публикуется как нормальная попытка; исходный TimeoutError/RuntimeError сохраняется, site не создаётся |
| S05 / средняя, dev dependency | pyproject/uv.lock: pytest 8.4.2, PYSEC-2026-1845 / CVE-2025-71176. По advisory: локальная атака на /tmp/pytest-of-* на Unix; runtime проекта от pytest не зависит | pip-audit 2.10.1 по pinned export: две дублирующиеся записи одного advisory в одном пакете | pytest 9.0.3 (указанная исправленная версия), lock обновлён; 339 passed; повторный аудит 56 зависимостей без skip и известных vulnerabilities |
| S06 / низкая | cli.parser: стандартная argparse.error печатала отвергнутый аргумент целиком; лишний URL мог раскрыть credentials/query в stderr | A:test_cli_parse_error_does_not_echo_rejected_secret | Безопасный parser печатает usage и фиксированную ошибку без исходных аргументов; marker отсутствует в stderr, exit=2 |

A/E/I — test_acceptance.py / test_step0_end_to_end.py / test_connection_integration.py.
Дополнительно исправлена POSIX-ветка: rename/unlink/mkdir относительно directory FD,
вместо повторного pathname access. Первоначально это следовало из анализа гонки; теперь реальная directory-swap
регрессия выполнена на Ubuntu CI (результаты платформенной приёмки ниже).

## Независимая проверка сетевой архитектуры

- До соединения проверяются все DNS-адреса; mixed public/private запрещён целиком.
  Numeric IP передаётся AnyIOBackend; peer IP/порт сверяются до передачи HTTP.
- Host/SNI остаются исходными; CERT_REQUIRED и check_hostname проверяются в constructor
  и непосредственно start_tls. Реальные тесты valid/wrong-host/untrusted CA для IPv4/IPv6
  выполнены; отдельно выполнен peer mismatch с отсутствием HTTP-байтов.
- Новое соединение заново проверяет DNS. Redirect к запрещённому IP не получает GET,
  счётчик HTTP не увеличивается. DNS-кэш и скрытые retries отсутствуют.
- IPv4-mapped, IPv6 transition, loopback/private/link-local/reserved/multicast/metadata
  запрещены. Неоднозначные numeric/percent/IDNA формы отвергаются до соединения.
- Proxy явно неподдержан; trust_env=false, фиксированный CA bundle, cookies не переносятся.
  TLS не выключается ради тестов. Только тестовые процессы разрешают fixture loopback;
  production-флага для снятия public-only нет.
- aiter_raw сохраняет content-encoded bytes; bounded zlib decoder ограничивает результат
  до помещения в буфер. Unsupported/multiple/truncated encodings закрывают поток.
  Общий timeout охватывает DNS/connect/read/delays; chunk/time checks дополняют его.

## Файлы, секреты и публикация

Имена raw — UUID, внешние URL не используются как пути. EvidenceFile запрещает absolute,
traversal, backslash, Windows devices/ADS и неоднозначные компоненты. Путь не resolve-ится
с последующим небезопасным open: проверяются реальные handles, reparse/link count и тип.
Чтение ограничено до allocation; loader ограничивает один файл 64 MiB и суммарно 256 MiB,
JSON 16 MiB; выбранные тела дополнительно ограничены config. Duplicate-key metadata отвергается.

Реальные hardlink и Windows junction тесты подтверждают отказ до fdopen/чтения sentinel.
Directory rename race проверена после исправления S01. Файловый symlink тест skipped:
WinError 1314. Это ограничение окружения, не успешный тест. NTFS junction всё же реально
проверяет reparse-point branch. POSIX FD ветка требует отдельного запуска на POSIX.

Каждый новый run получает private permissions. Полные URL/query/body могут содержать секреты
и сохраняются только локально. Headers по allowlist, Set-Cookie/Authorization исключены.
Сетевые диагностические logger records в области запроса редактируются вместе с exception
и stack data; тест проверяет отсутствие секретного marker и поддельной строки журнала.
CLI выводит только origin, причины, ID и пользовательский output path.

Evidence публикуется раньше manifest/journal и site; site — последний commit marker.
При disk preflight error не было DNS/GET. При blob failure нет положительного site.
KeyboardInterrupt удаляет временный файл; hard process exit оставляет исходники и running
journal, но не site. Автоматическая успешная загрузка незавершённого каталога невозможна.
Atomic replace не равен fsync транзакции всей папки при аппаратном сбое.

## Инструменты и фактический вывод

Актуальный синтаксис сверялся с установленными --help и первичными источниками:
[Bandit CLI](https://bandit.readthedocs.io/en/latest/man/bandit.html),
[pip-audit README](https://github.com/pypa/pip-audit#usage).

- Bandit **1.9.4**, `python -m bandit -r src -f json`: 0 findings, без nosec/массовых skip.
- Дополнительная проверка чистоты расширила Bandit на `src tools`: 9 low B101 в старых
  проверяющих инструментах заменены явными raise, итог 0 findings без подавления предупреждений.
  Ruff также проверяет все `src tests tools`. В новом изолированном окружении и с новым
  pip-audit cache повторены 339 passed/1 skipped и аудит 56 зависимостей/0 vulnerabilities.
- pip-audit **2.10.1**, сначала --no-deps по pinned export; после исправления
  --require-hashes по полному uv export: **56 dependencies, 0 vulnerabilities, 0 skipped**.
  База была доступна. Предупреждение short/Unicode пути временного venv проверено:
  аудит завершился с кодом 0; это не сообщение о пропуске пакетов.
- Ruff **0.14.3**, mypy **1.18.2** strict: без ошибок; uv **0.9.7** lock/pip check проходят.
- pytest **9.0.3**: 339 passed, 1 skipped; настоящий TCP/TLS и отдельный subprocess consumer
  выполнены. Сканеры не заменяют эти проверки и ручной анализ.

Выводы scanner проверены вручную. Устаревший pytest был dev-only, но исправлен для
воспроизводимости тестов. Advisory дополнительно сверен с
[базой PyPA](https://github.com/pypa/advisory-database/blob/main/vulns/pytest/PYSEC-2026-1845.yaml)
и [changelog pytest 9.0.3](https://docs.pytest.org/en/stable/changelog.html#pytest-9-0-3):
исправление insecure temporary directory относится к CVE-2025-71176.
Признаков опубликованного секрета в изменяемом коде/фикстурах
не обнаружено; отдельный полный forensic secret-scan истории Git не выполнялся.

## Остаточные ограничения

Completion audit 0.3.1 дополнительно проверил import через audit hook в новом процессе:
любые socket events, запись файлов и eager PSL read при импорте запрещены. Проверены
CLI-коды 2/5/70/130 с отсутствием лишних GET, редактированием деталей и failed commit
при storage/cancellation; UTC rollback отвергается до чтения body. Исправлено неверное
добавление ACCESS_CHALLENGE к подтверждённому ACCESS_LOGIN (низкая серьёзность корректности,
policy.classify_result): regression test_login_barrier_does_not_invent_a_challenge проверяет
reason_codes/next_action, отсутствие fallback и отказ handoff при единственном GET.

Не проверены аппаратная потеря питания, враждебный маршрутизатор и полный forensic scan
истории Git. POSIX filesystem integration и file symlink реально выполнены в CI.
Датированный public smoke в отчёте приёмки не доказывает универсальную доступность. Один проверенный
IP без перебора остальных может снизить доступность. Неподдержанные proxy, HTTP2, brotli,
raw-deflate и JS дают явные ограничения. Большие ответы ограниченно буферизуются; все
попытки bounded, но процесс не имеет отдельной OS memory quota. SHA-256 не заменяет подпись.
Отсутствие предупреждений не означает «код полностью безопасен».

## Платформенная приёмка и patch 0.3.2

Внешняя Windows 2025 job в run 41308165433 (commit 4fc456e) фактически прошла:
340 passed, отдельный file-symlink case 1 passed, JUnit gate tests=1 / skipped+failed=0;
mypy/Ruff/Bandit, audit и build также successful. Это закрывает прежний Windows-блокер,
без изменения настроек ОС пользователя. Ubuntu обнаружил один дефект fixture: denied()
не принимала src_dir_fd/dst_dir_fd у POSIX os.replace. Исправлена сигнатура заглушки,
проверка OUTPUT_WRITE_FAILED и все security-проверки сохранены.

Два дополнительных сценария хранения:

- S07, средняя серьёзность: src/geo_audit/evidence.py, EvidenceStore.verify использовал
  file_digest до EOF после
  одной проверки stat. Растущий конкурентно файл мог неограниченно продлевать hash-read.
  test_verification_bounds_reads_when_evidence_keeps_growing на старом коде запрашивал
  262144 bytes при declared size=3 и падал. Теперь чтение ограничено declared size + 1;
  лишний байт обнаруживает рост, короткий поток — усечение; SHA-256 остаётся обязательным.
  Регрессия проходит и проверяет отсутствие лишних чтений, не только сообщение ошибки.
- S08, средняя серьёзность: src/geo_audit/secure_io.py, open_confined.
  POSIX FIFO может заблокировать O_RDONLY open до fstat,
  поэтому проверка regular-file после open сама по себе недостаточна. Добавлен O_NONBLOCK,
  затем прежний fstat/type/link check до fdopen. Семантика — Linux open(2):
  https://man7.org/linux/man-pages/man2/open.2.html . Реальный FIFO-тест с timeout дочернего
  процесса и запретом fdopen фактически прошёл на Ubuntu CI run 41310258426.

Также добавлена POSIX regression на rename pinned raw → parked и подстановку symlink
наружу между записью tmp и replace. Она требует неизменности внешнего sentinel/source,
отсутствия внешнего target и отказа последующей верификации изменённого namespace.
Это реальная проверка directory FD, а не замена проверки пути resolve/stat.
Итог run [41310258426](https://github.com/al7xey/geo/actions/runs/41310258426), commit 88d3c1b:
обе ОС successful; S07/S08 и parent-swap регрессии подтверждены, no-skip symlink gate пройден.
Linux mypy обнаружил 12 platform-only attr ошибок: os.name guards заменены на
sys.platform, без ignore; strict types Linux/Windows проверяются на обеих CI ОС.

S09, низкая серьёзность, отказ handoff: src/geo_audit/evidence.py, EvidenceStore.publish
не учитывал caps loader в src/geo_audit/handoff.py
(64 MiB/file, 256 MiB/run), поэтому большой завершённый run мог получить positive site,
который загрузчик отвергал. Условие — большой ответ/накопленная история или увеличенный config.
Общий verify_evidence_limits теперь вызывается producer и обоими handoff до чтения evidence.
Четыре безопасных сценария с уменьшенным test-only cap воспроизводят оба лимита:
test_producer_refuses_result_that_handoff_cannot_load и
test_loader_enforces_shared_limits_before_evidence_open. Проверяют failed/false, отсутствие
site commit, сохранение observations, ровно один GET, неизменный budget и запрет
verify/verified_bytes при превышении. Все четыре прошли локально и в обеих CI ОС.
