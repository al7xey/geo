"""Static, escaped, local-only step 8 viewer."""

from html import escape

from .models import DomainGroup, EntityGroup, EntityJob, Step8Summary


def render_report(
    summary: Step8Summary,
    entities: list[EntityGroup],
    domains: list[DomainGroup],
    jobs: list[EntityJob],
) -> bytes:
    def cell(value: object) -> str:
        return "<td>" + escape(str(value)) + "</td>"

    labels = {
        "target": "Целевая компания",
        "non_target_candidate": "Кандидат другого бренда",
        "unresolved": "Идентичность не определена",
    }
    brand_rows = "".join(
        "<tr>"
        + "".join(
            cell(v)
            for v in (
                e.canonical_name,
                labels[e.identity_scope],
                ", ".join(e.variants),
                len(e.answer_ids),
                len(e.eligible_answer_ids),
                e.merge_basis,
            )
        )
        + "</tr>"
        for e in entities
    )
    domain_rows = "".join(
        "<tr>"
        + "".join(
            cell(v)
            for v in (
                d.host,
                d.kind,
                d.owner,
                len(d.answer_ids),
                len(d.eligible_citation_answer_ids),
                d.reason or "Нет подтверждающих данных",
            )
        )
        + "</tr>"
        for d in domains
    )
    job_rows = "".join(
        "<tr>"
        + "".join(
            cell(v)
            for v in (
                j.task_type,
                j.host or " / ".join(j.names),
                j.reason,
                j.status,
            )
        )
        + "</tr>"
        for j in jobs
    )
    return (
        """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Шаг 8 — названия и источники</title><style>
body{font:16px system-ui;margin:32px;background:#10151d;color:#e3eaf2}
main{max-width:1400px;margin:auto}table{border-collapse:collapse;width:100%;margin:24px 0}
td,th{text-align:left;padding:10px;border:1px solid #354353;overflow-wrap:anywhere}
th{background:#223041}p{line-height:1.6}.notice{padding:16px;background:#28384b}
</style><main><h1>Шаг 8. Названия брендов и типы источников</h1>"""
        + f"<p>Ответов: {summary.source_answers}. Наблюдений брендов: {summary.occurrences}. "
        + f"Групп: {summary.entity_groups}. Доменов: {summary.source_hosts}.</p>"
        + "<p class='notice'>Данные наследуют происхождение исходных ответов: мок-ответы остаются "
        "синтетическими. Кандидат имени не доказывает существование конкурента. "
        "API отключён; сайты не открывались. Проценты видимости рассчитываются на шаге 9.</p>"
        + f"<p>Неразрешённых групп: {summary.unresolved_identity_groups}; типов источников: "
        + f"{summary.unknown_source_types}. Заданий на будущее: {len(jobs)}; отложено доменов: "
        + f"{summary.domains_deferred}. Статус: {escape(summary.status)}.</p>"
        + "<p>Похожие названия и транслитерация автоматически не объединяются. "
        "Поиск похожих пар ограничен бюджетом и префиксом; это не полный поиск алиасов. "
        "Идентичность неоднозначных упоминаний ожидает проверки шага 7.</p>"
        + "<h2>Группы названий</h2><table><tr><th>Название</th><th>Идентичность</th>"
        "<th>Наблюдаемые варианты</th><th>Ответов</th><th>Пригодных</th><th>Основание</th></tr>"
        + brand_rows
        + "</table><h2>Источники по точному hostname</h2><table><tr><th>Домен</th>"
        "<th>Тип</th><th>Владелец</th><th>Ответов</th>"
        "<th>С цитированием</th><th>Основание</th></tr>"
        + domain_rows
        + "</table><h2>Ожидающие задания</h2><table><tr><th>Задача</th>"
        "<th>Объект</th><th>Причина</th><th>Статус</th></tr>"
        + job_rows
        + "</table><p>Наблюдаемые факты: исходные имена, цитаты и URL. Карта — производная "
        "нормализация с указанным основанием; гипотезы причин и рекомендации аудита здесь "
        "не создаются. Решения моделей не имитируются.</p></main></html>"
    ).encode("utf-8")
