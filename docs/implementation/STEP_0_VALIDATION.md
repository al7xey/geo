# Финальная приёмка шага 0

01.11.2026. Область: только шаг 0 и вход шага 1. Windows 11 22631/NTFS,
Python 3.12.10, uv 0.9.7; пакет 0.3.0. Промпты/алгоритмы сверены по составным требованиям:
[матрица](STEP_0_REQUIREMENTS.md). Исходные документы не подменены реализацией.

## Исходное состояние и происхождение изменений

До изменений: ветка main, origin https://github.com/al7xey/geo.git, HEAD 0fa9d78
«Implement step 0 orchestration and saved handoff; final review in progress».
`git status --short` был пуст: изменённых/неотслеживаемых файлов не было. Локальный автор
уже alexey / 189361628+al7xey@users.noreply.github.com; глобальная конфигурация не менялась.
Ранее существующие runner/content/secure_io/handoff не приписываются этой приёмке.

Baseline: 246 passed за 67.01 s; mypy 24 source files, Ruff без ошибок; Bandit 0 findings.
README/контекст/матрица отставали от кода и называли runner нереализованным. Аудит исходных
зависимостей нашёл один advisory pytest (две дублирующиеся записи); это исправлено, не скрыто.
Пять исходных benchmark-сценариев сохранены до оптимизаций, затем повторены из git archive
0fa9d78 тем же инструментом для сравнения с финальным кодом. Подробности и samples —
[измерения](STEP_0_PERFORMANCE.md).

## Финальные проверки

| Проверка | Фактический результат |
|---|---|
| uv sync --locked; lock --check; pip check | Установка по lock, 59 packages в resolution, зависимости совместимы |
| pytest -q -ra | **332 passed, 1 skipped**; единственный skip — реальный file symlink, Windows WinError 1314 |
| mypy 1.18.2, strict | Success, 24 source files |
| Ruff 0.14.3 (src/tests/benchmark/генератор) | All checks passed |
| python tools/generate_step0_artifacts.py --check | 32 воспроизводимых артефакта, без сети |
| Примеры Python + Draft 2020-12/FormatChecker | Все проходят; архивный 1.0 читается |
| Bandit 1.9.4, -r src -f json | 0 findings, без массовых исключений |
| pip-audit 2.10.1 --require-hashes по uv export | 56 зависимостей, 0 известных vulnerabilities, 0 skip; база доступна |
| compileall src/geo_audit | Успех |
| uv build --wheel | geo_audit-0.3.0-py3-none-any.whl собран |
| Wheel в отдельном tmp/wheel-smoke venv с runtime pins/hashes | Установлен; -I/-B import из site-packages, schema config/PSL hash и CLI --help проходят |
| Пять benchmark-сценариев before/after, по 3 повтора | Samples сохранены; HTTP/delays неизменны, DNS/copies уменьшены |
| Проверка всех 4 PDF по git baseline | Побайтово совпадают, hashes ниже |

Артефакты scanner/venv/build и незавершённые тестовые каталоги находятся в игнорируемом tmp,
в коммит не включаются. README содержит команды установки, CLI/API, выходы/exit codes,
пример Step1Input и ограничения. Scanner findings вручную разобраны в security review.

## Что доказывают тесты вместе

Требования принимаются по наблюдаемым действиям, а не наличию кода или одному unit-тесту.
Проверены URL/IDN/query/parser differences; exact host/path/port; PSL/private и observed www;
HTTPS-first/explicit HTTP; все fallback gates/причины; TLS→HTTP, broken HTTPS cycle;
NXDOMAIN/temporary DNS; relative redirects/loops/limits/external; retry/Retry-After/pacing;
15 попыток, общий deadline/slow drip; gzip/deflate/bombs/truncation/encoded cap;
HTML/JS/challenge/login/placeholder/204/206/binary/Content-Type/charset;
атомарная запись, disk preflight/blob failure/KeyboardInterrupt/hard exit;
отмена с сохранением наблюдений; программные исключения; версии/пути/хэши/связи evidence.

Отрицательные проверки подтверждают отсутствие запрещённого действия: connector.calls,
фактические HTTP-байты, socket closure, fdopen calls и budget counters. Например:

- private/mixed DNS и storage preflight failure — ни одного соединения и списания попытки;
- внешний/private/cyclic/over-limit redirect — целевого GET нет; scope не расширился;
- peer mismatch — соединение закрыто до отправки HTTP;
- hardlink/junction — sentinel не прочитан, fdopen не вызван;
- directory swap — rename не проходит до освобождения handle, затем проходит;
- истёкший/unknown-version вход — HTML не открывается, GET не выполняется;
- программная ошибка — библиотека пробрасывает исходное исключение, fake site отсутствует.

## Настоящие соединения и новый потребитель

