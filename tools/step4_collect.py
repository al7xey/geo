# ruff: noqa: E501
"""Шаг 4: сбор ответов ИИ-систем на вопросы шага 3.

  uv run --locked --no-dev python tools/step4_collect.py private/step3/<папка>/step4_input.json --dry-run
  uv run --locked --no-dev python tools/step4_collect.py <step4_input.json> --max-cost-per-system 35

Каждый вопрос уходит без истории и без служебной инструкции, с веб-поиском и страной
(где API это позволяет). Модели — по уровню вопроса (ROUTES). На пару «вопрос × система»
по умолчанию адаптивно (решение пользователя 02.11.2026): 3 повтора; если хоть один
отличается — назвали компанию или нет, есть ли сайт клиента в источниках, — добираем до 5. --repeats N — ровно N повторов без надстройки.
Каждый ответ сохраняется дважды: сжатый ответ API с SHA-256 в raw/ и строка в answers.jsonl.
Сеть/лимит — до 3 повторов с паузой; отказ API — ошибка без повтора; пустой ответ — 1 повтор.
Система без ответа больше чем на 20 % запросов исключается из сравнения.
Ключи: OPENAI_API_KEY, VERTEX_API_KEY (Gemini через Vertex AI), ANTHROPIC_API_KEY,
PERPLEXITY_API_KEY, DEEPSEEK_API_KEY (private/.env).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from step2_checks import brand_patterns  # noqa: E402
from step4_handoff import load_step4_input, plan  # noqa: E402

# Модели по уровню вопроса — решение пользователя 02.11.2026 (вторая редакция: дешевле и быстрее).
# Цены: $/млн токенов (вход, выход), поиск: $ за один поиск. Проверено по документации 02.11.2026.
ROUTES = {
    "chatgpt": {lvl: ("gpt-6-sol", "none") for lvl in (1, 2, 3, 4)},
    "gemini": {lvl: ("gemini-3.5-flash-lite", None) for lvl in (1, 2, 3, 4)},
    "claude": {lvl: ("claude-haiku-5-5", "medium") for lvl in (1, 2, 3, 4)},
    # 03.11.2026: дешёвые системы. Perplexity — Agent API с web_search; у DeepSeek поиска в чате
    # нет, поэтому цикл «модель просит поиск → /v1/tools/search → ответ» ведёт наш код.
    "perplexity": {lvl: ("perplexity/sonar", None) for lvl in (1, 2, 3, 4)},
    "deepseek": {lvl: ("deepseek-flash", None) for lvl in (1, 2, 3, 4)},
}
# Сколько веб-поисков разрешено на ответ по уровню. OpenAI — max_tool_calls (все встроенные
# инструменты, лишние вызовы игнорируются), Claude — max_uses, DeepSeek — наш цикл; у Gemini и
# Perplexity такой настройки нет.
MAX_SEARCHES = {1: 3, 2: 3, 3: 5, 4: 6}
PRICES = {
    "gpt-6-sol": (2.0, 10.0),
    "gemini-3.5-flash-lite": (0.30, 2.50),  # цена AI Studio; цену Vertex сверить в Billing
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "perplexity/sonar": (0.25, 2.50),
    "deepseek-flash": (0.30, 1.20),  # дневной тариф; ночью вдвое дешевле — считаем по верхнему
}
SEARCH_PRICE = {
    "chatgpt": 10 / 1000,
    "gemini": 14 / 1000,
    "claude": 10 / 1000,
    "perplexity": 2.5 / 1000,  # search_type "web"; "fast" — $1, но это уже не обычный Sonar
    "deepseek": 0.75 / 1000,
}
# Оценка одного ответа для плана: (вход с найденным, выход, поисков) по уровню 1–2 / 3 / 4.
# ChatGPT и Claude — проба на gogetlinks.net 02.11.2026 (по ответу на уровень, gpt-6-sol none,
# Haiku, лимиты поиска), Gemini — проба через Vertex AI 03.11.2026.
ESTIMATE = {
    "chatgpt": [(21000, 770, 2.5), (38000, 1150, 6), (42000, 2300, 5)],
    "gemini": [(60, 1300, 2), (100, 1050, 2), (180, 1520, 2)],
    "claude": [(20300, 2430, 2), (21500, 2300, 2), (68000, 7900, 5)],
    "perplexity": [(4000, 800, 2), (5000, 1100, 2), (6000, 1500, 3)],  # допущение до пробы
    "deepseek": [(10300, 1340, 2), (30800, 1820, 4), (35900, 2760, 4)],  # проба 03.11.2026
}
# Адаптивные повторы — решение пользователя 02.11.2026: 3 ответа; если хоть один отличается
# (назвали компанию или нет, есть ли сайт клиента в источниках) — добираем до 5.
FIRST, MAX_REPEATS = 3, 5
RETRIES = 6  # повторов при сбое сети или лимите частоты (429): OpenAI быстро упирается в лимит
CONCURRENCY = {
    "chatgpt": 6,
    "gemini": 6,
    "claude": 10,
    "perplexity": 6,
    "deepseek": 6,
}  # одновременных запросов к системе
COUNTRY_ISO = {"Россия": "RU", "США": "US", "Казахстан": "KZ", "Беларусь": "BY", "Украина": "UA"}
KEYS = {
    "chatgpt": "OPENAI_API_KEY",
    "gemini": "VERTEX_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
BASE = {  # переопределяются переменными окружения только в тестах
    "chatgpt": os.environ.get("GEO_OPENAI_BASE", "https://api.openai.com"),
    "gemini": os.environ.get("GEO_VERTEX_BASE", "https://aiplatform.googleapis.com"),
    "claude": os.environ.get("GEO_ANTHROPIC_BASE", "https://api.anthropic.com"),
    "perplexity": os.environ.get("GEO_PERPLEXITY_BASE", "https://api.perplexity.ai"),
    "deepseek": os.environ.get("GEO_DEEPSEEK_BASE", "https://api.deepseek.com"),
}
# Инструмент поиска для DeepSeek: описание — единственный служебный текст в его запросе.
DEEPSEEK_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web. Returns results with title, url, summary and date.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}
DEEPSEEK_RESULTS = 5  # результатов на один поиск


class Rejected(Exception):
    """API отклонил запрос (4xx кроме 408/429): повтор бессмысленен."""


# ---------- запрос и разбор по системам ----------


def request(system, model, effort, text, country, level=1):
    iso = COUNTRY_ISO.get(country)
    if system == "chatgpt":
        tool = {
            "type": "web_search",
            "search_context_size": "low",  # 96 % цены — текст найденных страниц во входе
            "user_location": {"type": "approximate", **({"country": iso} if iso else {})},
        }
        body = {
            "model": model,
            "input": text,
            "tools": [tool],
            "include": ["web_search_call.action.sources"],
            "max_tool_calls": MAX_SEARCHES[level],
        }
        if effort:
            body["reasoning"] = {"effort": effort}
        return (
            f"{BASE[system]}/v1/responses",
            {"Authorization": f"Bearer {os.environ[KEYS[system]]}"},
            body,
        )
    if system == "gemini":
        # Vertex AI (AI Studio недоступен пользователю): generateContent + Grounding with
        # Google Search; страны у инструмента нет, рынок задаёт только текст вопроса.
        body = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "tools": [{"googleSearch": {}}],
        }
        return (
            f"{BASE[system]}/v1/publishers/google/models/{model}:generateContent",
            {"x-goog-api-key": os.environ[KEYS[system]]},
            body,
        )
    if system == "perplexity":
        tool = {"type": "web_search", "search_type": "web"}
        if iso:
            tool["user_location"] = {"country": iso}
        body = {"model": model, "input": text, "tools": [tool]}
        return (
            f"{BASE[system]}/v1/agent",
            {"Authorization": f"Bearer {os.environ[KEYS[system]]}"},
            body,
        )
    if system == "deepseek":  # первый шаг цикла, остальные — Collector.deepseek
        body = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "tools": [DEEPSEEK_SEARCH],
            "thinking": {"type": "disabled"},
        }
        return (
            f"{BASE[system]}/chat/completions",
            {"Authorization": f"Bearer {os.environ[KEYS[system]]}"},
            body,
        )
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES[level]}
    if iso:
        tool["user_location"] = {"type": "approximate", "country": iso}
    body = {
        "model": model,
        "max_tokens": 16000,
        "output_config": {"effort": effort},
        "tools": [tool],
        "messages": [{"role": "user", "content": text}],
    }
    headers = {"x-api-key": os.environ[KEYS[system]], "anthropic-version": "2023-06-01"}
    return f"{BASE[system]}/v1/messages", headers, body


def parse(system, doc):
    """Ответ API → (текст, источники, число поисков, токены вход, токены выход или None)."""
    text, sources, searches = [], [], 0
    if system == "chatgpt":
        for item in doc.get("output") or []:
            if item.get("type") == "web_search_call":
                searches += 1
                sources += [
                    s.get("url")
                    for s in (item.get("action") or {}).get("sources") or []
                    if s.get("url")
                ]
            elif item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        text.append(c.get("text") or "")
                        sources += [a["url"] for a in c.get("annotations") or [] if a.get("url")]
        u = doc.get("usage") or {}
        tokens = (u.get("input_tokens"), u.get("output_tokens"))
    elif system == "gemini":
        cand = (doc.get("candidates") or [{}])[0]
        text = [
            p.get("text") or ""
            for p in (cand.get("content") or {}).get("parts") or []
            if not p.get("thought")
        ]
        gm = cand.get("groundingMetadata") or {}
        searches = len(gm.get("webSearchQueries") or [])
        for ch in gm.get("groundingChunks") or []:
            web = ch.get("web") or {}
            # uri — переадресация vertexaisearch, домен источника лежит в title
            title = (web.get("domain") or web.get("title") or "").strip().lower()
            if title and " " not in title and "." in title:
                sources.append(f"https://{title}/")
            elif web.get("uri"):
                sources.append(web["uri"])
        u = doc.get("usageMetadata") or {}
        out = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
        tokens = (u.get("promptTokenCount"), out if u else None)
    elif system == "perplexity":
        for item in doc.get("output") or []:
            if item.get("type") == "search_results":
                searches += 1
                sources += [x["url"] for x in item.get("results") or [] if x.get("url")]
            elif item.get("type") == "message":
                text += [
                    c.get("text") or ""
                    for c in item.get("content") or []
                    if c.get("type") == "output_text"
                ]
        u = doc.get("usage") or {}
        n = ((u.get("tool_calls_details") or {}).get("search_web") or {}).get("invocation")
        searches = n if n is not None else searches
        tokens = (u.get("input_tokens"), u.get("output_tokens"))
    elif system == "deepseek":  # {"steps": [...]} — запись всего цикла, см. Collector.deepseek
        tin = tout = 0
        for step in doc.get("steps") or []:
            r = step.get("response") or {}
            if step.get("type") == "search":
                searches += 1
                sources += [x["url"] for x in r.get("results") or [] if x.get("url")]
            else:
                u = r.get("usage") or {}
                tin += u.get("prompt_tokens") or 0
                tout += u.get("completion_tokens") or 0
                msg = ((r.get("choices") or [{}])[0]).get("message") or {}
                text = [msg.get("content") or ""]  # ответ — содержимое последнего шага
        tokens = (tin, tout)
    else:
        for block in doc.get("content") or []:
            if block.get("type") == "text":
                text.append(block.get("text") or "")
                sources += [c["url"] for c in block.get("citations") or [] if c.get("url")]
        u = doc.get("usage") or {}
        searches = (u.get("server_tool_use") or {}).get("web_search_requests") or 0
        tokens = (u.get("input_tokens"), u.get("output_tokens"))
    return "".join(text).strip(), list(dict.fromkeys(sources)), searches, *tokens


def cost(system, model, searches, tin, tout):
    if tin is None or tout is None:
        return None  # API не вернул токены — стоимость неизвестна, а не ноль
    pi, po = PRICES[model]
    return round(tin * pi / 1e6 + tout * po / 1e6 + searches * SEARCH_PRICE[system], 6)


# ---------- сбор ----------


class Collector:
    def __init__(
        self, doc, out, systems, max_cost=None, per_system=None, concurrency=None,
        repeats=None,
    ):  # fmt: skip
        self.repeats = repeats  # None — адаптивно: FIRST, при расхождении до MAX_REPEATS
        self.doc, self.out, self.systems = doc, out, systems
        self.max_cost, self.per_system = max_cost, per_system
        self.blocked = set()  # системы, упёршиеся в свой потолок расходов
        self.raw = out / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.answers = (out / "answers.jsonl").open("a", encoding="utf-8")
        self.spent, self.stopped = 0.0, False
        self.sem = {s: asyncio.Semaphore(concurrency or CONCURRENCY[s]) for s in systems}
        names = {"name_variants": [{"variant": n} for n in doc["name_variants"]]}
        self.pats = brand_patterns(names, doc["domain"])
        self.stats = {
            s: {"ok": 0, "error": 0, "empty": 0, "cost": 0.0, "cost_unknown": 0} for s in systems
        }

    def mentioned(self, text):
        return any(rx.search(text) for _, _, rx in self.pats)

    def cites_site(self, sources):
        """Есть ли сайт клиента (или его поддомен) среди источников ответа."""
        domain = self.doc["domain"].lower().removeprefix("www.")
        hosts = ((urlsplit(u).hostname or "").removeprefix("www.") for u in sources)
        return any(h == domain or h.endswith("." + domain) for h in hosts)

    async def call(self, client, system, model, effort, text, level):
        url, headers, body = request(system, model, effort, text, self.doc["country"], level)
        if system == "deepseek":
            return await self.deepseek(client, url, headers, body, MAX_SEARCHES[level])
        return await self.post(client, url, headers, body)

    async def deepseek(self, client, url, headers, body, limit):
        """Цикл DeepSeek: модель просит поиск, мы ищем через /v1/tools/search и возвращаем
        найденное, пока не будет ответа или лимита поисков. Все шаги — в одном raw-документе."""
        steps, msgs, searches = [], list(body["messages"]), 0
        for _ in range(limit + 3):
            req = {**body, "messages": msgs}
            if searches >= limit:
                req["tool_choice"] = "none"  # лимит исчерпан — только ответ
            doc = json.loads(await self.post(client, url, headers, req))
            steps.append({"type": "chat", "response": doc})
            msg = ((doc.get("choices") or [{}])[0]).get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls or searches >= limit:
                break
            msgs.append(
                {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
            )
            for c in calls:
                try:
                    query = str(json.loads(c["function"]["arguments"]).get("query") or "")
                except (KeyError, TypeError, ValueError):
                    query = ""
                content = "No results: empty query or search limit reached."
                if query and searches < limit:
                    found = json.loads(
                        await self.post(
                            client,
                            f"{BASE['deepseek']}/v1/tools/search",
                            headers,
                            {"query": query[:128], "count": DEEPSEEK_RESULTS},
                        )
                    )
                    searches += 1
                    steps.append({"type": "search", "query": query, "response": found})
                    content = json.dumps(
                        [
                            {k: x.get(k) for k in ("title", "url", "summary", "date")}
                            for x in found.get("results") or []
                        ],
                        ensure_ascii=False,
                    )
                msgs.append({"role": "tool", "tool_call_id": c.get("id"), "content": content})
        return json.dumps({"steps": steps}, ensure_ascii=False).encode()

    async def post(self, client, url, headers, body):
        for attempt in range(RETRIES + 1):  # сеть, 408, 429, 5xx — повтор с паузой
            wait = min(60.0, 5 * 2**attempt)
            try:
                r = await client.post(url, headers=headers, json=body)
            except httpx.TransportError as e:
                err = f"сеть: {type(e).__name__}"
            else:
                if r.status_code == 200:
                    return r.content
                if r.status_code not in (408, 429) and r.status_code < 500:
                    raise Rejected(f"HTTP {r.status_code}: {r.text[:300]}")
                err = f"HTTP {r.status_code}"
                try:  # лимит частоты: ждём столько, сколько просит API
                    wait = max(wait, min(120.0, float(r.headers.get("retry-after", 0))))
                except ValueError:
                    pass
            if attempt < RETRIES:
                await asyncio.sleep(wait)
        raise RuntimeError(err)

    async def answer(self, client, system, q, repeat):
        model, effort = ROUTES[system][q["level"]]
        row = {
            "answer_id": "a-" + uuid.uuid4().hex[:12],
            "prompt_id": q["id"],
            "system": system,
            "model": model,
            "effort": effort,
            "repeat": repeat,
            "created_at": datetime.now(UTC).isoformat(),
        }
        t0 = time.monotonic()
        async with self.sem[system]:
            for _ in range(2):  # пустой ответ — один повтор
                if self.stopped or system in self.blocked:
                    row.update(status="skipped", error="достигнут лимит расходов")
                    return self.write(row)
                try:
                    raw = await self.call(client, system, model, effort, q["text"], q["level"])
                except (Rejected, RuntimeError) as e:
                    row.update(
                        status="error", error=str(e), seconds=round(time.monotonic() - t0, 1)
                    )
                    self.stats[system]["error"] += 1
                    return self.write(row)
                text, sources, searches, tin, tout = parse(system, json.loads(raw))
                usd = cost(system, model, searches, tin, tout)
                if usd is None:  # токенов нет — для потолка берём оценку, в строке остаётся None
                    est_in, est_out, _ = ESTIMATE[system][max(0, q["level"] - 2)]
                    row["cost_estimated_usd"] = cost(system, model, searches, est_in, est_out)
                self.add_cost(system, usd, row.get("cost_estimated_usd"))
                name = f"{row['answer_id']}.json.gz"
                packed = gzip.compress(raw, mtime=0)
                (self.raw / name).write_bytes(packed)
                row.update(
                    raw_file=f"raw/{name}",
                    raw_sha256=hashlib.sha256(packed).hexdigest(),
                    text=text,
                    sources=sources,
                    searches=searches,
                    input_tokens=tin,
                    output_tokens=tout,
                    cost_usd=usd,
                    seconds=round(time.monotonic() - t0, 1),
                )
                if text:
                    break
            row["status"] = "ok" if row["text"] else "empty"
            row["mentioned"] = self.mentioned(row["text"]) if row["text"] else None
            row["cites_site"] = self.cites_site(row["sources"]) if row["text"] else None
            self.stats[system]["ok" if row["text"] else "empty"] += 1
            return self.write(row)

    def add_cost(self, system, usd, estimated=None):
        if usd is None:
            self.stats[system]["cost_unknown"] += 1
            usd = estimated or 0.0
        self.spent += usd
        self.stats[system]["cost"] += usd
        if self.max_cost is not None and self.spent >= self.max_cost:
            self.stopped = True
        if self.per_system is not None and self.stats[system]["cost"] >= self.per_system:
            self.blocked.add(system)

    def write(self, row):
        self.answers.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.answers.flush()
        return row

    async def pair(self, client, system, q):
        """Адаптивно: FIRST повторов, при расхождении — до MAX_REPEATS; или ровно self.repeats."""
        first = self.repeats or FIRST
        rows = await asyncio.gather(*(self.answer(client, system, q, i + 1) for i in range(first)))
        if self.repeats:
            return rows
        seen = {(r["mentioned"], r["cites_site"]) for r in rows if r["status"] == "ok"}
        if len(seen) > 1:
            more = MAX_REPEATS - FIRST
            rows += await asyncio.gather(
                *(self.answer(client, system, q, FIRST + i + 1) for i in range(more))
            )
        return rows

    async def run_targets(self, targets):
        """Дозапрос: заново только указанные (система, вопрос, номер повтора)."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
            done = await asyncio.gather(*(self.answer(client, s, q, rep) for s, q, rep in targets))
        self.answers.close()
        return done

    async def run(self, prompts):
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
            jobs = [self.pair(client, s, q) for s in self.systems for q in prompts]
            done = [r for rows in await asyncio.gather(*jobs) for r in rows]
        self.answers.close()
        return done


