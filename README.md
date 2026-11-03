# GEO Audit

Аудит видимости компании в ответах ИИ-систем (GEO). Python 3.12+.

- Шаги 0–1 (`geo-audit`, без платных API): доступность, TLS, robots, sitemap, внутренние
  страницы, директивы индексации; проверяемый текст и языковые/географические сигналы.
- Шаг 2 (`tools/`, alpha, Claude API): профиль компании и 40 вопросов покупателя.
- Шаг 3: пользователь проверяет и правит вопросы на локальной странице.
- Шаг 4: вопросы уходят в ChatGPT, Gemini, Claude, DeepSeek, Perplexity с веб-поиском;
  сохраняются ответы, источники, назван ли бренд, есть ли сайт клиента в источниках, цена.

Шаги 5–8 обрабатывают сохранённые ответы без платных API: находят упоминания,
определяют роль компании, готовят независимую проверку и группируют названия и источники.
Неясные случаи ожидают проверки; итоговые проценты шага 9 ещё не реализованы.

## Быстрый запуск на Windows, macOS и Linux

Нужен установленный Python 3.12 или новее. Ubuntu, WSL и Docker не требуются.
В терминале перейдите в папку проекта. На Windows выполните:

```powershell
python run.py setup
python run.py demo
```

На macOS и Linux используйте `python3` вместо `python`:

```sh
python3 run.py setup
python3 run.py demo
```

Установка требует интернета и загружает закреплённые зависимости. Учебный запуск
обрабатывает 480 вымышленных ответов, не обращается к сайтам или нейросетям и печатает
путь к отчёту. Откройте этот файл самостоятельно. Результаты хранятся в `private/demo/`.
Повторный запуск создаёт новую папку. Это проверка работы программы, а не аудит компании.
Внешние ключи для учебного запуска не нужны.

Подробные команды: [запуск на разных системах](docs/PORTABLE_RUN.md).

## Ключи API

Шаги 0–1 ключей не требуют. Для шагов 2 и 4 скопируйте [.env.example](.env.example)
в `private/.env` и впишите свои ключи; перед запуском загрузите их:

```sh
set -a; source private/.env; set +a
```

Платные шаги расходуют деньги на ваших аккаунтах: начинайте с `--dry-run` (план и оценка).

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

## Шаг 2: профиль компании и 40 запросов (tools/, alpha)

Нужны ключ `ANTHROPIC_API_KEY` и закрытый промт в `private/prompts/prompt_step2_profile_and_queries.md`
(в репозиторий не входит). Шаги 0→1→2 одной командой:

```sh
uv run --locked --no-dev python tools/geo_pipeline.py https://example.ru --country Россия
```

Запросы всегда на русском; рынок — `--country`, по домену (.ru → Россия) или Россия по умолчанию.
Параллельно с шагами 0–1 идёт веб-поиск о компании (Haiku 5.5, ~$0.03; `--no-web` отключает).
Если на сайте мало текста или он закрыт для обхода (антибот, 403), нужно описание компании:
`--description "…"` или `--description-file F`; для закрытого сайта шаг 2 строится только по нему.
`--preset quality|balanced|fast|cheap` выбирает модели (по умолчанию quality: профиль и запросы —
Sonnet 5.5, запросы по 4 уровням параллельно, около $0.15–0.25 за сайт; balanced и fast пишут
профиль на Haiku 5.5 — дешевле, но в слепом сравнении проиграли). `--pages N` ограничивает число
читаемых страниц шага 1 (по умолчанию 20). Результат: `private/step2_fast/<публикация>/` —
`profile.json`, `prompts.json`, `step2_report.json` (время, токены и стоимость каждого вызова).
Сравнение двух результатов со слепым листом оценки: `tools/step2_compare.py A B`.
Офлайн-тесты: `tests/test_step2_tools.py`.

## Шаг 3: проверка вопросов пользователем (tools/, alpha)

