# ruff: noqa: E501
"""Шаги 0→1→2 одной командой.

  uv run --locked --no-dev python tools/geo_pipeline.py https://example.ru [--country Россия]

Параллельно с шагами 0–1 идёт веб-поиск о компании (Haiku 5.5, ~$0.05; --no-web отключает).
Если сайт закрыт для обхода (антибот, 403) или почти пуст для робота (JS-приложение),
шаг 2 строится по веб-поиску и описанию (--description / --description-file), если оно есть.
Описание спрашивается, только когда поиск ничего не нашёл. Запросы всегда на русском;
рынок — из --country, по домену (.ru → Россия) или Россия по умолчанию.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import step2_fast  # noqa: E402
from step2_checks import (  # noqa: E402
    DEFAULT_COUNTRY,
    TLD_COUNTRY,
    description_input,
    guess_language_country,
    load_input,
)
from step2_web import facts, web_brief  # noqa: E402


def audit(url: str, description: str | None, pages: int) -> dict:
    """Шаги 0–1 тем же кодом, что и `geo-audit audit`, но в этом же процессе."""
    from geo_audit.config import Step0Config
    from geo_audit.step1.config import Step1Config
    from geo_audit.step1.models import Step1Result
    from geo_audit.step1.pipeline import audit_site

    # Профиль пайплайна: зависшая страница стоит 6 с, а не 15; из sitemap — первые 1000 URL,
    # этого хватает для выбора 20 страниц по ролям. Умолчания `geo-audit audit` не меняются.
    step0 = Step0Config(read_timeout_seconds=6.0)
    step1 = Step1Config(
        source_step0_config_sha256=step0.fingerprint(), max_selected=pages, max_sitemap_urls=1000
    )
    out = Path("private/runs")
    r = asyncio.run(
        audit_site(url, config=step0, step1_config=step1, output_dir=out, description=description)
    )
    if isinstance(r, Step1Result):
        return {
            "status": r.run.status,
            "can_start_step2": r.crawl.material.can_start_step2,
            "material": r.crawl.material.state,
            "coverage": r.crawl.coverage.model_dump(mode="json"),
            "result_directory": str(out / r.run.publication_id),
        }
    return {"status": r.status, "phase": "step0", "reason_codes": list(r.reason_codes)}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("url")
    ap.add_argument("--country")
    ap.add_argument("--language")
    ap.add_argument("--preset", choices=step2_fast.PRESETS, default="quality")
    ap.add_argument(
        "--pages", type=int, default=20, help="сколько страниц читать (1–30, по умолчанию 20)"
    )
    ap.add_argument("--description", help="описание компании, если сайта не хватает")
    ap.add_argument("--description-file", type=Path)
    ap.add_argument("--no-web", action="store_true", help="без справки из веб-поиска")
    a = ap.parse_args()
    sys.exit(asyncio.run(pipeline(a)))


async def pipeline(a: argparse.Namespace) -> int:
    given = a.description or (
        a.description_file.read_text(encoding="utf-8").strip() if a.description_file else None
    )
    domain = httpx.URL(a.url if "://" in a.url else "https://" + a.url).host.removeprefix("www.")

    def describe(why: str) -> str:
        if given:
            return given
        if not sys.stdin.isatty():
            sys.exit(f"{why} Нужно описание компании: --description или --description-file.")
        return input(f"{why} Опишите компанию в 2–3 предложениях: ").strip()

    t0 = time.monotonic()
    # Справке нужен только домен и страна: она идёт, пока код читает сайт.
    early_country = a.country or TLD_COUNTRY.get(domain.rsplit(".", 1)[-1], DEFAULT_COUNTRY)
    web = None
    if not a.no_web and early_country:
        print(f"Веб-поиск о {domain} (параллельно с шагами 0–1)…")
        web = asyncio.create_task(web_brief(domain, early_country))
    print("Шаги 0–1: проверка сайта и чтение страниц…")
    res = await asyncio.to_thread(audit, a.url, None, a.pages)
    t01 = time.monotonic() - t0
    brief, web_call = await web if web else (None, None)
    known = bool(facts(brief))
    thin = res.get("status") == "needs_input" or (
        res.get("phase") != "step0" and not res.get("can_start_step2")
    )
    if res.get("phase") == "step0" or (thin and known and not given):
        # Сайт закрыт или почти пуст для робота — тот же барьер видят ИИ-краулеры (вывод аудита).
        reasons = ", ".join(res.get("reason_codes") or [str(res.get("material"))])
        print(f"  шаги 0–1: {t01:.0f} с, текста сайта недостаточно ({reasons})")
        run_dir = description_input(
            Path("private/step2_input") / f"{domain}-{int(time.time())}",
            domain,
            given or ("" if known else describe("Сайт закрыт для автоматического обхода.")),
            reasons,
        )
        print(
            "  шаг 2 строится без текстов сайта: "
            + ", ".join(x for x, ok in (("веб-поиск", known), ("описание", given)) if ok)
        )
    elif thin:
        res = await asyncio.to_thread(audit, a.url, describe("Текста на сайте мало."), a.pages)
        t01 = time.monotonic() - t0
        if not res.get("can_start_step2"):
            print(f"Анализ остановлен: {res.get('status')} {res.get('material')}")
            return 1
        run_dir = Path(res["result_directory"])
    else:
        run_dir = Path(res["result_directory"])
        cov = res.get("coverage", {})
        print(f"  шаги 0–1: {t01:.0f} с")
        print(
            f"  статус {res['status']}, полезных страниц {cov.get('useful_pages')}, папка {run_dir}"
        )

    data = load_input(run_dir)
    language, country = guess_language_country(data, a.language, a.country)
    if (not country or not language) and not sys.stdin.isatty():
        sys.exit(
            f"Не определены страна ({country}) или язык ({language}): укажите --country/--language."
        )
    if not country:
        country = input("Для какой страны составлять запросы (например, Россия)? ").strip()
    if not language:
        language = input("На каком языке составлять запросы (например, русский)? ").strip()

    print("Шаг 2: профиль и 40 запросов…")
    step2_out = Path("private/step2_fast") / run_dir.name
    args = argparse.Namespace(
        run_dir=run_dir,
        prompt=Path("private/prompts/prompt_step2_profile_and_queries.md"),
        language=language,
        country=country,
        preset=a.preset,
        budget=100_000,
        out=step2_out,
        dry_run=False,
        external=brief,
        external_call=web_call,
        web=not a.no_web and web is None,  # страну узнали только сейчас — поиск внутри шага 2
    )
    code = await step2_fast.main_async(args)
    print(f"Всего шаги 0–2: {time.monotonic() - t0:.0f} с (шаги 0–1: {t01:.0f} с)")
    if code == 1:
        print("Шаг 2 не дал запросов, подробности в step2_report.json")
    else:
        print(f"Шаг 3 — проверить вопросы: python tools/step3_review.py {step2_out}")
    return code


if __name__ == "__main__":
    main()
