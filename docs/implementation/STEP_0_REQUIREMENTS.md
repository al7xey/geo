# Соответствие требованиям шага 0

Обновление 01.11.2026: таблица ниже фиксирует первый этап. URL/PSL/policy, retry/redirect,
bounded decoding и защищённый транспорт реализованы последующим этапом:
[актуальный отчёт](STEP_0_NETWORK_AND_POLICY.md). Классификатор HTML и final runner ещё требуются.

Дата: 31.10.2026. «Контракт» означает определённый интерфейс/инвариант, **не** работающую
проверку сайта. Все сетевые строки требуют отдельной приёмки по разделу 8 алгоритма.
Ссылки разделов относятся к `docs/steps/STEP_0_ALGORITHM_v1.md` и одноимённому PDF.

| Раздел | Требование | Основа реализации / проверка | Что ещё требуется |
|---|---|---|---|
| 0.1–0.3 | URL, доступность, scope, доказательства отдельно от GEO-оценки | Контракты/API, никаких платных этапов | Исполнитель шага 0 |
| 1.1 | Исходная строка, версии, run/parent_run, выходной каталог | Step0Config/SiteValidationResult, EvidenceStore с явным run_directory | Создание нового run_id и каталога runner |
| 1.2.1–3 | Пробелы, controls, HTTP(S), credentials, порт | normalize_input; положительные/отрицательные URL-тесты | Fuzz/corpus перед сетевой приёмкой |
| 1.2.4 | IDN, ASCII/Unicode, выбранная IDNA | IDNA UTS46 non-transitional STD3; IDN-тест | Дополнительный корпус IDN |
| 1.2.5–6 | Путь/query/fragment, согласование с HTTPX | normalize_input, тесты повторных query и регистра | Расширенный корпус неоднозначного синтаксиса |
| 1.3 | Глубокий путь не заменяется корнем | entry_path и исходный URL в модели | Runner не должен вводить root fallback |
| 2.1–2.2 | HTTPS-first, явный HTTP, TLS verify, один fallback | defaults, TLSState, http_fallback_eligible, фикстура TLS→HTTP | Реальное выполнение цепочки и запрет повторного круга |
| 2.1 | NXDOMAIN и временный DNS не становятся fallback | Табличные unit-тесты policy | Классификация исключений DNS адаптера |
| 2.1–2.3 | 403/барьер не вызывают смену схемы, downgrade сохраняется | FetchAttempt разделяет статус/ошибку, policy, ErrorDetail | Runner, TLS mapper, downgrade detection |
| 2.3 | HTTP-наблюдение и HTTPS-неопределённость не смешиваются | Отдельные попытки, tls_state, причины | Пользовательские формулировки фактического результата |
| 3.1 | Точные хосты, ports, origin, path_scope | Модели/config, инвариант scope, запрет скрытого расширения | Проверка портов/path перед каждым GET |
| 3.2 | Ручные 301/302/303/307/308, Location, visited, 5 hops | RedirectHop и протоколы | Разрешение относительного Location, цикл/hop enforcement |
| 3.3 | Наблюдаемый www-алиас, локальный PSL/private | AliasVerifier, обязательный psl_sha256 для алиаса | Выбор/поставка локального PSL, алгоритм проверки |
| 3.4 | Чужой хост: needs_input до запроса, allow-host повтор | Полная фикстура needs_input, target/evidence связи | Runner, повторный запуск с allow-host, тест отсутствия целевого GET |
| 3.4 | canonical/meta refresh не расширяют scope | Решение архитектуры, строгий scope | Наблюдения классификатора |
| 4.1 | GET, TLS, UA, язык, no env/proxy/cookies/retries | Step0Config и протокол HTTPX Request | Защищённый HTTPX-адаптер; реальные настройки клиента |
| 4.1 | Ограниченное потоковое тело до/после распаковки | Лимиты, bytes_read/decoded_bytes, EvidenceFile representations | Streaming, bounded decompression и запись |
| 4.2 | 60/300 секунд, 15/150 попыток | Budget и BudgetSnapshot, unit-тест общей квоты | asyncio.timeout вокруг всех операций |
| 4.2 | Таймауты 5/15/10/5 и 1 старт/хост/сек | Поля Step0Config | Передача в HTTPX и scheduler |
| 4.3 | 2 retries, jitter, Retry-After, RETRY_DEFERRED | Поля config, retryable statuses и код причины | Планировщик и тесты дат/секунд Retry-After |
| 4.4 | Public-only IPv4/IPv6/mapped, DNS rebinding | ResolvedTarget + отрицательные IP-тесты, контракты pinning | DNS resolver, связь проверенных IP с соединением и интеграционные тесты |
| 5.1 | HTML/JS/barrier/soft404/empty/binary/HTTP errors | Все content_state определены, фикстуры и инварианты | Классификатор содержимого и основания/уверенность |
| 5.1 | Sniff <=4KiB, encoding, replacements | Поля config/EvidenceRecord | Sniffing/ограниченное декодирование |
| 5.2–5.3 | Все status и точные условия can_enter_step1 | Архитектурная таблица, model validators, негативные тесты | Сбор наблюдений и применение всей таблицы runner |
| 5.3 | Недостаточно доказательств/ошибка хранения не дают успех | Publish/handoff сверяют файлы, тест отсутствующего raw и отказа replace | Перевод отказа обязательного сохранения runner в failed/OUTPUT_WRITE_FAILED |
| 5.4 | Различать ошибки ввода/сети/TLS/HTTP/содержимого | Закрытый ErrorCode, ErrorDetail, FetchAttempt | Адаптер исключений транспорта и классификатор |
| 6.1 | Полный site.json вместо учебного фрагмента | SiteValidationResult, schema и примеры 1.0.0 | Запись фактического аудита |
| 6.2 | ID/parent, URL/target, purpose, headers, время, TLS, размеры | FetchAttempt/RedirectHop, связи валидируются | Фактические наблюдения адаптера |
| 6.3 | raw, manifest, SHA-256, представления/полнота | EvidenceStore, EvidenceFile, реальные hash/write-тесты | Потоковая интеграция с сетью |
| 6.3 | Секреты, повторные заголовки, атомарный JSON | Header allowlist, tuple, redaction metadata, безопасные ошибки CLI | Фильтрация реальных заголовков адаптера |
| 7.1–7.3 | Клиент/бюджет инъецируются, API без input(), CLI | Async API и Protocol; step0 явно недоступен | Оркестрация с сохранением частичной истории |
| 8.1–8.2 | Полная приёмка на контролируемом транспорте | Текущие contract-тесты не объявляются сетевой приёмкой | Все 13 сетевых сценариев, TLS, streaming, rebinding, факт числа GET |
| 9.2; шаг1 §1.1 | HTML не загружается заново, scope/конфиг/бюджет не теряются | Step1Input, build/verify handoff, файлы/hash/deadline tests | Потребитель шага 1 вне текущего объёма |
| 9.4 | Исходник сохранён, версии/решения фиксируются | Контекст обновлён, решения датированы, SHA-256 PDF проверяются | Следующее изменение методики потребует новой версии |

Приоритет следующей реализации: (1) защищённый транспорт и общий deadline;
(2) retries/redirects/fallback с записью попыток; (3) классификатор содержимого;
(4) runner и CLI реального запуска; (5) полная приёмка §8. Шаг 1 не реализуется.
