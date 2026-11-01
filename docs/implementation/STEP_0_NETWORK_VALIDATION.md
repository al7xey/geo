# Проверка URL/policy и сетевого клиента

> Исторические результаты 0.2.0. Финальная приёмка и ограничения 0.3.0:
> [STEP_0_VALIDATION.md](STEP_0_VALIDATION.md).

Дата: 01.11.2026. Windows, Python 3.12.10. Пакет 0.2.0.
Актуализирует, но не заменяет исторический отчёт первого этапа.

Последний полный запуск: **227 passed**. Из них **11** - контролируемые
интеграционные проверки с настоящими TCP/TLS; остальные работают без внешней сети.
Никакой аудит пользовательского/внешнего сайта не запускался.

| Группа | Что доказано |
|---|---|
| Контракты и схемы | Примеры, сериализация, новые версии, читаемость архивного примера 1.0, инварианты ссылок/бюджета/хранения |
| Чистые URL | IDN, path case, query повторы/порядок/percent/+, empty query, fragment, idempotence, независимая сверка HTTPX, неверные schemes/credentials/controls/host/ports/IP-формы |
| Location и scope | Относительные .././, empty query, repeated slash, exact hosts, segment path, extra ports, cross-scheme loops |
| Локальный PSL | Private tenant boundaries, suffix-only запрет, www по наблюдению, wildcard *.ck, exception !www.ck/!city.kawasaki.jp, IDN |
| Таблицы решений | HTTPS-first, явная схема, ограниченные retries/fallback, отсутствие fallback при DNS/HTTP/barrier, точные status/can_enter_step1 |
| Офлайн HTTP Core | HTTP parsing поверх контролируемых byte streams; фактическое число вызовов connector, headers, cookies/env isolation, retries, Retry-After seconds/date, 15-attempt cap, блокирование redirect до GET |
| Body limits | gzip bomb, truncation, unsupported encoding, encoded cap, exact decoded boundary, запрет concatenated gzip; ограниченные retained buffers и incomplete-флаг |
| Deadline/cancellation | Медленный chunked drip не обходит общий deadline, пользовательский CancelledError закрывает поток, не выдаётся успех |

Интеграционные проверки используют собственный локальный сервер и CA, созданный trustme:

1. Настоящий TLS handshake с правильным hostname, SNI и исходным HTTP Host - IPv4 и IPv6.
2. Неверный hostname сертификата - IPv4 и IPv6: certificate failure, **HTTP GET не отправляется**.
3. Недоверенная CA - IPv4 и IPv6: certificate failure, **HTTP GET не отправляется**.
4. Умышленно неверный connector приводит к настоящему TCP-сокету с иным peer IP:
   TARGET_DNS_REBINDING, сервер получает EOF без HTTP-байтов.
5. Рабочая public-only политика получает DNS→127.0.0.1: локальный сервер не принимает
   соединение, HTTP-квота остаётся нулевой.
6. После успешного GET DNS меняется на private: второй запрос блокируется, даже когда
   первый сервер предлагал keep-alive. Успешных GET/HTTP-попыток остаётся один.
7. Сервер принимает реальный ClientHello и не завершает TLS handshake: внешний deadline
   закрывает уже существующий TCP-поток; сервер наблюдает EOF.
8. Реальный HTTP-сервер возвращает redirect на private: второй GET/соединение отсутствует.

Пункты 1–3 параметризованы IPv4/IPv6, поэтому суммарно интеграционных cases 11.
В положительных локальных кейсах **только внутри теста** заменяется public-IP predicate
для разрешения loopback-сервера. Сами TCP, pin/peer check, Host/SNI, SSL и hostname
verification остаются настоящими. В рабочем коде флага отключения public-only нет.
Один кейс отдельно проверяет неизменённую рабочую политику. Это не MockTransport-проверка
реального TLS и не заявление об испытании внешнего интернет-маршрута.

Команды воспроизведения:

Также выполнены: strict mypy (20 исходных файлов), Ruff, проверка lock и совместимости
32 установленных пакетов, генератор 29 артефактов с --check, CLI --help и сборка wheel.
В wheel проверено наличие точных байтов PSL с ожидаемым SHA-256 и py.typed.
Хэши исходного PDF и PDF шага 0 совпадают с зафиксированными до изменений.

```powershell
uv sync --locked
uv run --locked pytest -q
uv run --locked pytest tests/test_connection_integration.py -q
uv run --locked mypy
uv run --locked ruff check src tests tools/generate_step0_artifacts.py
uv run --locked python tools/generate_step0_artifacts.py --check
```

Политика proxy/непроверенного TLS проверяется отрицательными тестами: явный отказ,
без небезопасного продолжения. Внешние DNS/CA/TLS-инфраструктура и proxy/HTTP2/reuse
не объявляются испытанными поддерживаемыми маршрутами. Подробные ограничения:
[STEP_0_NETWORK_AND_POLICY.md](STEP_0_NETWORK_AND_POLICY.md).
