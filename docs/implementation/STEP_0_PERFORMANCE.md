# Измерения и оптимизация шага 0

01.11.2026. Python 3.12.10, Windows 11 22631, uv 0.9.7. Код до: 0fa9d78
(чистое исходное дерево с уже существующим runner). После: пакет 0.3.0, исправления приёмки.
Инструмент tools/benchmark_step0.py, по 3 повтора каждого сценария; полные samples:
[до](step0-benchmark-before.json), [после](step0-benchmark-after.json).
Первичные замеры выполнены до оптимизаций. Для окончательного сравнения baseline повторён
из git archive 0fa9d78 с тем же инструментом через отдельный PYTHONPATH; рабочий код не откатывался.
Runtime-зависимости одинаковы; обновление pytest не участвует в runtime benchmark.

## Методика

`python -m tools.benchmark_step0 --output FILE --repeats 3` запускает настоящий runner,
HTTP Core parsing, классификацию, hashing, private run directory, fsync/atomic publication.
TCP/DNS контролируемые, внешней сети нет. DNS занимает 0.002 виртуальной секунды,
соединение 0.005; sleep/pacing/retry продвигают VirtualTime без настоящего ожидания.
HTTPS handshake в этом benchmark — fixture; реальные TLS проверки выполнены отдельно.

Wall time — perf_counter, без удаления временного каталога; peak — tracemalloc внутри
измеряемого прохода. Это Python allocations, не RSS, не память SSL/ядра и не весь процесс.
Fixture body создаётся до измерения. После тёплого PSL cache его ранее выделенная память
не входит в per-run tracemalloc peak. Поэтому memory small-case нельзя читать как RSS.
Первый sample HTTPS включает cold PSL; cold samples сохранены отдельно от медиан.

Счётчики фиксируют DNS/connect (HTTP attempts), вызовы SHA-256/file_digest и объём данных,
переданных на hashing, число classifications/входных bytes, SSLContext creation.
`psl_loads` считает запросы загрузчика, а не промахи cache. `retained_body_bytes` считает
различные encoded/entity bytes-объекты в HttpAttempt, `duplicate_identity_bytes` — вторую
тождественную копию. Это не суммарное число всех malloc/copy в Python/OpenSSL.

## Медианы до/после

| Сценарий | Wall ms до → после | Python peak MiB до → после | DNS до → после | HTTP до → после | Виртуальное время, s |
|---|---:|---:|---:|---:|---:|
| Обычный HTTPS HTML | 3046.876 → 650.119 | 3.055 → 0.348 | 1 → 1 | 1 → 1 | 0.007 |
| Три redirect | 4474.113 → 1011.228 | 3.055 → 0.430 | 7 → 4 | 4 → 4 | 3.007 |
| TLS certificate failure → HTTP | 3433.061 → 682.781 | 3.055 → 0.374 | 2 → 2 | 2 → 2 | 1.007 |
| HTML 5,242,800 bytes, около 5 MiB cap | 3624.337 → 1232.091 | 36.680 → 25.351 | 1 → 1 | 1 → 1 | 0.007 |
| 503 → 429 → HTML | 3887.830 → 865.078 | 3.055 → 0.413 | 3 → 3 | 3 → 3 | 3.021 |

В этой серии медианы улучшились примерно в 2.9–5.0 раза; точные wall значения зависят
от диска/антивируса/нагрузки и не являются SLA. Основной выигрыш относится к повторным
проходам в одном процессе. Первый cold HTTPS: 3648.764 → 3401.732 ms, peak 4.177 MiB
в обоих случаях; существенный cold-start выигрыш не заявляется.

| Дополнительная работа | До → после |
|---|---|
| Identity body bytes, обычный HTML | 124 → 62; duplicate 62 → 0 |
| Identity body bytes, около лимита | 10,485,600 → 5,242,800; duplicate 5,242,800 → 0 |
| TLS contexts, redirects / fallback / retries | 4/2/3 → 1/1/1 |
| SHA calls, HTML / redirects / fallback / near-limit / retries | 13/31/15/13/25 → 11/29/13/11/23 |
| Hashed bytes, обычный HTML | 679,953 → 4,119 (основная разница — повторные hashes PSL) |
| Hashed bytes, near-limit | 32,136,411 → 31,460,577 |
| Classifications, пять сценариев | 1/4/2/1/3 → 1/4/2/1/3; body bytes неизменны |

## Подтверждённые издержки и компромиссы

1. Повторный PSL parse/IDNA и два hash снимка на каждый новый client. Добавлен cache
   одного immutable package snapshot на процесс. PSL не загружается по сети; изменение
   поставляемого снимка требует нового процесса. DNS cache не добавлен.
2. Default SSLContext создавался для каждой попытки. Один context на клиент, hostname/
   CERT_REQUIRED проверяются также непосредственно перед TLS. Новые pool/socket по-прежнему
   на каждый GET; cookies и непроверенное keepalive не переиспользуются.
3. Каждый разрешённый redirect делал DNS precheck и затем ещё один resolution перед connect.
   Для follow/www проверка теперь делается только непосредственно перед соединением;
   внешний proposal по-прежнему проверяется без GET. Отказанное назначение не подключается.
4. Identity с одинаковыми encoded/decoded bounds держал два bytearray и два bytes с одним
   содержимым. Теперь общий bounded buffer и один immutable bytes. Раздельные representations
   и отдельные файлы доказательств сохранены. При различных bounds/сжатии буферы раздельные.
5. Runner хранит текстовую assessment только последнего кандидата; предыдущие доказательства
   уже на диске. History/trace и ограничения не изменены.

Полная потоковая запись/хэширование вместо bounded buffering потребовала бы изменения
observer/FetchTrace. Она не добавлена: текущие измерения подтвердили более простое уменьшение
копий; исходные байты и готовый ответ передаются без повторного GET. Повторный hash при publish
сохранён намеренно: обнаруживает подмену до site commit. Повторная проверка/декодирование
при загрузке недоверенного каталога также сохранены. Config validation мала по сравнению
с PSL/I/O; её не убирали. Runtime dependencies не расширены ради ускорения.

HTTP count, виртуальные delays, pacing/Retry-After и квоты одинаковы. Защита назначения/TLS
не ослаблена. Полный suite и реальные TLS/subprocess handoff проверки выполнены после
изменений (см. STEP_0_VALIDATION.md). Step1Input и смысл статусов не изменялись оптимизацией.