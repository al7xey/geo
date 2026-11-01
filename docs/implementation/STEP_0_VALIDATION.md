# Финальная приёмка шага 0

01.11.2026. Область: только шаг 0 и вход шага 1. Windows 11 22631/NTFS,
Python 3.12.10, uv 0.9.7; пакет 0.3.1. Промпты/алгоритмы сверены по составным требованиям:
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
| pytest -q -ra | **339 passed, 1 skipped**; единственный skip — реальный file symlink, Windows WinError 1314 |
| mypy 1.18.2, strict | Success, 24 source files |
| Ruff 0.14.3 (все src/tests/tools) | All checks passed; область расширена при проверке чистоты |
| python tools/generate_step0_artifacts.py --check | 32 воспроизводимых артефакта, без сети |
| Примеры Python + Draft 2020-12/FormatChecker | Все проходят; архивный 1.0 читается |
| Bandit 1.9.4, -r src tools -f json | 0 findings, без массовых исключений |
| pip-audit 2.10.1 --require-hashes по uv export | 56 зависимостей, 0 известных vulnerabilities, 0 skip; база доступна |
| compileall src/geo_audit | Успех |
| uv build --wheel | geo_audit-0.3.1-py3-none-any.whl собран |
| Wheel в отдельном tmp/wheel-smoke venv с runtime pins/hashes | Установлен; -I/-B import из site-packages, schema config/PSL hash и CLI --help проходят |
| Пять benchmark-сценариев before/after, по 3 повтора | Агрегаты в Git, сырые samples локально; HTTP/delays неизменны, DNS/copies уменьшены |
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

## Инвентаризация, чистота и изолированная проверка 0.3.0

Дополнительное поручение 11–12 от 01.11.2026. Старт: main=c786373, чистый git status,
посторонних изменённых/неотслеживаемых файлов нет. Изменения этой задачи отдельные.
В итоговом дереве 100 файлов (без удаления полезных данных):

| Группа | Файлов | Назначение / решение |
|---|---:|---|
| Корень | 7 | .gitattributes/.gitignore/.python-version, AGENTS, README, pyproject, uv.lock |
| src | 27 | 24 Python-модуля, py.typed, PSL и README с upstream/version/hash/MPL-2.0 |
| tests | 8 | Содержательные контрактные, transport, real TCP/TLS, handoff и acceptance сценарии |
| examples/contracts | 24 | Генерируемые синтетические контракты/представления и архивный 1.0; не живой аудит |
| schemas | 8 | Генерация из актуальных моделей; Python-инварианты проверяются дополнительно |
| docs | 17 | Алгоритмы, исходный PDF/извлечение, промпты, актуальные отчёты/поля; черновики помечены историческими |
| output/pdf | 3 | Согласованные PDF шагов 0/1 и восьми промптов; сохранены побайтово |
| tools | 6 | Генерация контрактов, benchmark, проверка archives, три исторических PDF-инструмента |

Локальные игнорируемые группы до нового export: .venv (5483 файла), .bootstrap (878),
pytest/mypy/Ruff caches (1171), tmp (1348). Это рабочая среда, рендеры, тестовые среды,
аудиторские/benchmark логи и сборки, а не исходники. Во время проверки tmp вырос за счёт
новой изолированной среды и архивов. Они не удалялись и не включаются в Git.
Новый tools/check_distribution.py — единственный новый исходник этой задачи, включён осознанно.
Неизвестных неотслеживаемых файлов при инвентаризации не обнаружено.

Из текущего индекса исключены два ранее отслеживаемых сырых benchmark JSON:
step0-benchmark-before.json и step0-benchmark-after.json. `git rm --cached` оставил их
локально; история не переписывалась. Агрегаты/методика в STEP_0_PERFORMANCE.md и инструмент
сохранены. Это требование чистоты, не отказ от результатов измерений.
Прочих отслеживаемых caches/venv/installers/audit pages/секретов/случайных backup не найдено.
PSL, fixtures (включая одинаковые байты разных representations), lock, py.typed,
схемы и PDF проверены по назначению и сохранены. Рекурсивное удаление/git clean не применялись.