Выполнены **17** контролируемых интеграционных сценариев в test_connection_integration.py:
11 сетевых плюс 6 сквозных. Реальны TCP, SSL, certificate verification, hostname/SNI,
Host, peer, stream cleanup. Положительным локальным серверам test-only predicate разрешает
loopback; production-флага снятия public-only нет. Отдельно проверено, что production
не подключается к loopback. Внешние сайты не требуются.

`test_real_runner_saved_input_in_new_process_without_get` выполняет настоящий validate_site
на HTTPS-сайте с контролируемым сертификатом, сохраняет реальные артефакты, запускает новый
Python-процесс. В нём DNS/connect API запрещены; число GET на сервере остаётся **1**.
Проверены success, допустимый partial (JS), denied, corrupted file, unknown version, expired.
Для success/partial сравниваются исходный HTML, allowed_hosts, path_scope, счётчик и остаток.
`crawl_permission=unknown`, обход не начинается. Эту проверку не подменяют JSON fixtures.

Внутрипроцессный путь дополнительно доказывает тождественность FetchResult/Budget и отсутствие
повторного GET. VirtualTime на +10 s уменьшает остаток; expiry >300 s запрещает вход.
Монотонный timestamp между процессами не передаётся: max(saved elapsed, UTC age), потом
новый monotonic elapsed. UTC rollback ранее captured_at отвергается. Новый run только явный.

## Исправленные расхождения

- Windows directory sharing действительно не блокировало rename с READ_ATTRIBUTES:
  GENERIC_READ + share READ/WRITE без DELETE исправляет гонку, сохраняет child writes.
- Смешанные версии/позднее расширение scope/несвязанные representations:
  согласованные версии, хронологическое replay решений, observation и encoded/entity/text checks.
- Deadline мог истечь внутри synchronous observer/инъецируемого потока:
  добавлены checks на chunks/finish/после observer, без скрытого продления.
- TimeoutError дефекта мог быть замаскирован как сеть либо заменён observer validation:
  истинный timeout различается по expired; исходный дефект выходит из библиотеки.
- CAPTCHA в маленькой контактной форме не должна подтверждать барьер:
  dedicated challenge требует также характерного title и доминирования инструкций.
- argparse мог раскрыть отвергнутый секретный URL: fixed message вместо echo arguments.
- pytest advisory устранён обновлением 8.4.2→9.0.3; lock проверен повторно.
- Текущий selected HTML требует все полные evidence representations; manifest в raw/.
  Синтетические примеры/генератор актуализированы. Неполные прошлые runs не мигрируются
  пустыми данными: INPUT_ARTIFACT_INVALID и новый запуск.

Эти уточнения завершают ранее незавершённую редакцию step0-1.2, документированы в контексте.
Оптимизация отдельно не меняет Step1Input, статусы, rate limits или TLS/security policies.
Подробные условия и регрессии каждого security finding — STEP_0_SECURITY_REVIEW.md.

## Сохранность PDF

Побайтовое сравнение с `git show 0fa9d78:PATH`, все четыре совпали:

| PDF | SHA-256 |
|---|---|
| docs/source/geo-audit-algorithm-2026-10-31.pdf | ef8d4df5902719f4350089d1bc803902fd66a73c9ec542742e5b2baaca820f48 |
| output/pdf/Шаг 0 - Проверка адреса сайта.pdf | 89377872fe7870d65c50dff55fa130474a51385b0e1abf20dfceb10e4b027660 |
| output/pdf/Шаг 1 - Чтение сайта и технические проверки.pdf | 139e2a75475ae343cf16683680c79b225b94b1f478895ed07822cc23944ddc66 |
| output/pdf/Промты для Шага 0.pdf | b7d9c5836b59d5a018cf91f0dfea3e890dc35b689ccca3be62f30cc23d176155 |

## Явные ограничения приёмки

Не проведены: POSIX filesystem integration, file symlink без Windows privilege,
живой smoke на публичном сайте, аппаратный power loss, враждебный маршрутизатор,
полный forensic secret scan Git history. Symlink skip не засчитан как pass.
Junction/hardlink/directory race реально выполнены. Production security не отключалась.

Монотонный budget — последовательный, не межпроцессная конкурентная квота. UTC часы доверенные.
Атомарность файла не обещает аппаратную транзакцию каталога. SHA-256 не подписывает происхождение.
Тела bounded в памяти; нет отдельной OS memory quota. Proxy/HTTP2/keepalive/JS/brotli/raw-deflate
не поддержаны. Первый проверенный IP может оказаться недоступен при наличии других рабочих IP.
Это известные ограничения, а не утверждение универсальной безопасности.

Следующий отдельный этап — реализация шага 1 по его алгоритму. В этой приёмке robots,
очередь URL, анализ бизнеса и платные этапы неприменимы: явно исключены поручением.