```sh
uv run --locked --no-dev python tools/step3_review.py private/step2_fast/<папка шага 2>
```

Открывает локальную страницу http://127.0.0.1:8766: варианты названия, категория и 40 вопросов
можно править, можно добавить до 10 своих. «Проверить» показывает ошибки у каждого вопроса
(пустой, не указан вид, длина вне диапазона вида, почти дубль, у брендовой позиции нет названия);
вопрос с названием компании сохраняется, но не входит в расчёт видимости — страница поясняет это.
«Сохранить» пишет в `private/step3/<папка>/` `prompts.json` (метки suggested/edited/custom),
`profile.json` и `step4_input.json` — вход шага 4 с контрольной суммой вопросов.
Офлайн-тесты: `tests/test_step3_review.py`.

## Шаги 5-8: обработка собранных ответов

Шаги 0-4 перенесены из предоставленного архива без изменений кода. Шаги 5-8
адаптированы к его файлам. Полный порядок работы и единая команда запуска:
[docs/INTEGRATION_0_8.md](docs/INTEGRATION_0_8.md).

Если ответы уже собраны, новые запросы и оплата не нужны:

```powershell
uv run --locked python tools/steps0_8.py analyze --step4-input private/step3/PROJECT/step4_input.json --answers-dir private/step4/PROJECT --output-dir private/complete/PROJECT
```

Откройте `private/complete/PROJECT/report.html`. Папка результата должна быть новой.
Для проверки результата:

```powershell
uv run --locked python -m geo_audit.step5.workflow --verify private/complete/PROJECT
```

Профиль по умолчанию читается рядом с `step4_input.json`; другой утверждённый файл
можно передать через `--profile`. Повторы сбора не учитываются дважды. Брендовые
вопросы и исключённые сборщиком системы не возвращаются в измерение видимости.
Неопределённые случаи сохраняются для проверки; обращения к моделям на шагах
5-8 пока отключены. Итоговые проценты видимости относятся к шагу 9.

## Шаг 4: ответы ИИ-систем (tools/, alpha)

```sh
uv run --locked --no-dev python tools/step4_collect.py private/step3/<папка>/step4_input.json --dry-run
uv run --locked --no-dev python tools/step4_collect.py private/step3/<папка>/step4_input.json \
    --systems chatgpt gemini claude deepseek --max-cost-per-system 20 --max-cost 30
```

`--dry-run` проверяет вход и показывает число запросов и оценку стоимости, ничего не отправляя.
Без `--max-cost` или `--max-cost-per-system` платный запуск не начнётся.

| Система | Модель | Поиск |
|---|---|---|
| ChatGPT | gpt-6-sol, без размышлений | web_search, не больше 3/3/5/6 вызовов по уровню вопроса |
| Gemini | gemini-3.5-flash-lite через Vertex AI | Grounding with Google Search |
| Claude | Haiku 5.5 | web_search, не больше 3/3/5/6 |
| DeepSeek | deepseek-flash | поиск DeepSeek, цикл ведёт наш код, не больше 3/3/5/6 |
| Perplexity | sonar | web_search Agent API (на реальном API ещё не проверен) |

Каждый вопрос уходит без истории и служебной инструкции. Повторы адаптивные: 3 ответа; если
хоть один отличается (назван бренд или нет, есть ли сайт клиента в источниках) — до 5.
`--repeats N` задаёт ровно N. При лимите частоты (429) запрос повторяется с паузой;
`--retry-errors` дозапрашивает только упавшие ответы. Результат в `private/step4/<папка>/`:
`answers.jsonl` (текст, источники, токены, цена, время), `raw/` (сжатые ответы API с SHA-256),
`step4_report.json` (сводка по системам). Ориентир на сайт из 41 вопроса: ChatGPT ~$14,
Gemini ~$6.5, Claude ~$5, DeepSeek ~$2; около 20 минут, дольше всех ChatGPT.
Офлайн-тесты на локальном фейковом API: `tests/test_step4_collect.py`.

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
