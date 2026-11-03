# ruff: noqa: E501
"""Шаг 2а: справка о компании из веб-поиска (Haiku 5.5 + web_search).

Нужна, когда сайт отдаёт роботу мало текста (JS-приложение) или закрыт антиботом.
Запускается параллельно с шагами 0–1: ей нужен только домен.

Проверка кодом: факт остаётся, только если его url был среди результатов поиска,
которые вернул API; цитата помечается проверенной, если совпадает с цитатой
(citations), которую API приложил к ответу. Модель не может подсунуть свой адрес.

  uv run --locked --no-dev python tools/step2_web.py variant.com --country США
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.10, 0.50  # $/млн токенов, Haiku 5.5 до 100 тыс. токенов
PRICE_LONG_IN, PRICE_LONG_OUT = 0.50, 2.50
PRICE_SEARCH = 10.0 / 1000  # $ за один поиск, platform.claude.com, 02.11.2026
MAX_SEARCHES = 4
FACT_FIELDS = ("activity", "products_services", "target_audience", "geography", "prices")
COUNTRY_ISO = {
    "Россия": "RU",
    "США": "US",
    "Казахстан": "KZ",
    "Беларусь": "BY",
    "Украина": "UA",
    "Германия": "DE",
    "Великобритания": "GB",
    "Швеция": "SE",
}

SYSTEM = """Ты готовишь справку о компании, которой принадлежит сайт {domain}, для аудита этого сайта.
Рынок: {country}. Язык ответа: русский.

Как искать:
1. Сначала ищи по домену «{domain}», потом по названию компании вместе с доменом.
2. Не больше {searches} поисков.

Правила:
- Пиши только о компании с сайта {domain}. Если есть другие компании с похожим названием, не используй их.
  Если не уверен, что источник о той же компании, пропусти его.
- Каждый факт подтверждай короткой дословной цитатой (до 25 слов) из источника и его адресом (url).
- Помечай источник: «сайт компании» или «сторонний» (каталог, обзор, новость, магазин приложений, соцсеть).
- Ничего не додумывай. Если что-то не нашлось, напиши «не найдено».
- Не оценивай компанию, не хвали и не критикуй.
- Коротко: в каждом поле не больше 3 фактов, факт — одна короткая фраза.