.gitignore расширен: build/dist/install artifacts, env/IDE/cache, coverage/profiles,
benchmark logs, .env/keys, backups/tmp и run outputs. Проверены representative paths
через git check-ignore, а не только наличие строк. Модель хранения: временные рендеры,
screenshots и контактные листы размещаются в tmp либо output/renders/screenshots.
Сканирование текущих отслеживаемых файлов на private-key/GitHub-token/AWS-key patterns
дало 0 совпадений; это ограниченная проверка, не forensic аудит истории и не доказательство
отсутствия любого возможного секрета.

### Состав установочных архивов

У исходного default sdist было 102 файла, включая docs/PDF/tests/benchmark logs.
Wheel был чистым. Добавлен явный sdist include в pyproject по официальной конфигурации
[Hatch](https://hatch.pypa.io/latest/config/build/).
Теперь wheel — 31 файл, sdist — 33. Содержат только runtime Python, py.typed, PSL/provenance,
метаданные; sdist также README, pyproject, uv.lock, .python-version и автоматически включённый
.gitignore/PKG-INFO. Нет tmp, tests, test certificates, docs/PDF или audit/benchmark results.
`python tools/check_distribution.py DIR` проверяет состав и обязательные файлы без extraction.

Сборки из рабочей папки и чистой копии совпали побайтово:

- wheel SHA-256: 6558da5dd9335c36c7fc09803cb112c96d2dcba6b8ac5881c0c04f34b133a6a6;
- sdist SHA-256: ce8dfe83f7d60d1e982349dc0b839323f0345eeef8b3701a6580d856566df296.

Эти hashes относятся к версии README/pyproject этой задачи; сами бинарные архивы в Git
не включаются. Изменения только последующих отчётов не влияют на их содержимое.

### Проверка только из версионируемых исходников

Создан новый каталог tmp/geo-clean-856e4i93 из 99 текущих tracked файлов и нового
check_distribution.py, который включается в этот коммит. Не копировались .git, .bootstrap,
.venv, caches, tmp, benchmark samples или скачанные страницы. В новой папке:

- `uv --no-cache sync --locked --python 3.12`: 59 пакетов заново подготовлены/установлены,
  download/build без uv-кэша, 20.62 s preparation + 1.98 s install;
- `python -m pytest -q -ra`: **332 passed, 1 skipped за 91.54 s**. File symlink skip
  обусловлен WinError 1314; реальный TCP/TLS и новый процесс handoff действительно выполнены;
- mypy strict (24 source files), Ruff `src tests tools`, Bandit `src tools`: проходят;
- генератор --check: 32 артефакта; compileall src/tests/tools; lock --check / pip check: проходят;
- pip-audit --require-hashes с новым --cache-dir tmp/audit-cache: 56 зависимостей,
  0 vulnerabilities, база реально доступна, старый cache не использовался;
- `uv --no-cache build`, archive checker, CLI --help, validate Step1Input: проходят;
- benchmark smoke по всем 5 сценариям, один повтор каждого, завершён; HTTP/DNS/identity-copy
  показатели согласованы. Это smoke, не новая серия before/after и не новый cold-start SLA.

Wheel затем установлен вместо editable-пакета; импорт -I/-B подтверждает site-packages,
PSL hash и версию config. Отдельные шесть real-runner/new-process handoff cases повторены
на установленном wheel. Генераторы PDF не запускались и исходные PDF не перезаписывались.
Их код включён в Ruff/compileall/Bandit; исторический workflow требует отдельного окружения
и рендеров, как явно указано в README.

Расширенный Ruff обнаружил 39 замечаний в старых PDF-tools: форматирование/imports исправлены.
Bandit по tools обнаружил 9 low B101 (assert в проверяющих инструментах). Они заменены
явными raise ValueError/RuntimeError, чтобы проверки действовали также при python -O.
Предупреждения не скрывались nosec/массовыми исключениями. Поведение runtime шага 0,
контракт handoff и лимиты этой задачей не менялись.
## Повторный completion audit: patch 0.3.1

Перед продолжением прочитаны оба приложения pasted-text-1/2; начальное состояние
main=61ab175, git status чистый. Предыдущий этап дал подтверждённый прогресс (коммит,
чистый export, тесты/сборки), но завершение заново проверено по действующим требованиям.

Найдено дополнительное расхождение: classify_result для любой access_barrier добавлял
ACCESS_CHALLENGE, даже если content assessment подтверждал только ACCESS_LOGIN.
Это не открывало шаг 1, но формировало недостоверную дополнительную причину. Исправлено
в policy.classify_result; пакет 0.3.1, schema/algorithm/config остаются 1.2 и не понижены.
Сквозная регрессия проверяет точные reason_codes/next_action, сохранённый site, отсутствие
fallback, один GET и отказ handoff. Изменение не затрагивает условия can_enter_step1.

Дополнительно усилены прямые доказательства:

- subprocess import с audit hook запрещает все socket events, запись/создание/удаление
  файлов и eager чтение PSL; импорт проходит, рабочая папка остаётся пустой;
- реальные CLI-проходы для exit 5/70/130 и loader exit 2 проверяют transport/DNS calls,
  отсутствие secret marker, отсутствие положительного site и точные бюджетные счётчики;
- UTC rollback, как и expiry, отклоняется до открытия body, без сети;
- прежние шесть real TLS→runner→disk→новый consumer сценариев повторены на wheel 0.3.1.

Актуальные результаты: локально 339 passed / 1 skipped за 64.82 s; новый tracked-only
export tmp/geo-final-20sb3gkb, без старых caches/venv/логов — 339 passed / 1 skipped
за 97.87 s. После усиления import hook отдельно повторён его тест и в чистой копии.
uv --no-cache sync --locked заново установил 59 пакетов (19.19 s prepare, 2.72 s install).
Mypy strict 24 source files, Ruff src/tests/tools, Bandit src/tools, lock/pip check,
генератор --check (32), CLI/API и archive checker проходят. pip-audit с новым cache:
56 dependencies, 0 vulnerabilities/skip. Benchmark smoke всех пяти сценариев выполнен;
новая серия before/after не заявляется. На установленном wheel: 6 handoff cases passed.

Wheel 31 файл / sdist 33 файла, текущая и чистая сборки 0.3.1 побайтово совпали:

- wheel SHA-256 ac8f3b6ece610dd671c98240e53c636500f3c029f6813b0f3d69e095a3788d4b;
- sdist SHA-256 dc4b205b6e74fb71c9e28032e6a1d19181d33b16f0efe7bf91b6f09726bba4a9.

Чистота: число необходимых tracked файлов остаётся 100; новых отчётов/fixtures/окружений
в Git не добавлено. PDF и схемы не менялись. Исторические измерения/выходы 0.3.0 выше
сохранены как результаты соответствующего этапа; актуальные gates приведены здесь.

Строгая проверка реальной file symlink остаётся незавершённой, а не засчитанной:
SeCreateSymbolicLinkPrivilege не назначена текущему токену (whoami /priv), создание
возвращает WinError 1314. Для закрытия нужна Windows-среда с этим правом или отдельная
готовая POSIX/CI-среда. Docker CLI отсутствует; wsl --list возвращает exit 1 и help,
у текущего пользователя нет зарегистрированного Lxss-окружения. ОС/привилегии не изменялись.
Hardlink, junction и directory rename race проверены; эти результаты не подменяют file
symlink тест. Остальные ограничения security review сохраняются. Полное завершение
строгого security gate до этой внешней проверки не заявляется.

## Подготовка внешней платформенной проверки

По возобновлённому поручению добавлен .github/workflows/step0.yml: Windows 2025 / Ubuntu 24.04, Python 3.12.10, uv 0.9.7, read-only contents permission, SHA-pinned actions, без caches и с timeout 15 минут. Все тесты, типы, lint/security/dependency audit, генерация и сборка выполняются заново. Отдельный JUnit gate требует один реально выполненный file-symlink case, ноль skip/failure. До получения результата это подготовка, а не успешная проверка. GitHub Actions для репозитория включён; системные настройки пользователя не менялись. Новый tracked workflow увеличивает инвентаризацию до 101 файла.

## Ревью кода и ручное тестирование: 01.11.2026

По поручению пользователя проверены реализация шага 0 и фактический запуск. Исходный
полный прогон: 339 passed, 1 skipped (Windows file-symlink privilege). Ruff, strict mypy
и генерация 32 контрактных артефактов прошли.

Подтверждённый P2-дефект: ответ HTML с `<![weird]>` или `<![unexpected[broken]]>`
вызывал AssertionError стандартного HTMLParser и прерывал validate_site в observer,
не сохраняя нормальный итог. Два end-to-end регрессионных теста воспроизвели падение.
Исправление ограничено известными parser input errors: результат suspicious_html/partial,
низкая уверенность, diagnostic_allowed и сохранённое основание. Непричастные исключения
не подавляются. Тесты проверяют также сохранение и загрузку исходного HTML в handoff
без дополнительных запросов.

Для ручного тестирования добавлена команда `try-step0`. Пять тестов проверяют два реальных
прохода runner на фикстурном транспорте с разными run_id и бюджетами, отсутствие query-секрета
в сводке, продолжение после известной ошибки, распространение программного дефекта,
EOF без сети/создания каталога и общие CLI-настройки. Релевантная выборка с существующими
content-сценариями: 16 passed. CLI --help и завершение по пустому stdin выполнены отдельно.

Живые наблюдения из этой Windows-среды:

| URL | Результат |
|---|---|
| https://example.com | ok, вход шага 1 пригоден |
| http://example.com | ok, выбран HTTP |
| https://www.python.org | ok, вход шага 1 пригоден |
| https://expired.badssl.com, fallback выключен | failed, HTTPS_TLS_CERTIFICATE_ERROR |

Это ограниченные GET входных страниц, без обхода шага 1, браузера и платных API.
Raw/site результаты оставлены в output/runs, в Git не добавлены.

После изменений Ruff src/tests/tools, mypy (25 source files), Bandit src/tools
(0 findings) и генератор --check (32) прошли. pip-audit: 56 dependencies,
0 известных vulnerabilities. Wheel/sdist собраны и проверены: 32/34 файла соответственно,
добавлен только runtime-модуль интерактивного CLI. Это не новая полная security-сертификация:
ограничения предыдущего security review и непроверенной POSIX-интеграции сохраняются.

Финальный полный прогон после завершения параллельных правок: 346 passed, 1 skipped
за 90.26 s. Единственный skip — существующее ограничение права file symlink в Windows.
Повторные Ruff и mypy прошли; git diff --check не обнаружил ошибок whitespace.

## Исправления по первой Windows/Linux проверке

Run 41308165433, commit 4fc456e: Windows 2025 successful, 340 passed за 27.39 s,
file symlink 1 passed без skip; Linux — 1 failed / 337 passed / 2 Windows-only skipped.
Единственное падение Linux связано с fixture denied(), не принимающей dir_fd kwargs.
Сигнатура исправлена без удаления проверок. В 0.3.2 дополнительно ограничен hash-read
растущего файла и добавлен O_NONBLOCK для POSIX FIFO, плюс регрессия parent-swap.

Локальная изолированная копия C:/GEO/tmp/step0-acceptance-032:
340 passed / 3 skipped (нет Windows symlink privilege, два POSIX-only сценария).
Mypy 24 runtime files, Ruff src/tests/tools, Bandit и 32 generated artifacts проходят.
Это отдельный worktree: параллельные правки CLI/content/interactive из основного C:/GEO
не копировались, не перезаписывались и в данный коммит не включаются.
Новые Linux-сценарии до второго CI-прогона остаются ожидающими фактической проверки.