def latest(path):
    """Последняя строка по каждой паре (вопрос, система, повтор): дозапрос заменяет ошибку."""
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        rows[(r["prompt_id"], r["system"], r["repeat"])] = r
    return list(rows.values())


def summarize(rows, systems):
    out = {"systems": {}, "excluded": [], "spent_usd": 0.0}
    for s in systems:
        mine = [r for r in rows if r["system"] == s and r["status"] != "skipped"]
        bad = sum(r["status"] in {"error", "empty"} for r in mine)
        usd = sum(r.get("cost_usd") or 0 for r in mine)
        share = bad / len(mine) if mine else 1.0
        out["systems"][s] = {
            "answers": len(mine),
            "ok": len(mine) - bad,
            "failed": bad,
            "failed_share": round(share, 3),
            "mentioned": sum(bool(r.get("mentioned")) for r in mine),
            "cost_usd": round(usd, 4),
            "cost_unknown": sum(r["status"] == "ok" and r.get("cost_usd") is None for r in mine),
            "cost_estimated_usd": round(sum(r.get("cost_estimated_usd") or 0 for r in mine), 4),
            "seconds_avg": round(sum(r.get("seconds") or 0 for r in mine) / max(1, len(mine)), 1),
        }
        out["spent_usd"] += usd
        if share > 0.2:
            out["excluded"].append(
                {"system": s, "reason": f"нет ответа на {share:.0%} запросов (порог 20 %)"}
            )
    out["spent_usd"] = round(out["spent_usd"], 4)
    out["usable_systems"] = [s for s in systems if s not in {e["system"] for e in out["excluded"]}]
    return out


