"""Офлайн-тесты шага 2 (tools/). Сеть и ключ API не нужны."""

import asyncio
import json
import random
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import step2_checks as C  # noqa: E402
import step2_fast as F  # noqa: E402

PROFILE = {
    "data_status": "ok",
    "domain": "romashka.ru",
    "name_variants": [{"variant": "Ромашка", "ambiguous": False}],
    "proprietary_terms": [],
}
VOC = "альфа бета гамма дельта эпсилон дзета тета йота каппа лямбда сигма омега".split()


def text(rng, n, brand=False):
    t = " ".join(rng.choice(VOC) + str(rng.randint(0, 99999)) for _ in range(n))
    return "Ромашка " + t if brand else t


def length(pos):
    lvl = int(pos[0])
    return {1: 10, 2: 20, 3: 40 if pos in F.BRANDED else 25, 4: 60}[lvl]


def test_brand_forms_and_translit():
    pats = C.brand_patterns({"name_variants": [{"variant": "Ромашка"}]}, "romashka.ru")
    hit = lambda s: any(rx.search(s) for _, _, rx in pats)  # noqa: E731
    assert hit("сравни с Ромашкой") and hit("компания Romashka")
    assert not hit("ромашковый чай")


def test_quote_must_be_verbatim():
    prof = {
        "activity": [
            {"quote": "Делаем ремонт квартир", "url": "u"},
            {"quote": "Лидер рынка с 1990", "url": "u"},
        ]
    }
    removed = C.clean_profile(prof, {"u": "Мы  делаем.\nДелаем ремонт   квартир под ключ"}, "")
    assert len(prof["activity"]) == 1 and len(removed) == 1


def test_stage_queries_fixes_only_bad_positions():
    rng, calls = random.Random(1), []

    class Fake:
        def log(self, m):
            pass

        async def ask(self, tag, model, effort, system, user, max_tokens=0):
            calls.append(tag)
            lvl = int(tag[1])
            if "fix" in tag:
                line = user.split("Составь заново только позиции")[1].split("\n")[0]
                ps = re.findall(r"\d\.\d\d", line)
            else:
                ps = [p for p in F.PLAN if p.startswith(f"{lvl}.")]
            out = []
            for p in ps:
                t = text(rng, length(p), p in F.BRANDED)
                if "fix" not in tag and p == "1.03":
                    t = "Где заказать как у Ромашки услуги рядом недорого"
                if "fix" not in tag and p == "3.02":
                    t = "коротко"
                out.append({"plan_position": p, "text": t})
            if lvl == 4 and "fix" not in tag:
                out = out[:8]
            return (
                "<prompts_json>"
                + json.dumps({"prompts": out}, ensure_ascii=False)
                + "</prompts_json>"
            )

    good, issues, glob = asyncio.run(
        F.stage_queries(
            Fake(), "m", "medium", {i: "" for i in range(1, 5)}, PROFILE, "", "romashka.ru", {}
        )
    )
    assert len(good) == 40 and not issues and not glob
    assert sorted(calls) == sorted(["L1", "L2", "L3", "L4", "L1-fix", "L3-fix", "L4-fix"])
    q = good["3.07"]
    assert q["pair_id"] == "P2" and q["has_brand"] and not q["counts_in_organic_visibility"]


def test_corpus_drops_repeated_boilerplate(monkeypatch):
    import geo_audit.step1.handoff as H

    empty = types.SimpleNamespace(
        result=types.SimpleNamespace(crawl=types.SimpleNamespace(pages=[], candidates=[]))
    )
    monkeypatch.setattr(H, "load_step2_input", lambda d: empty)
    menu = "Главная\nУслуги\nКонтакты\n© ООО Ромашка"
    pages = [
        {"url": f"https://r.ru/{i}", "text": f"{menu}\nУникальный текст {i}"} for i in range(9)
    ]
    corpus, stats = F.build_corpus({"pages": pages}, None, 100_000)
    assert corpus.count("Главная") == 1 and all(f"Уникальный текст {i}" in corpus for i in range(9))
    assert stats["deduped_chars"] < stats["raw_chars"]


def test_prompt_sections_split():
    md = (
        "# T\n## Роль и задача\nроль\n## Часть 1. Профиль\nп\n### Правила\nправ\n"
        "#### Уровень 1. Базовые\n| 1.01 | Обзор вариантов | шаблон |\n#### Уровень 2. X\nx"
    )
    s = F.sections(md)
    assert "| 1.01" in F.find(s, "Уровень 1") and "x" not in F.find(s, "Уровень 1")
    assert F.scenarios(md) == {"1.01": "Обзор вариантов"}


def test_description_only_input(tmp_path):
    d = C.description_input(tmp_path / "shop", "shop.ru", "Магазин цветов в Москве.", "HTTP_DENIED")
    data = C.load_input(d)
    assert data["pages"] == [] and data["material"] == "description_only"
    assert C.guess_language_country(data, None, None) == ("русский", "Россия")
    corpus, stats = F.build_corpus(data, d, 100_000)
    assert corpus == "" and stats["pages_sent"] == 0


def test_duplicate_name_variants_are_merged():
    profile = {
        "name_variants": [
            {"variant": "Ромашка", "type": "official", "source": {"quote": "Ромашка", "url": "u"}},
            {"variant": "ромашка", "type": "short", "source": {"url": "transliteration"}},
        ]
    }
    C.clean_profile(profile, {"u": "Ромашка — цветы"}, "")
    assert [v["variant"] for v in profile["name_variants"]] == ["Ромашка"]


def test_web_brief_keeps_only_facts_from_search_results():
    import step2_web as W

    content = [
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "shop.ru"}},
        {
            "type": "web_search_tool_result",
            "content": [
                {"type": "web_search_result", "url": "https://shop.ru/about", "title": "О нас"}
            ],
        },
        {
            "type": "text",
            "text": '{"activity": [{"fact": "Продаёт цветы", '
            '"quote": "Доставляем цветы по Москве",',
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://www.shop.ru/about/",
                    "cited_text": "Доставляем цветы по Москве за 2 часа.",
                }
            ],
        },
        {
            "type": "text",
            "text": ' "url": "https://shop.ru/about", "source": "сайт компании"},'
            ' {"fact": "Выдумка", "quote": "x", "url": "https://other.ru/",'
            ' "source": "сторонний"}]}',
        },
    ]
    raw, urls, cited = W.parse(content)
    brief = W.verify(json.loads(raw), urls, cited)
    assert [f["fact"] for f in brief["activity"]] == ["Продаёт цветы"]
    assert brief["activity"][0]["quote_checked"] and brief["facts_dropped"] == 1
    block, quotes = W.external_block(brief)
    assert "<external_sources>" in block and "Выдумка" not in block
    assert C.quote_ok("Доставляем цветы по Москве", "https://shop.ru/about", quotes, "")
    brief["name_variants"] = ["Ромашка"]
    assert "Ромашка" in W.external_block(brief)[1]["https://shop.ru/about"]
