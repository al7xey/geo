# Архитектура принятого шага 0

01.11.2026. Пакет 0.3.2. Основа: подробный алгоритм шага 0, вход шага 1 §1.1,
восемь промптов и решения PROJECT_CONTEXT.md. Область: шаг 0 и handoff;
robots, обход, браузер и платные API не реализуются.

| Ответственность | Компонент | Работающая граница |
|---|---|---|
| Контракты | config, contracts, models, errors | Frozen/strict Pydantic, обязательные nullable, закрытые коды и версии |
| URL и решения | urls, scope, policy, psl | host/origin/path отдельно; чистые таблицы; локальный PSL/private |
| Сеть | connection, http_client, body | Public-only, numeric pin, peer, Host/SNI/TLS; ручной GET и bounded decoding |
| Ресурсы | timing, budget | clock/sleep/jitter; 60 секунд/15 попыток внутри 300/150 |
| Оркестрация | api.validate_site | Observer сохраняет завершённые попытки и redirects до результата |
| Содержимое | content.assess_response | HTML/JS/барьер/подозрение, основания и confidence; HTTP отдельно |
| Доказательства | evidence, secure_io | Новая приватная папка; no-follow handles; SHA-256; fsync/atomic replace |
| Передача | handoff | Готовые FetchResult/Budget либо проверка сохранённого каталога |
| CLI | cli, __main__, redaction | asyncio.run, безопасный stdout, библиотека без input |

`network.Step0Client` — протокол исполнителя. Неиспользуемые DestinationChecker/AliasVerifier
удалены. Нет БД, серверов, очередей, DI-фреймворка. Async нужен для общего deadline DNS,
connect/read/backoff; запросы последовательные. Budget не является распределённой квотой.

## API

Экспорты перечислены в src/geo_audit/__init__.py. Основные:

- `await validate_site(input_url, config, client=None, budget=None, *, store=None,
  output_dir=Path('output/runs'), timing=None, parent_run_id=None) -> SiteValidationResult`;
- `prepare_step1(site, *, budget=None, timing=None) -> Step1Handoff`;
- `load_site_result(run_directory, ...) -> SiteValidationResult`;
- `load_step1_input(run_directory, *, timing=None, expected_config_hash=None) -> Step1Handoff`.

Инъецируемый клиент обязан ровно один раз доставлять завершённые события observer.
Несогласованная история — программная ошибка. Импорт не начинает аудит, не читает PSL,
не создаёт SSL context или каталог запуска. SafeHttpClient явно получает immutable PSL
(один раз на процесс), переиспользует проверяемый SSLContext, но создаёт новый socket/pool
для каждого GET. DNS не кэшируется. Cookies, proxy и env trust отключены.

## Статусы

| Наблюдение | status | can_enter_step1 |
|---|---|---|
| Полный пригодный HTML 200 без предупреждений | ok | true |
| HTML после fallback/downgrade/retry/ошибки декодирования | partial | true |
| JS-кандидат / подозрение с разрешённой диагностикой | partial | true |
| Барьер, пустое/бинарное/неполное/слишком большое тело 2xx | partial | false |
| Безопасный неподтверждённый внешний хост | needs_input | false |
| Ошибка ввода/назначения, окончательная сеть, HTTP error, 206, бюджет, storage | failed | false |

Ни 200, ни длина текста сами по себе не доказывают пригодность. CAPTCHA в короткой
контактной форме не равна dedicated challenge. JS не исполняется. next_action централизован
в policy и проверяется моделью. can_enter_step1 не даёт robots-разрешения.

Без HTTP status=null; без соединения connected_ip=null; без пригодной передачи
selected_evidence_id/effective_url/origin=null. Nullable-поля обязательны в JSON.
request_sent означает начало транспортной попытки, включая отказ соединения;
это не доказанная отправка HTTP-байтов. DNS/target rejection до транспорта не списывает попытку.

## Публикация и восстановление

Тела/наблюдение → raw/manifest.json → journal.json. В конце проверяются evidence,
записываются manifest/config/budget, последним site.json. Имена — внутренние UUID.
Новый запуск не переиспользует существующую папку. Файлы fsync до atomic replace.
Windows: GENERIC_READ, FILE_SHARE_READ|WRITE без DELETE для каталогов; листья без
следования reparse points и без write/delete sharing. POSIX: O_NOFOLLOW/openat,
dir_fd для rename/unlink/mkdir. Windows проверен фактически; POSIX интеграция не проводилась.

Отмена закрывает ресурсы, сохраняет failed и завершённые наблюдения, затем пробрасывает
CancelledError. Ошибка диска даёт OUTPUT_WRITE_FAILED без положительного commit.
Hard exit может оставить .tmp, raw и running-journal; без site.json запуск не завершён.
Возобновление не реализовано. Atomic rename не обещает транзакцию каталога при потере питания.

Step1Input вложенно содержит site: версии, URL/origin/scope, status/reasons,
выбранные response/evidence, TLS-историю, BudgetSnapshot. Step1Handoff добавляет HTML bytes,
текст и действующий Budget. В процессе FetchResult/Budget передаются теми же объектами.
Загрузчик без сети проверяет версии, хронологию переходов, пути, хэши, связь encoded/entity/text
и свежесть. Повреждение даёт INPUT_ARTIFACT_INVALID; пустой HTML не подставляется.

Монотонный timestamp не сериализуется. После загрузки расход = max(saved elapsed,
UTC now − started_at); дальнейшее монотонное время прибавляется к расходу. Счётчики сохраняются.
UTC ранее captured_at, истёкший total_deadline или исчерпанная квота запрещают вход.
Новый запуск явный, с новым run_id; часы ОС доверенные.

## Версии

Schema 1.2.0, algorithm step0-1.2, config step0-config-1.2.0; SHA-256 полного config.
Согласованные 1.0/1.1 контракты читаются для истории; неизвестные/смешанные версии отвергаются.
Текущий выбранный ответ требует все четыре полных представления. Каталоги незавершённой
реализации без них требуют нового запуска. Учебная schema=1.0 из PDF не поддерживается.
JSON Schema генерируется из моделей; межполевые инварианты дополнительно проверяет Python.
Примеры синтетические, с фиксированным временем; они не действующий handoff.

Подтверждения: [матрица](STEP_0_REQUIREMENTS.md), [приёмка](STEP_0_VALIDATION.md),
[security](STEP_0_SECURITY_REVIEW.md), [измерения](STEP_0_PERFORMANCE.md).
