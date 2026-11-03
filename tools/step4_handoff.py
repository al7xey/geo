# ruff: noqa: E501
"""Переход шаг 3 → шаг 4: файл step4_input.json и его проверка перед сбором ответов.

Шаг 3 пишет файл через write_step4_input, шаг 4 читает только через load_step4_input:
загрузчик сверяет контрольную сумму вопросов и заново проверяет инварианты, поэтому
файл, исправленный руками после проверки шага 3, в сбор ответов не попадёт.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "step4-input-1"
ORIGINS = {"suggested", "edited", "custom"}  # предложен, изменён, свой
MAX_CUSTOM = 10
MAX_PROMPTS = 50


def prompts_sha256(prompts):
    canon = json.dumps(prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def write_step4_input(path: Path, *, domain, language, country, name_variants, prompts, source):
    doc = {
        "schema_version": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "domain": domain,
        "language": language,
        "country": country,
        "name_variants": name_variants,  # нужны шагу 5 для поиска названия в ответах
        "source": source,
        "prompts": prompts,
        "prompts_sha256": prompts_sha256(prompts),
    }
    errors = invariants(doc)
    if errors:
        raise ValueError("; ".join(errors))
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def invariants(doc):
    errors = []
    for key in ("domain", "language", "country"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            errors.append(f"не указано поле {key}")
    names = doc.get("name_variants")
    if (
        not isinstance(names, list)
        or not names
        or not all(isinstance(n, str) and n.strip() for n in names)
    ):
        errors.append("нужен хотя бы один вариант названия компании")
    prompts = doc.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        return errors + ["нет вопросов"]
    if len(prompts) > MAX_PROMPTS:
        errors.append(f"вопросов {len(prompts)}, допустимо не больше {MAX_PROMPTS}")
    ids = [q.get("id") for q in prompts]
    if len(set(ids)) != len(ids):
        errors.append("повторяются id вопросов")
    if sum(q.get("origin") == "custom" for q in prompts) > MAX_CUSTOM:
        errors.append(f"своих вопросов больше {MAX_CUSTOM}")
    for q in prompts:
        qid = q.get("id")
        if not isinstance(q.get("text"), str) or not q["text"].strip():
            errors.append(f"{qid}: пустой текст")
        if q.get("origin") not in ORIGINS:
            errors.append(f"{qid}: метка {q.get('origin')!r} не из {sorted(ORIGINS)}")
        if q.get("level") not in (1, 2, 3, 4):
            errors.append(f"{qid}: не указан вид (уровень 1–4)")
        if q.get("counts_in_organic_visibility") == bool(q.get("has_brand")):
            errors.append(f"{qid}: вопрос с названием компании не может входить в расчёт видимости")
    return errors


def load_step4_input(path: Path):
    """Единственный вход шага 4. Бросает ValueError с понятной причиной."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != SCHEMA:
        raise ValueError(f"неизвестная схема {doc.get('schema_version')!r}, ожидалась {SCHEMA}")
    if prompts_sha256(doc.get("prompts") or []) != doc.get("prompts_sha256"):
        raise ValueError(
            "вопросы изменены после проверки шага 3 — откройте шаг 3 и сохраните заново"
        )
    errors = invariants(doc)
    if errors:
        raise ValueError("; ".join(errors))
    return doc


def plan(doc, systems, repeats=(3, 10)):
    """Сколько запросов уйдёт в каждую систему при числе повторов от repeats[0] до repeats[1]."""
    n = len(doc["prompts"])
    lo, hi = repeats
    return {
        "prompts": n,
        "organic": sum(q["counts_in_organic_visibility"] for q in doc["prompts"]),
        "branded": sum(q["has_brand"] for q in doc["prompts"]),
        "by_origin": {o: sum(q["origin"] == o for q in doc["prompts"]) for o in sorted(ORIGINS)},
        "per_system_requests": [n * lo, n * hi],
        "total_requests": [n * lo * len(systems), n * hi * len(systems)],
    }
