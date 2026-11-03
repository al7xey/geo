# ruff: noqa: E501
"""Шаг 2: общие функции чтения корпуса и проверок + базовый однозапросный режим (для сравнения).

Берёт проверенный корпус шага 1 (read_corpus), отправляет промт в Claude,
проверяет ответ кодом по правилам алгоритма, делает один повтор со списком
нарушений и при необходимости догенерирует недостающие запросы.

Примеры:
  # 1) Без затрат: только собрать промт и посмотреть, что уйдёт в модель
  uv run --locked python tools/step2_profile_queries.py private/runs/PUB_ID --dry-run
  # 2) Настоящий запуск (нужен ANTHROPIC_API_KEY)
  uv run --locked python tools/step2_profile_queries.py private/runs/PUB_ID
  # 3) Проверить уже сохранённый ответ модели без новых затрат
  uv run --locked python tools/step2_profile_queries.py private/runs/PUB_ID --response-file F
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import httpx

from geo_audit.step1.handoff import read_corpus

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_CORPUS_CHARS = 200_000  # примерно 60 тыс. токенов
LEVEL_NAMES = {1: "Базовые", 2: "С контекстом", 3: "Выбор и сравнение", 4: "Исследование"}
LENGTH = {1: (5, 20), 2: (12, 40), "3n": (15, 50), "3b": (25, 70), 4: (40, 150)}
PAIRS = {f"3.0{i}": f"P{i}" for i in range(1, 6)} | {f"3.{i + 5:02d}": f"P{i}" for i in range(1, 6)}
DUP_THRESHOLD = 0.85
TLD_COUNTRY = {
    "ru": "Россия",
    "se": "Швеция",
    "by": "Беларусь",
    "kz": "Казахстан",
    "ua": "Украина",
    "de": "Германия",
    "fi": "Финляндия",
    "uk": "Великобритания",
}
PLAN = [f"{lvl}.{i:02d}" for lvl in range(1, 5) for i in range(1, 11)]

TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "kh",
            "ts",
            "ch",
            "sh",
            "shch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ],
        strict=True,
    )
)


# ---------- вход ----------


def description_input(directory: Path, domain: str, description: str, reason: str) -> Path:
    """Вход шага 2 без корпуса: сайт закрыт для обхода, профиль строится по описанию."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "input.json").write_text(
        json.dumps(
            {"domain": domain, "user_description": description, "blocked_reason": reason},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return directory


def load_input(run_dir: Path):
    if (run_dir / "input.json").exists():
        raw = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        return {
            "pages": [],
            "user_description": raw["user_description"],
            "signals": collections.Counter(),
            "domain": raw["domain"],
            "material": "description_only",
            "publication": run_dir.name,
        }
    handoff, texts = read_corpus(run_dir)
    result = handoff.result
    by_text = {p.main_text.evidence_id: p for p in result.crawl.pages if p.main_text}
    pages = []
    for ref in (*handoff.useful_texts, *handoff.supporting_texts):
        page = by_text.get(ref.evidence_id)
        if page and ref.evidence_id in texts:
            pages.append({"url": str(page.effective_url), "text": texts[ref.evidence_id]})
    if not pages:
        # Полезных страниц нет (одностраничное JS-приложение): короткий текст, title и
        # meta всё же дают название и суть — передаём его, проверив хеш, как и остальное.
        from geo_audit.evidence import EvidenceStore
        from geo_audit.step1.storage import verify_file

        io, records = EvidenceStore(run_dir), {e.evidence_id: e for e in result.evidence}
        for page in result.crawl.pages:
            ref = page.main_text
            if ref and ref.text_chars and page.content_state == "html":
                text = verify_file(io, records[ref.evidence_id]).decode("utf-8")
                pages.append({"url": str(page.effective_url), "text": text})
    user = "\n".join(
        texts[u.text.evidence_id]
        for u in result.crawl.material.user_materials
        if u.text.evidence_id in texts
    )
    signals = collections.Counter()
    for p in result.crawl.pages:
        for s in p.signals:
            signals[(s.kind, s.value)] += 2 if s.strength == "explicit" else 1
    first = (
        pages[0]["url"] if pages else next((str(p.effective_url) for p in result.crawl.pages), "")
    )
    domain = re.sub(r"^www\.", "", httpx.URL(first).host) if first else ""
    return {
        "pages": pages,
        "user_description": user,
        "signals": signals,
        "domain": domain,
        "material": result.crawl.material.state,
        "publication": result.run.publication_id,
    }


# Запросы всегда на русском, независимо от языка сайта и рынка (решение 02.11.2026).
DEFAULT_LANGUAGE = "русский"
DEFAULT_COUNTRY = "Россия"  # рынок, если его не дали и домен не подсказывает (.com и т.п.)


def guess_language_country(data, language, country):
    if not country:
        country = TLD_COUNTRY.get(data["domain"].rsplit(".", 1)[-1], DEFAULT_COUNTRY)
    return language or DEFAULT_LANGUAGE, country


def build_corpus(pages, limit=MAX_CORPUS_CHARS):
    total = sum(len(p["text"]) for p in pages)
    truncated = total > limit
    parts = []
    for p in pages:
        text = p["text"]
        if truncated:
            text = text[: max(1500, int(len(text) * limit / total))]
        parts.append(f'  <page url="{p["url"]}">{text}</page>')
    return "\n".join(parts), truncated, {p["url"]: p["text"] for p in pages}


def render(prompt_md, data, language, country, corpus):
    system = (
        prompt_md.replace("{{LANGUAGE}}", language)
        .replace("{{COUNTRY}}", country)
        .replace("{{DOMAIN}}", data["domain"])
    )
    user = (
        f"<language>{language}</language>\n<country>{country}</country>\n"
        f"<domain>{data['domain']}</domain>\n<pages>\n{corpus}\n</pages>\n"
        f"<user_description>{data['user_description']}</user_description>"
    )
    return system, user


# ---------- API ----------


def call_claude(system, messages, model, max_tokens, log):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Нет ANTHROPIC_API_KEY. Задайте ключ или используйте --dry-run.")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "stream": True,
    }  # temperature не задаём: Sonnet 5.5 вернёт 400
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    text, usage = [], {}
    for attempt in range(3):
        try:
            with httpx.stream(
                "POST", API_URL, json=body, headers=headers, timeout=httpx.Timeout(900, connect=30)
            ) as r:
                if r.status_code in (429, 500, 529) and attempt < 2:
                    log(f"API {r.status_code}, повтор через 20 с")
                    import time

                    time.sleep(20)
                    continue
                if r.status_code != 200:
                    r.read()
                    sys.exit(f"API ошибка {r.status_code}: {r.text[:500]}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:])
                    if ev["type"] == "message_start":
                        usage.update(ev["message"].get("usage", {}))
                    elif (
                        ev["type"] == "content_block_delta"
                        and ev["delta"].get("type") == "text_delta"
                    ):
                        text.append(ev["delta"]["text"])
                    elif ev["type"] == "message_delta":
                        usage.update(ev.get("usage", {}))
                        usage["stop_reason"] = ev["delta"].get("stop_reason")
                    elif ev["type"] == "error":
                        sys.exit(f"Ошибка потока API: {ev}")
            return "".join(text), usage
        except httpx.TransportError as e:
            log(f"Сетевая ошибка {e!r}, попытка {attempt + 1}/3")
    sys.exit("API недоступно после 3 попыток")


# ---------- разбор и проверки ----------


def extract(raw, tag):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", raw, re.S)
    if not m:
        raise ValueError(f"нет блока <{tag}>")
    return json.loads(re.sub(r"^```(json)?|```$", "", m.group(1).strip()).strip())


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def words(s):
    return len(re.findall(r"\S+", s))


def quote_ok(quote, url, page_texts, user_text):
    if not quote:
        return False
    hay = user_text if url == "user_description" else page_texts.get(url)
    if hay is None:  # адрес мог прийти без/с завершающим «/»
        hay = next(
            (t for u, t in page_texts.items() if u.rstrip("/") == str(url).rstrip("/")), None
        )
    return hay is not None and norm(quote) in norm(hay)


def clean_profile(profile, page_texts, user_text):
    """Удаляет факты без дословной цитаты (правило алгоритма). Возвращает список удалённого."""
    removed = []

    def keep(item, where):
        ok = quote_ok(item.get("quote"), item.get("url"), page_texts, user_text)
        if ok and words(item.get("quote", "")) > 25:
            removed.append({"where": where, "item": item, "why": "цитата длиннее 25 слов"})
            return False
        if not ok:
            removed.append({"where": where, "item": item, "why": "цитаты нет в текстах"})
        return ok

    for key in ("activity", "products_services", "target_audience", "prices", "self_claims"):
        profile[key] = [x for x in profile.get(key) or [] if keep(x, key)]
    geo = profile.get("geography") or {}
    for key in ("locations", "service_area"):
        geo[key] = [x for x in geo.get(key) or [] if keep(x, f"geography.{key}")]
    profile["geography"] = geo
    for key in ("display_name", "legal_name"):
        item = (profile.get("company") or {}).get(key)
        if item and item.get("value") and not keep(item, f"company.{key}"):
            profile["company"][key] = None
    variants, seen = [], set()
    for v in profile.get("name_variants") or []:
        src = v.get("source") or {}
        key = (v.get("variant") or "").strip().casefold()
        if key in seen:  # повтор того же написания с другим типом — один вариант
            continue
        if src.get("url") == "transliteration" or keep(src, f"name_variants:{v.get('variant')}"):
            variants.append(v)
            seen.add(key)
    profile["name_variants"] = variants
    cat = profile.get("category") or {}
    cat["basis"] = [b for b in cat.get("basis") or [] if keep(b, "category.basis")]
    return removed


def translit(s):
    return "".join(TRANSLIT.get(c, c) for c in s.lower())


def brand_patterns(profile, domain):
    """Название с окончаниями (до 3 букв после основы) и транслитерацией, как на шаге 5."""
    terms = {}
    for v in profile.get("name_variants") or []:
        if v.get("variant"):
            terms[v["variant"]] = bool(v.get("ambiguous"))
    for t in profile.get("proprietary_terms") or []:
        if t:
            terms[t] = False
    if domain:
        terms[domain] = False
        terms[domain.split(".")[0]] = False
    pats = []
    for term, ambiguous in terms.items():
        forms = {term.lower(), translit(term)}
        for f in forms:
            if len(f) < 3:
                continue
            stem = re.sub(r"[аяоеыиуюйьaeiouy]+$", "", f) if len(f) > 4 else f
            pats.append(
                (term, ambiguous, re.compile(rf"(?<!\w){re.escape(stem)}\w{{0,3}}(?!\w)", re.I))
            )
    return pats


def check_prompts(prompts, profile, domain):
    """Возвращает (годные запросы по позиции, нарушения запросов, общие нарушения)."""
    issues, global_issues, good = collections.defaultdict(list), [], {}
    if profile.get("data_status") == "insufficient":
        return {}, issues, ["data_status=insufficient: запросы не составлены"]
    pats = brand_patterns(profile, domain)
    seen = {}
    for q in prompts:
        pos = q.get("plan_position")
        text = (q.get("text") or "").strip()
        p = issues[pos or q.get("id", "?")]
        if pos not in PLAN:
            p.append(f"неизвестная позиция плана {pos!r}")
            continue
        if pos in seen:
            p.append("позиция встречается дважды")
            continue
        seen[pos] = q
        level = int(pos[0])
        branded = pos in {"3.06", "3.07", "3.08", "3.09", "3.10"}
        if q.get("level") != level or q.get("level_name") != LEVEL_NAMES[level]:
            p.append("неверный level/level_name")
        if bool(q.get("has_brand")) != branded:
            p.append("неверный has_brand")
        if q.get("counts_in_organic_visibility") != (not branded):
            p.append("неверный counts_in_organic_visibility")
        if q.get("pair_id") != PAIRS.get(pos):
            p.append(f"pair_id должен быть {PAIRS.get(pos)}")
        lo, hi = LENGTH["3b" if branded else "3n"] if level == 3 else LENGTH[level]
        n = words(text)
        if not lo <= n <= hi:
            p.append(f"длина {n} слов, нужно {lo}–{hi}; сделай около {(lo + hi) // 2} слов")
        if q.get("word_count") != n:
            q["word_count"] = n  # пересчитываем сами, это не повод выбрасывать запрос
        if re.search(r"\b(19|20)\d{2}\b", text):
            p.append("в запросе есть год")
        hits = [(t, amb) for t, amb, rx in pats if rx.search(text)]
        if not branded:
            hard = [t for t, amb in hits if not amb]
            if hard:
                p.append(f"подсказка ответа: найдено {sorted(set(hard))}")
            soft = [t for t, amb in hits if amb]
            if soft:
                q.setdefault("warnings", []).append(
                    f"неоднозначный вариант названия: {sorted(set(soft))}"
                )
        elif not hits:
            p.append("в брендовом запросе не найдено название компании")
    # почти одинаковые запросы
    items = [(pos, norm(q["text"]).lower()) for pos, q in seen.items()]
    for i, (a, ta) in enumerate(items):
        for b, tb in items[i + 1 :]:
            if PAIRS.get(a) and PAIRS.get(a) == PAIRS.get(b):
                continue  # пары уровня 3 похожи по замыслу
            r = difflib.SequenceMatcher(None, ta, tb).ratio()
            if r >= DUP_THRESHOLD:
                issues[b].append(f"почти дубль {a} (сходство {r:.0%})")
    # одинаковое название во всех брендовых
    branded_q = [seen[p]["text"] for p in ("3.06", "3.07", "3.08", "3.09", "3.10") if p in seen]
    if branded_q:
        common = [t for t, _, rx in pats if all(rx.search(x) for x in branded_q)]
        if not common:
            global_issues.append("в брендовых запросах название записано по-разному")
    for pos, q in seen.items():
        if not issues.get(pos):
            good[pos] = q
    missing = [p for p in PLAN if p not in seen]
    if missing:
        global_issues.append(f"нет позиций: {missing}")
    return good, {k: v for k, v in issues.items() if v}, global_issues


def violations_text(removed, issues, global_issues):
    lines = [f"- общее: {g}" for g in global_issues]
    lines += [f"- запрос {pos}: {'; '.join(v)}" for pos, v in sorted(issues.items())]
    lines += [f"- профиль, {r['where']}: {r['why']}" for r in removed[:30]]
    return "\n".join(lines)


# ---------- основной поток ----------


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_dir", type=Path, help="папка публикации шага 1 (private/runs/step1-...)")
    ap.add_argument(
        "--prompt", type=Path, default=Path("private/prompts/prompt_step2_profile_and_queries.md")
    )
    ap.add_argument("--language")
    ap.add_argument("--country")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=64000)
    ap.add_argument(
        "--out", type=Path, help="куда сохранить (по умолчанию private/step2/<публикация>)"
    )
    ap.add_argument("--dry-run", action="store_true", help="только собрать промт, без вызова API")
    ap.add_argument(
        "--response-file", type=Path, help="проверить сохранённый ответ модели, без API"
    )
    args = ap.parse_args()

    data = load_input(args.run_dir)
    out = args.out or Path("private/step2") / data["publication"]
    out.mkdir(parents=True, exist_ok=True)
    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(msg)

    language, country = guess_language_country(data, args.language, args.country)
    if not language or not country:
        sys.exit(
            f"Не удалось определить язык ({language}) или страну ({country}). "
            "Укажите --language и --country."
        )
    log(
        f"Страниц: {len(data['pages'])}, материал шага 1: {data['material']}, "
        f"язык: {language}, страна: {country}, домен: {data['domain']}"
    )
    corpus, truncated, page_texts = build_corpus(data["pages"])
    if truncated:
        log(f"Тексты обрезаны до ~{MAX_CORPUS_CHARS} символов пропорционально длине страниц")
    system, user = render(args.prompt.read_text(encoding="utf-8"), data, language, country, corpus)
    (out / "request_system.md").write_text(system, encoding="utf-8")
    (out / "request_user.xml").write_text(user, encoding="utf-8")
    if args.dry_run:
        log(
            f"Dry-run: промт сохранён в {out}. Символов: {len(system) + len(user)}"
            f" (~{(len(system) + len(user)) // 3500} тыс. токенов)"
        )
        return

    usage_total = collections.Counter()
    messages = [{"role": "user", "content": user}]

    def ask(msgs, tag):
        raw, usage = call_claude(system, msgs, args.model, args.max_tokens, log)
        (out / f"raw_{tag}.txt").write_text(raw, encoding="utf-8")
        for k in ("input_tokens", "output_tokens"):
            usage_total[k] += usage.get(k) or 0
        if usage.get("stop_reason") == "max_tokens":
            log(
                f"Ответ обрезан по max_tokens={args.max_tokens} (сохранён в raw_{tag}.txt, "
                f"токенов: вход {usage.get('input_tokens')}, выход {usage.get('output_tokens')}). "
                "Повтор с тем же лимитом бесполезен — запустите снова с --max-tokens 64000."
            )
            sys.exit(1)
        return raw

    raw = (
        args.response_file.read_text(encoding="utf-8")
        if args.response_file
        else ask(messages, "attempt1")
    )
    attempts = 1
    while True:
        try:
            profile, prompts = extract(raw, "profile_json"), extract(raw, "prompts_json")["prompts"]
            removed = clean_profile(profile, page_texts, data["user_description"])
            good, issues, global_issues = check_prompts(prompts, profile, data["domain"])
        except (ValueError, KeyError, TypeError) as e:
            profile, prompts, removed, good, issues = None, [], [], {}, {}
            global_issues = [f"ответ не соответствует структуре: {e}"]
        log(
            f"Попытка {attempts}: годных запросов {len(good)}/40, "
            f"нарушений в запросах {len(issues)}, общих {len(global_issues)}, "
            f"удалено фактов профиля {len(removed)}"
        )
        if (not issues and not global_issues) or attempts >= 2 or args.response_file:
            break
        attempts += 1
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "Проверка кодом нашла нарушения. Исправь их и верни "
                "полный ответ в том же формате:\n"
                + violations_text(removed, issues, global_issues),
            },
        ]
        raw = ask(messages, "attempt2")

    # догенерация недостающих позиций коротким отдельным запросом
    missing = [p for p in PLAN if p not in good]
    if (
        profile
        and missing
        and profile.get("data_status") != "insufficient"
        and not args.response_file
    ):
        log(f"Догенерация {len(missing)} позиций: {missing}")
        kept = [{"plan_position": p, "text": q["text"]} for p, q in sorted(good.items())]
        topup = (
            "Профиль компании (уже проверен):\n"
            + json.dumps(profile, ensure_ascii=False)
            + "\n\nУже принятые запросы (не повторяй их намерения):\n"
            + json.dumps(kept, ensure_ascii=False)
            + f"\n\nСоставь только позиции плана {missing} по тем же правилам. "
            'Верни только блок <prompts_json>{"prompts": [...]}</prompts_json>.'
        )
        raw3 = ask([{"role": "user", "content": topup}], "topup")
        try:
            extra = extract(raw3, "prompts_json")["prompts"]
            g2, iss2, _ = check_prompts(list(good.values()) + extra, profile, data["domain"])
            for p in missing:
                if p in g2:
                    good[p] = g2[p]
            issues = {p: v for p, v in iss2.items() if p not in good}
        except (ValueError, KeyError, TypeError) as e:
            log(f"Догенерация не удалась: {e}")

    final = []
    for i, pos in enumerate(PLAN, 1):
        if pos in good:
            q = good[pos]
            q.update(
                id=f"q{i:02d}", origin="suggested"
            )  # пометка для шага 3: предложен/изменён/свой
            final.append(q)
    status = "ok" if len(final) == 40 else ("partial" if final else "failed")
    (out / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "prompts.json").write_text(
        json.dumps({"prompts": final}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "attempts": attempts,
        "prompts_accepted": len(final),
        "rejected": issues,
        "global_issues": global_issues,
        "profile_facts_removed": removed,
        "corpus_truncated": truncated,
        "language": language,
        "country": country,
        "usage": dict(usage_total),
        "cost_usd_estimate": round(
            usage_total["input_tokens"] * 2e-6 + usage_total["output_tokens"] * 10e-6, 4
        ),
        "log": log_lines,
    }
    (out / "step2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nИтог: {status}, принято запросов {len(final)}/40. Файлы: {out}")
    sys.exit(0 if status == "ok" else 3 if status == "partial" else 1)


if __name__ == "__main__":
    main()
