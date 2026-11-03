# ruff: noqa: E501
"""Сравнение двух результатов шага 2 (например, исходный и быстрый) на одном сайте.

  uv run --locked --no-dev python tools/step2_compare.py private/step2/step1-X private/step2_fast/step1-X

Печатает метрики, которые код считает объективно, и создаёт слепой лист для ручной оценки:
blind_review.md (варианты A/B перемешаны по каждой позиции) и blind_key.json (какой вариант чей).
"""

import json
import random
import sys
from pathlib import Path


def load(d):
    d = Path(d)
    prof = json.loads((d / "profile.json").read_text(encoding="utf-8")) or {}
    prompts = {
        q["plan_position"]: q
        for q in json.loads((d / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    }
    rep = json.loads((d / "step2_report.json").read_text(encoding="utf-8"))
    return prof, prompts, rep


def facts(p):
    g = p.get("geography") or {}
    return {
        k: len(p.get(k) or [])
        for k in ("activity", "products_services", "target_audience", "prices", "self_claims")
    } | {
        "locations": len(g.get("locations") or []),
        "service_area": len(g.get("service_area") or []),
        "name_variants": len(p.get("name_variants") or []),
        "unknowns": len(p.get("unknowns") or []),
    }


def cost_time(rep):
    if "calls" in rep:
        return rep["cost_usd"], rep.get("seconds_total")
    return rep.get("cost_usd_estimate"), None


def main():
    a_dir, b_dir = sys.argv[1], sys.argv[2]
    (pa, qa, ra), (pb, qb, rb) = load(a_dir), load(b_dir)
    print(
        f"{'метрика':28} {'A: ' + Path(a_dir).parent.name:>22} {'B: ' + Path(b_dir).parent.name:>22}"
    )
    rows = [
        ("статус", ra["status"], rb["status"]),
        ("принято запросов", ra["prompts_accepted"], rb["prompts_accepted"]),
        ("отклонено позиций", len(ra.get("rejected") or {}), len(rb.get("rejected") or {})),
        (
            "удалено фактов профиля",
            len(ra.get("profile_facts_removed") or []),
            len(rb.get("profile_facts_removed") or []),
        ),
        ("data_status", pa.get("data_status"), pb.get("data_status")),
        ("стоимость, $", *(cost_time(r)[0] for r in (ra, rb))),
        ("время, с", *(cost_time(r)[1] for r in (ra, rb))),
    ]
    fa, fb = facts(pa), facts(pb)
    rows += [(f"фактов: {k}", fa[k], fb[k]) for k in fa]
    for name, x, y in rows:
        print(f"{name:28} {str(x):>22} {str(y):>22}")
    ca, cb = (pa.get("category") or {}), (pb.get("category") or {})
    print(f"\nКатегория A: {ca.get('main')} | {ca.get('client_terms')}")
    print(f"Категория B: {cb.get('main')} | {cb.get('client_terms')}")
    na = sorted(v["variant"] for v in pa.get("name_variants") or [])
    nb = sorted(v["variant"] for v in pb.get("name_variants") or [])
    print(f"Названия A: {na}\nНазвания B: {nb}")

    rng = random.Random(42)  # nosec B311 - перемешивание для слепой оценки, не криптография
    key, lines = (
        {},
        [
            "# Слепая оценка запросов\n",
            "По каждой позиции отметьте лучший вариант (A, B или =). Критерии: похоже на "
            "реального клиента; соответствует назначению позиции; не выходит за деятельность "
            "компании; не подсказывает ответ.\n",
        ],
    )
    for pos in sorted(set(qa) | set(qb)):
        pair = [("A", qa.get(pos)), ("B", qb.get(pos))]
        rng.shuffle(pair)
        key[pos] = {"1": pair[0][0], "2": pair[1][0]}
        scen = (pair[0][1] or pair[1][1] or {}).get("scenario", "")
        lines.append(f"\n## {pos} — {scen}\n")
        for i, (_, q) in enumerate(pair, 1):
            lines.append(f"**{i}.** {(q or {}).get('text', '— нет запроса —')}\n")
        lines.append("Лучше: ___\n")
    out = Path(b_dir)
    (out / "blind_review.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "blind_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(
        f"\nЛист для слепой оценки: {out / 'blind_review.md'} (ключ: blind_key.json — не открывайте до оценки)"
    )


if __name__ == "__main__":
    main()
