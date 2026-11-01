# GEO Audit

Технический аудит сайта и рекомендации GEO без платных API и LLM. Python 3.12+.
Проверяет доступность, TLS, robots, sitemap, внутренние страницы, директивы
индексации и сниппетов; извлекает проверяемый текст и языковые/географические сигналы.
Рекомендации основаны на наблюдениях и evidence. Видимость в ответах ИИ не измеряется,
профиль компании и следующий аналитический шаг не реализованы.

## Установка и запуск

Установите Python 3.12 и [uv](https://docs.astral.sh/uv/getting-started/installation/),
затем из каталога клонированного репозитория:

```sh
uv sync --locked --python 3.12
uv run --locked geo-audit --help
uv run --locked geo-audit serve
uv run --locked geo-audit audit https://example.com
```

`serve` открывает локальный сервер: http://127.0.0.1:8765 — введите сайт в браузере.
Одновременно выполняется один анализ; интерфейс показывает охват, наблюдения,
географические/языковые сигналы и рекомендации. Сервер доступен только локально.
`audit` обращается к указанному сайту из терминала. Результаты по умолчанию сохраняются
в игнорируемом `private/runs/`. Сводка JSON содержит статус, охват, ограничения,
каталог публикации и технические рекомендации. Исходники шага 0 сохраняются отдельно;
шаг 1 их не переписывает. Не публикуйте результаты аудита: URL и исходники сайта
могут содержать чувствительные данные. Папку вывода можно задать через `--output-dir`.

```sh
uv run --locked geo-audit audit https://example.com --description-file description.txt
uv run --locked geo-audit step1 private/runs/RUN_ID
uv run --locked geo-audit recommend private/runs/PUBLICATION_ID
uv run --locked geo-audit load-step2 private/runs/PUBLICATION_ID
```

`step1` принимает свежий проверенный результат шага 0; общий таймер и расход попыток
не обнуляются. Для истёкшего результата нужен новый явный `audit`.
`recommend` работает офлайн с сохранённым шагом 0 или 1, проверяя evidence.
`load-step2` проверяет сохранённый корпус без DNS/HTTP и повторной экстракции.
Его отдельная политика свежести — 7 дней от начала исходного наблюдения, а не 300 секунд
сетевого бюджета. Технические ошибки не исчезают после добавления описания пользователя.

Старые команды шага 0 сохранены; точный синтаксис доступен через `--help`.
Для `audit`: `--step0-config` задаёт JSON `Step0Config`, `--config` — `Step1Config`.
`step1` также принимает `--config`. Поле `source_step0_config_sha256` обязательно
совпадает с fingerprint конфигурации шага 0; это другой hash, чем конфигурация шага 1.
Схемы и синтетические примеры: [schemas](schemas), [examples/contracts](examples/contracts).
Они не являются действующими сетевыми handoff.

Коды новых команд: 0 — ok; 1 — failed; 2 — некорректный ввод; 3 — partial;
4 — needs_input; 5 — ошибка сохранения; 70 — программная ошибка; 130 — отмена.
При отказе диска наличие полноценной JSON-публикации не гарантируется.

## Границы и безопасность

Статический обход последовательный: максимум 30 выбранных страниц, 5000 кандидатов,
2 query-варианта на путь. Общий бюджет шагов 0–1: 300 секунд / 150 попыток;
собственный предел шага 0: 60 секунд / 15 попыток. Redirect/retry расходуют попытки,
cache hit — нет. Pacing, Retry-After и robots соблюдаются до каждого HTML redirect.
Один клиент проверяет публичное назначение, закрепляет IP и проверяет TLS/peer.
Sitemap/CDN/canonical не расширяют разрешение на внешний HTML.

Robots: 512 KiB decoded, неизвестность не считается разрешением.
Sitemap: до 5 файлов, 5 MiB на слой/файл, 5000 URL, глубина 3;
DTD/entities и XML-сеть запрещены. Evidence ограничены 64 MiB/файл и 256 MiB/запуск.
Текст нормализуется NFC с определёнными Unicode offsets; цитаты проверяются по hash.
Решение о бизнес-материале — консервативная эвристика явных фраз деятельности,
а не семантическая гарантия. Служебная длина не даёт готовность корпуса.

UA probes и browser выключены по умолчанию. Включённые probes — наблюдение из нашей
среды, не доказательство ответа настоящему роботу. Google-Extended не имеет HTTP UA.
Без проверенной защиты фактического browser egress включение браузера возвращает
`unknown/SAFE_EGRESS_UNAVAILABLE`: безопасный renderer пока не поставляется.
CAPTCHA/login/robots не обходятся.

## Публичный consumer API

```python
from pathlib import Path
from geo_audit.step1.handoff import read_corpus
from geo_audit.recommendations import recommendation_report

directory = Path("private/runs/PUBLICATION_ID")
handoff, texts = read_corpus(directory)  # только сохранённые проверенные тексты
assert handoff.result.crawl.material.can_start_step2
report = recommendation_report(directory)  # офлайн; ссылки на checks/evidence
```

## Проверки разработки

```sh
uv lock --check
uv pip check
uv run --locked pytest -q -ra
uv run --locked ruff check src tests tools
uv run --locked mypy --platform win32
uv run --locked mypy --platform linux
uv run --locked python tools/generate_step0_artifacts.py --check
uv run --locked python tools/generate_step1_artifacts.py --check
uv run --locked bandit -r src tools
uv export --locked --no-emit-project --format requirements-txt --output-file tmp/audit-requirements.txt
uv run --locked pip-audit --require-hashes -r tmp/audit-requirements.txt --cache-dir tmp/audit-cache --progress-spinner off
uv build --out-dir tmp/package
uv run --locked python tools/check_distribution.py tmp/package
```

CI выполняет эти gates на Windows и Linux. Фактические результаты и ограничения:
[docs/VALIDATION.md](docs/VALIDATION.md). Бенчмарк локальных fixtures:
`uv run --locked python tools/benchmark_step1.py --help` (samples сохраняйте в `tmp/`).
Wheel/sdist содержат runtime, PSL с происхождением и метаданные; tests/docs/private
в дистрибутив не входят. Для checkout-тестов нужны tracked fixtures и dev-зависимости.

Личные документы, промпты и результаты запусков хранятся только в игнорируемом
`private/` и не нужны для установки. Удаление из текущего дерева Git не удаляет
ранее опубликованные файлы из истории репозитория.
