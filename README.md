# GEO Audit — шаг 0

Python 3.12+, пакет 0.3.1. Проверяет URL, получает ограниченный HTML, сохраняет
доказательства и готовит вход будущего шага 1. Работают HTTPS-first, явный HTTP,
ограниченный fallback, ручные редиректы, проверка назначения фактического соединения,
TLS, бюджеты и классификация содержимого.

Обход шага 1, robots.txt, браузер, генерация вопросов и платные API не реализуются.
`can_enter_step1` не означает разрешение robots.

## Установка и запуск в Windows PowerShell

```powershell
Set-Location C:\GEO
python -m pip install --user uv==0.9.7
uv sync --locked --python 3.12
uv run --locked geo-audit --help
uv run --locked geo-audit step0 'https://example.com/ru/?city=msk' --output-dir output/runs
uv run --locked geo-audit step0 'example.com' --no-http-fallback
uv run --locked geo-audit step0 'example.com' --allow-host other.example --http-fallback
```

В этой рабочей папке uv также доступен как `.\.bootstrap\Scripts\uv.exe`.
Для воспроизведения не обновляйте `uv.lock`. Сетевой запуск выполняйте для выбранного
вами публичного сайта. Команды выше — примеры, а не результаты живого аудита.
Тесты используют контролируемые фикстуры и серверы.

### Ручная проверка нескольких сайтов

```powershell
Set-Location C:\GEO
.\.venv\Scripts\python.exe -X utf8 -m geo_audit try-step0
```

В строке `Сайт>` вводите публичные URL по очереди. Для каждого адреса создаётся
отдельный запуск и новый бюджет. Выводятся status, пригодность для шага 1, HTTP/TLS,
тип содержимого, число попыток, время, причины и каталог со всеми доказательствами.
Пустая строка или `exit` завершает режим; Ctrl+C отменяет текущую проверку.
Полные URL/query в сводке не печатаются. Это CLI-режим, библиотека не запрашивает ввод.

Поддерживаются те же `--config`, `--output-dir`, `--allow-host` и
`--no-http-fallback`, что у `step0`. Например:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m geo_audit try-step0 --no-http-fallback
```

Живые результаты остаются в игнорируемой Git папке `output/runs/`.

Конфигурацию можно сохранить через `config-defaults` и передать `--config FILE`.
Сохранение UTF-8 без зависимости от кодировки перенаправления PowerShell:

```powershell
$cfg = uv run --locked geo-audit config-defaults
[IO.File]::WriteAllText((Join-Path $PWD 'step0-config.json'), ($cfg -join "`n"), [Text.UTF8Encoding]::new($false))
uv run --locked geo-audit step0 'example.com' --config step0-config.json
uv run --locked geo-audit load-step1 'output/runs/run-REPLACE_WITH_ACTUAL_ID'
```

`--allow-host` задаёт точный хост, допускается несколько параметров. Он не разрешает
private IP, все поддомены или дополнительные порты. Порты и `path_scope` задаются
в конфигурации. `--parent-run-id` связывает новый запуск с предыдущим, без скрытого
продления бюджета. `needs_input` возвращает конкретный `--allow-host`, без ожидания ввода.

## Выходы и exit codes

```text
run-<uuid>/
  config.json          # полная конфигурация
  budget.json          # время и счётчики на момент завершения
  journal.json         # завершённые наблюдения; running/completed/cancelled
  raw/manifest.json    # evidence_id, SHA-256, размер, представление, полнота
  raw/<uuid>.bin       # тела и структурированные наблюдения
  site.json            # итог; публикуется последним
```

`content_encoded` — тело до content decoding, после удаления HTTP transfer framing;
`entity_bytes` — распакованное содержимое; `decoded_text` — текст в UTF-8 с записанной
исходной/применённой кодировкой. Это не захват TLS/HTTP wire-трафика. При identity
байты совпадают, но происхождение представлений остаётся явным.

Синтетический stdout CLI (полный контракт: [site-ok.json](examples/contracts/site-ok.json)):

```json
{"status":"ok","can_enter_step1":true,"effective_origin":"https://example.com","reason_codes":[],"next_action":"continue_step1","run_id":"run-<uuid>","run_directory":"C:\\GEO\\output\\runs\\run-<uuid>"}
```

| Код | Значение |
|---|---|
| 0 | ok или успешная локальная проверка |
| 1 | failed при проверке сайта |
| 2 | Ошибка аргументов, конфигурации или входного артефакта |
| 3 | partial: пригодность определяется отдельно по can_enter_step1 |
| 4 | needs_input |
| 5 | OUTPUT_WRITE_FAILED |
| 70 | Программная ошибка; библиотека сохраняет исходное исключение |
| 130 | Отмена / KeyboardInterrupt |

200, короткий текст и успешный TLS по отдельности не подтверждают пригодность HTML.
Полные URL/query и тела находятся в приватном локальном каталоге; консоль показывает origin.

## Публичный API и вход шага 1

```python
from pathlib import Path
from geo_audit import (
    Budget, SafeHttpClient, Step0Config, TimeServices,
    load_step1_input, prepare_step1, validate_site,
)