Ответ — только JSON:
{{
  "name_variants": ["как компанию называют в источниках"],
  "activity": [{{"fact": "", "quote": "", "url": "", "source": "сайт компании|сторонний"}}],
  "products_services": [...],
  "target_audience": [...],
  "geography": [...],
  "prices": [...],
  "category": "как клиент назвал бы такую компанию, 2–5 слов",
  "compared_with": ["компании, с которыми её сравнивают в источниках"],
  "not_found": ["что искал, но не нашёл"]
}}"""


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip().casefold()


def _url_key(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip()).rstrip("/").casefold()


def parse(content):
    """Блоки ответа API → (сырой текст, адреса результатов поиска, цитаты API)."""
    text, urls, cited = [], set(), []
    for block in content:
        kind = block.get("type")
        if kind == "web_search_tool_result" and isinstance(block.get("content"), list):
            urls.update(_url_key(r.get("url")) for r in block["content"] if r.get("url"))
        elif kind == "text":
            text.append(block.get("text", ""))
            for c in block.get("citations") or []:
                if c.get("cited_text"):
                    cited.append((_url_key(c.get("url")), _norm(c["cited_text"])))
    return "".join(text), urls, cited


def loads_lenient(raw):
    """JSON из ответа модели: без обёртки ```json и висячих запятых."""
    body = raw[raw.index("{") : raw.rindex("}") + 1]
    return json.loads(re.sub(r",\s*([}\]])", r"\1", body))


def verify(brief, urls, cited):
    """Оставляет факты с адресом из выдачи поиска; помечает, подтверждена ли цитата API."""
    dropped = 0
    for field in FACT_FIELDS:
        kept = []
        for item in brief.get(field) or []:
            if not isinstance(item, dict) or _url_key(item.get("url")) not in urls:
                dropped += 1
                continue
            q = _norm(item.get("quote"))
            item["quote_checked"] = bool(q) and any(
                u == _url_key(item["url"]) and (q in t or t in q) for u, t in cited
            )
            kept.append(item)
        brief[field] = kept
    brief["facts_dropped"] = dropped
    return brief


def facts(brief):
    return [i for f in FACT_FIELDS for i in (brief or {}).get(f) or []]


async def web_brief(domain, country, log=print):
    """Возвращает (справка или None, запись о вызове для отчёта)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Нет ANTHROPIC_API_KEY.")
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}
    if COUNTRY_ISO.get(country):
        tool["user_location"] = {"type": "approximate", "country": COUNTRY_ISO[country]}
    body = {
        "model": MODEL,
        "max_tokens": 8000,
        "system": SYSTEM.format(domain=domain, country=country, searches=MAX_SEARCHES),
        "output_config": {"effort": "low"},
        "tools": [tool],
        "messages": [{"role": "user", "content": f"Сайт: {domain}. Составь справку."}],
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    t0, content, usage = time.monotonic(), [], {"input_tokens": 0, "output_tokens": 0}
    searches = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30), headers=headers) as c:
        for _ in range(3):  # pause_turn: сервер просит продолжить длинный ход
            r = await c.post(API_URL, json=body)
            if r.status_code != 200:
                log(f"[web] API ошибка {r.status_code}: {r.text[:300]}")
                return None, None
            msg = r.json()
            content += msg["content"]
            u = msg.get("usage") or {}
            usage["input_tokens"] += u.get("input_tokens") or 0
            usage["output_tokens"] += u.get("output_tokens") or 0
            searches += (u.get("server_tool_use") or {}).get("web_search_requests") or 0
            if msg.get("stop_reason") != "pause_turn":
                break
            body["messages"] = [*body["messages"], {"role": "assistant", "content": msg["content"]}]
    sec = time.monotonic() - t0
    long = usage["input_tokens"] > 100_000
    cost = (
        usage["input_tokens"] * (PRICE_LONG_IN if long else PRICE_IN) / 1e6
        + usage["output_tokens"] * (PRICE_LONG_OUT if long else PRICE_OUT) / 1e6
        + searches * PRICE_SEARCH
    )
    call = {
        "tag": "web",
        "model": MODEL,
        "effort": "low",
        "seconds": round(sec, 1),
        **usage,
        "web_searches": searches,
        "cost_usd": round(cost, 5),
        "stop_reason": msg.get("stop_reason"),
    }
    raw, urls, cited = parse(content)
    call["raw"] = content  # для разбора сбоев; в отчёт шага 2 не попадает
    try:
        brief = loads_lenient(raw)
    except ValueError as e:
        log(f"[web] ответ не разобран ({e}): {raw[:200]!r}")
        return None, call
    brief = verify(brief, urls, cited)
    checked = sum(i.get("quote_checked") for i in facts(brief))
    log(
        f"[web] {MODEL}/low: {sec:.0f} с, поисков {searches}, вход {usage['input_tokens']}, "
        f"выход {usage['output_tokens']}, ${cost:.4f}; фактов {len(facts(brief))} "
        f"(цитата подтверждена API: {checked}, отброшено без адреса из выдачи: {brief['facts_dropped']})"
    )
    return brief, call


def external_block(brief):
    """Текст для шага 2 и «страницы» с цитатами для проверки профиля кодом."""
    rows = facts(brief)
    if not rows:
        return "", {}
    texts = {}
    names = [n for n in brief.get("name_variants") or [] if isinstance(n, str)]
    for item in rows:
        texts.setdefault(item["url"], list(names)).append(item.get("quote") or "")
    block = (
        "\n<external_sources>\nСведения из веб-поиска о компании этого сайта. Это НЕ тексты сайта: "
        "используй их только для того, чего нет на страницах; факт переноси с той же цитатой и url, "
        "сторонние источники не выдавай за слова компании.\n"
        + json.dumps(
            {k: brief.get(k) for k in ("name_variants", *FACT_FIELDS, "category", "compared_with")},
            ensure_ascii=False,
        )
        + "\n</external_sources>"
    )
    return block, {u: "\n".join(q) for u, q in texts.items()}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("domain")
    ap.add_argument("--country", required=True)
    a = ap.parse_args()
    brief, call = asyncio.run(web_brief(a.domain, a.country))
    print(json.dumps({"brief": brief, "call": call}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
