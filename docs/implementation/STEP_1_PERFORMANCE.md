# Step1: расходы и чистота, 01.11.2026

**Полный performance gate не пройден:** завершённого step1 нет. Не измерены
30 загруженных/extracted страниц и сравнимый baseline всего pipeline. Нет оснований
объявлять оптимизацию или экономию. Конкурентность и новый HTTP-клиент не добавлены.

## Измеренное discovery, не полный crawler

Windows, Python3.12.10, локальные синтетические HTTP stream fixtures;3 прогона/сценарий.
perf_counter — wall time, tracemalloc — Python peak allocations (не RSS), fake clock
только для Budget/pacing. Замер после создания step0+Step1Runtime, включает discover,
финальную валидацию и commit publication. Shared step0 attempt включён в attempts.
Вход одинаков внутри трёх повторов; order/DTO/counters проверяют отдельные тесты.
Во время измерений шла регрессия, поэтому значения ориентировочные, без выводов
о throughput и без сравнения разных реализаций. Samples — ignored tmp, не git.

| Fixture | Median ms | Peak MiB | Attempts 0–1 | Discovered | Selected |
|---|---:|---:|---:|---:|---:|
| one-page, maps off |207.95|16.148|1|1|1|
| 30 URLs, только discovery |318.41|16.114|1|30|1|
| map5000 entries |30966.81|34.039|3|5000|1|
| query trap5001 links |17055.75|21.914|1|5000|1|
| robots503 ×3 |573.00|16.184|4|1|1|
| Retry-After999 |773.20|16.219|3|1|1|

Все итоги partial/SKELETON_ONLY, source read-only, один selected entry, no extraction.
Query trap имеет bounded память, но ещё **не** проверяет требуемые2 variants/path.
30 URLs нельзя переименовать в30 fetched pages. Для финального сравнения: isolated
machine, no concurrent tests, fresh processes, одинаковые fixtures/lock/config,
≥3 samples baseline/fix; сверить median/peak/attempts/selection/semantic result и
hash/provenance, используя perf_counter и tracemalloc одинаково. Baseline9f231de
не имеет discover API, сравнение его skeleton с новой функциональностью не доказывает
выигрыш. Оптимизация отложена до готового pipeline и содержательного baseline.

## Архитектура и инвентаризация

Один SafeHttpClient/Budget/normalizer/secure_io; source entry переиспользован,
robots cached per origin/run; sitemap visited и discovery memo исключают повторный GET.
CPU parser вызывается один раз на bounded fetched map. Результат заново не парсит
веб-источник при offline reload. Проверка Pydantic при publish/load — integrity gate,
а не ненужная повторная extraction. Обход/экстракторы отсутствуют, поэтому их расходы
и dead code ревью не объявляются выполненными. Config crawler/probes/browser —
заявленный будущий контракт; удаление сейчас сломало бы схему, не обосновано.

До добавления новых отчётов/тестов: git ls-files143; runtime37, tests14, tools7,
docs20, schemas27, examples27, output3; прочее — CI/config/lock. Отдельно untracked7
новых файлов текущего sitemap, ignored22883 файлов (venv/bootstrap/cache/tmp/локальные
outputs). Неизвестные ignored/untracked документы не удалялись. Инвентарь не является
указанием удалять все локальные файлы. PSL+происхождение, PDF/промпты/алгоритмы,
схемы/синтетика, тесты и инструменты нужны; tracked удалений нет. Удалять доказанный
dead code не потребовалось. Собственные вспомогательные scripts tmp удалены после
проверок; scanner logs/samples остаются ignored, как запрошено.

Tracked secret-pattern scan (private PEM, GitHub/AWS/OpenAI key формы) дал0 файлов;
tracked .log/cache/run data —0. Содержимое потенциальных секретов не печаталось.
Это эвристический scan, не история и не доказательство отсутствия любых секретов.
История не переписывалась, git rm --cached не понадобился, пользовательские файлы
не удалялись. .gitignore уже покрывает tmp, venv/cache и output/runs.

Build policy прежняя: wheel runtime+PSL/py.typed/metadata; sdist runtime+build config,
README/lock/.python-version. `uv build --out-dir tmp/step1-package` и
`python tools/check_distribution.py tmp/step1-package`: wheel43, sdist45 файлов,
без tests/docs/PDF/audit outputs/venv. Synthetic schemas остаются в checkout,
runtime model generation работает из wheel. Fresh-install проверки — VALIDATION.

Временные test environments/архивы остаются в ignored tmp: автоматическая проверка
отклонила рекурсивную очистку тестовой копии (`blocked by policy`). Удалены только
свои четыре небольших вспомогательных script через адресный patch. Пользовательские
файлы не удалялись, tracked cleanup не требуется. Это ограничение локальной очистки,
не содержимого Git/wheel. Агрегаты выше получены до исправления S3 config fingerprint;
защитные значения/алгоритм parser сохранились, повторный performance выигрыш не заявлен.