async def inspect(url: str):
    config = Step0Config()
    timing = TimeServices()
    budget = Budget(config, timing=timing)
    client = SafeHttpClient(config, timing=timing)
    site = await validate_site(url, config, client, budget,
                              timing=timing, output_dir=Path('output/runs'))
    if site.can_enter_step1:
        ready = prepare_step1(site, budget=budget)
        assert ready.budget is budget
        # ready.fetch_result — готовый FetchResult; ready.contract — Step1Input.
        # ready.html — распакованные исходные HTML bytes, без повторного GET.
    return site

# В другом процессе, пока общий бюджет ещё действует:
ready = load_step1_input(Path('output/runs/run-REPLACE_WITH_ACTUAL_ID'))
html = ready.html
text = ready.fetch_result.html_text
scope = ready.contract.site.allowed_hosts, ready.contract.site.path_scope
remaining = ready.budget.remaining_seconds(step=1)
assert ready.contract.crawl_permission == 'unknown'
```

`validate_site(...) -> SiteValidationResult` принимает клиент (`Step0Client`), конфигурацию,
Budget, TimeServices и EvidenceStore. Библиотека не вызывает `input()`.
`load_site_result` проверяет любой конечный статус; `load_step1_input` принимает только
пригодный вход и возвращает `Step1Handoff(contract, fetch_result, budget)`.
Полный пример: [step1-input.json](examples/contracts/step1-input.json).

Версии: schema `1.2.0`, algorithm `step0-1.2`, config `step0-config-1.2.0`.
Согласованные 1.0/1.1 контракты читаются для истории; неполный старый каталог
не превращается в новый вход. Учебная схема `1.0` из PDF не является программной схемой.

В процессе время монотонное. Между процессами сохраняются UTC deadline, накопленное
время и счётчики. Восстановленный расход — максимум сохранённого расхода и времени UTC
от начала; последующее монотонное время прибавляется к нему. Загрузка учитывает простой,
не обнуляет квоты. Просрочка или UTC ранее captured_at дают INPUT_ARTIFACT_INVALID:
нужен явно новый запуск. Требуются корректные часы ОС.

## Проверки

```powershell
New-Item -ItemType Directory -Force tmp | Out-Null
uv lock --check
uv pip check
uv run --locked pytest -q -ra
uv run --locked mypy
uv run --locked ruff check src tests tools
uv run --locked python tools/generate_step0_artifacts.py --check
uv run --locked bandit -r src tools -f json -o tmp/bandit.json
uv export --locked --no-emit-project --format requirements-txt --output-file tmp/audit-requirements.txt --quiet
uv run --locked pip-audit --require-hashes -r tmp/audit-requirements.txt
uv run --locked python -m tools.benchmark_step0 --output tmp/step0-benchmark.json --repeats 3
uv build --out-dir tmp/package
uv run --locked python tools/check_distribution.py tmp/package
```

pip-audit требует доступа к базе уязвимостей. Недоступность сервиса не означает успех.
`validate` проверяет контракт; `load-step1` дополнительно проверяет файлы/хэши и свежесть.
Синтетические примеры имеют фиксированное время и не являются действующим handoff.

Для независимого воспроизведения используйте новый clone и `uv --no-cache sync --locked
--python 3.12`, затем те же команды. Приложение и тесты не требуют `.bootstrap`, старого
venv, локальных PDF-рендеров или benchmark JSON. Эти материалы игнорируются Git.
Wheel содержит только пакет/PSL/py.typed и метаданные; sdist — runtime source, README,
pyproject/lock и метаданные, без tests/docs/PDF/audit data. Полный checkout нужен для тестов.

Три PDF-инструмента в tools сохранены для происхождения документов; Ruff/compileall/Bandit
проверяют и их. Их повторный запуск требует отдельного документального окружения
(ReportLab, pdfplumber, pypdf, Pillow), Windows fonts и для QA — подготовленных рендеров.
Это исторический workflow подготовки PDF, не зависимость и не команда приёмки шага 0.

`.github/workflows/step0.yml` повторяет приёмку на Windows 2025 и Ubuntu 24.04.
Отдельный gate требует фактического выполнения file-symlink теста без skip. У workflow
read-only token, actions закреплены SHA, caches отключены, время каждой job ограничено.
Наличие workflow не означает успешного CI: фактические результаты фиксируются в отчёте приёмки.

Документы: [архитектура](docs/implementation/STEP_0_ARCHITECTURE.md),
[матрица требований](docs/implementation/STEP_0_REQUIREMENTS.md),
[проверки](docs/implementation/STEP_0_VALIDATION.md),
[security review](docs/implementation/STEP_0_SECURITY_REVIEW.md),
[измерения](docs/implementation/STEP_0_PERFORMANCE.md).

Ограничения: прямое соединение без proxy/keepalive/HTTP2; первый проверенный IP;
identity/gzip/zlib-deflate; эвристики без исполнения JS; ограниченные тела в памяти.
SHA-256 подтверждает целостность, но не подлинность против владельца, способного
переписать каталог и хэши. Windows проверен фактически; POSIX требует отдельной
интеграционной приёмки. Живые проверки шага 0 выполнены 01.11.2026: example.com
по HTTP/HTTPS и www.python.org дали ok; expired.badssl.com при отключённом fallback
дал ожидаемую ошибку сертификата. Эти результаты являются наблюдениями одного запуска,
а не обещанием доступности тех же сайтов в другой момент или среде.
Исходные PDF, алгоритмы и полезные инструменты документов сохранены.