def estimate(doc, systems, repeats=(FIRST, MAX_REPEATS)):
    """Оценка стоимости по каждой системе: [при меньшем, при большем числе повторов]."""
    res = {}
    for s in systems:
        one = 0.0
        for q in doc["prompts"]:
            tin, tout, n = ESTIMATE[s][0 if q["level"] <= 2 else q["level"] - 2]
            one += cost(s, ROUTES[s][q["level"]][0], n, tin, tout)
        res[s] = [round(one * repeats[0], 2), round(one * repeats[1], 2)]
    res["всего"] = [
        round(sum(v[0] for v in res.values()), 2),
        round(sum(v[1] for v in res.values()), 2),
    ]
    return res


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step4_input", type=Path)
    ap.add_argument("--systems", nargs="+", default=list(ROUTES), choices=list(ROUTES))
    ap.add_argument("--dry-run", action="store_true", help="только проверить вход и показать план")
    ap.add_argument("--max-cost", type=float, help="общий потолок расходов прогона, $")
    ap.add_argument(
        "--max-cost-per-system", type=float, help="потолок расходов на каждую систему, $"
    )
    ap.add_argument(
        "--limit-prompts", type=int, help="взять только первые N вопросов (пробный прогон)"
    )
    ap.add_argument("--out", type=Path)
    ap.add_argument(
        "--repeats",
        type=int,
        help=f"ровно N повторов (по умолчанию {FIRST}, при расхождении до {MAX_REPEATS})",
    )
    ap.add_argument(
        "--retry-errors", action="store_true", help="заново отправить только упавшие ответы"
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        help="одновременных запросов к системе (по умолчанию CONCURRENCY)",
    )
    a = ap.parse_args()
    try:
        doc = load_step4_input(a.step4_input)
    except (ValueError, OSError) as e:
        sys.exit(f"Вход шага 4 не принят: {e}")
    prompts = doc["prompts"][: a.limit_prompts] if a.limit_prompts else doc["prompts"]
    reps = (a.repeats, a.repeats) if a.repeats else (FIRST, MAX_REPEATS)
    view = {**doc, "prompts": prompts}
    print(
        f"{doc['domain']} · язык: {doc['language']} · рынок: {doc['country']} · названия: {', '.join(doc['name_variants'])}"
    )
    print(
        json.dumps(
            {
                "план": plan(view, a.systems, reps),
                "оценка_$": estimate(view, a.systems, reps),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if a.dry_run:
        return
    missing = [KEYS[s] for s in a.systems if not os.environ.get(KEYS[s])]
    if missing:
        sys.exit(f"Нет ключей: {', '.join(missing)} (добавьте в private/.env)")
    if a.max_cost is None and a.max_cost_per_system is None:
        sys.exit("Укажите --max-cost или --max-cost-per-system: потолок расходов в долларах.")
    out = a.out or Path("private/step4") / a.step4_input.parent.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    col = Collector(
        doc, out, a.systems, a.max_cost, a.max_cost_per_system,
        concurrency=a.concurrency, repeats=a.repeats,
    )  # fmt: skip
    if a.retry_errors:
        by_id = {q["id"]: q for q in prompts}
        targets = [
            (r["system"], by_id[r["prompt_id"]], r["repeat"])
            for r in latest(out / "answers.jsonl")
            if r["status"] in {"error", "skipped"}
            and r["system"] in a.systems
            and r["prompt_id"] in by_id
        ]
        print(f"Дозапрос упавших ответов: {len(targets)}")
        asyncio.run(col.run_targets(targets))
    else:
        asyncio.run(col.run(prompts))
    rows = latest(out / "answers.jsonl")
    # отчёт — по всем системам в файле, иначе дозапрос одной системы затёр бы остальные
    systems = [s for s in ROUTES if any(r["system"] == s for r in rows)]
    summary = summarize(rows, systems)
    summary.update(
        seconds_total=round(time.monotonic() - t0, 1),
        prompts=len(prompts),
        stopped_by_cost=col.stopped,
        systems_stopped_by_cost=sorted(col.blocked),
    )
    (out / "step4_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["usable_systems"]:
        sys.exit("Ни одна система не ответила — аудит остановлен.")


if __name__ == "__main__":
    main()
