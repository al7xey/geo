# GEO Audit: основа шага 0

Python 3.12+, пакет 0.2.0. Реализованы контракты, схемы, чистые URL/scope/policy,
локальный полный PSL и общий `SafeHttpClient` с привязкой соединения к проверенному IP,
TLS, retries/redirects, deadline и ограниченной распаковкой. Есть общий бюджет,
локальное хранение и проверка передачи шагу 1.
**Финальная оркестрация аудита пока не реализована.** Команда `step0` и API `validate_site`
возвращают явную ошибку `IMPLEMENTATION_NOT_AVAILABLE` до любого I/O.
Это не результат проверки доступности сайта.

## Установка и проверка (PowerShell, из C:\GEO)

Установите `uv==0.9.7` в отдельное инструментальное окружение или используйте имеющийся uv.
`uv.lock` фиксирует прямые и транзитивные зависимости, источники и хэши артефактов.
Не обновляйте lock при воспроизведении:

```powershell
uv sync --locked --python 3.12
uv run --locked geo-audit --help
uv run --locked geo-audit config-defaults
uv run --locked geo-audit validate SiteValidationResult examples/contracts/site-ok.json
uv run --locked geo-audit validate Step1Input examples/contracts/step1-input.json
uv run --locked python tools/generate_step0_artifacts.py --check
uv run --locked pytest -q
uv run --locked mypy
uv run --locked ruff check src tests tools/generate_step0_artifacts.py
```

В текущей рабочей папке uv также доступен как `.bootstrap\Scripts\uv.exe`.
Окружение `.venv` можно использовать напрямую без uv:

```powershell
.\.venv\Scripts\python.exe -m geo_audit --help
.\.venv\Scripts\geo-audit.exe schema Step1Input
.\.venv\Scripts\geo-audit.exe schema --output-dir schemas
```

Импортируемый API:

```python
from geo_audit import Step0Config, SiteValidationResult
from geo_audit.urls import normalize_input

entry = normalize_input("example.com/ru/?city=msk#prices")  # без сети
config = Step0Config()
site = SiteValidationResult.model_validate_json(open("site.json", "rb").read())
```

`await validate_site(input_url, config, client, budget, ...)` - зарезервированная
точка входа будущего исполнителя. Она сейчас всегда поднимает `GeoAuditError`
с категорией `implementation`. Протоколы внедрения находятся в `network.py`.

Общий клиент вызывается явно; импорт сам не запускает запросы:

```python
from geo_audit import Budget, SafeHttpClient, Step0Config
from geo_audit.scope import build_scope
from geo_audit.urls import normalize_input

async def fetch_entry(input_url: str):
    config = Step0Config()
    entry = normalize_input(input_url)
    scope = build_scope(entry, allowed_hosts=config.allowed_hosts,
                        path_scope=config.path_scope,
                        allowed_extra_ports=config.allowed_extra_ports)
    budget = Budget(config)
    client = SafeHttpClient(config)
    trace = await client.get(input_url, scope=scope, budget=budget, allow_http_fallback=True)
    # Transport trace is not a content classification or a published site.json.
    return trace
```

Для шага 1 передаётся тот же Budget и `step=1`. По умолчанию сам общий клиент не меняет
схему через fallback; его явно включает вызывающий этап. Proxy и ослабление TLS запрещены.
Подробности защиты, проверки настоящих TCP/TLS и принятые ограничения описаны в
[актуальной записке](docs/implementation/STEP_0_NETWORK_AND_POLICY.md).

CLI: код 0 - выполнена локальная операция; 1 - доменная ошибка проверки артефакта;
2 - неверные аргументы/JSON/файл; 4 - исполнитель ещё не реализован.
`validate` проверяет контракт, но не наличие файлов. `check-handoff --run-dir DIR`
дополнительно сверяет файлы/хэши и актуальный общий дедлайн.
У учебного примера фиксированное время: позднее он закономерно не проходит проверку
свежести `check-handoff`, хотя остаётся валидным примером контракта.

Подробности: [архитектура](docs/implementation/STEP_0_ARCHITECTURE.md),
[соответствие алгоритму](docs/implementation/STEP_0_REQUIREMENTS.md),
[поля контрактов](docs/implementation/STEP_0_FIELDS.md).

`examples/contracts` содержит только синтетические фикстуры, не результаты аудита.
Исходные алгоритмы, PDF и существующие `tools/build_algorithm_pdfs.py` /
`tools/check_algorithm_pdfs.py` сохранены. Их документальные зависимости не включены
в минимальные runtime-зависимости аудитора; используйте прежнее окружение подготовки PDF.
