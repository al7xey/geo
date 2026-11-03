"""Escaped, script-free report. Only registered metrics become numeric displays."""

from html import escape
from html.parser import HTMLParser

from ..step9.models import Aggregates, NumberCell, SiteContext
from ..step9.pipeline import number_registry
from ..step10.models import Gaps
from ..step11.models import Findings, Plan
from ..step11.pipeline import CHECK_LABELS

LABELS = {
    "name_match": "Явное совпадение названия",
    "presence": "Подтверждённое присутствие",
    "recommended": "Предложили как вариант",
    "discouraged": "Не советуют",
    "cited": "Есть ссылка на сайт",
}
QUALITY = {
    "not_assessed": "Надёжность не оценена",
    "insufficient_sample": "Малая выборка",
    "medium": "Средняя надёжность",
    "low": "Низкая надёжность",
}
MARKERS = (
    "<|system|>",
    "begin_internal_prompt",
    "requested_fields",
    "api_key",
    "authorization:",
    "bearer ",
    "instruction_version",
)
CSS = """*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#17242e;
font:16px/1.6 system-ui,sans-serif}main{max-width:1140px;margin:auto;padding:36px 24px}
h1{font-size:36px;line-height:1.15}h2{margin-top:42px}h3{margin-bottom:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}
.card,article{background:white;border:1px solid #dce4e5;border-radius:20px;padding:24px;
margin:12px 0;break-inside:avoid}.card strong{display:block;font-size:32px;color:#087c65}
.note{background:#e7eeef;padding:16px;border-radius:12px}.demo{background:#fff0c2}
.muted{color:#56656d}.table{overflow:auto}table{border-collapse:collapse;width:100%;
background:#fff}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce4e5;
vertical-align:top}th{background:#e7eeef}a{color:#087c65}li{margin:8px 0}
@media print{body{background:#fff;font-size:11pt}main{max-width:none;padding:0}
.downloads{display:none}h2{break-after:avoid}thead{display:table-header-group}}
"""


def formatted(cell: NumberCell) -> str:
    if cell.unit == "count":
        return str(int(cell.value))
    return f"{cell.value:.1f}".replace(".", ",") + " %"


class NumberAudit(HTMLParser):
    def __init__(self, registry: dict[str, NumberCell]) -> None:
        super().__init__(convert_charrefs=True)
        self.registry = registry
        self.current: str | None = None
        self.parts: list[str] = []
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "iframe", "object", "embed", "form"}:
            raise ValueError("active report content is forbidden")
        if "data-metric" in values:
            key = values["data-metric"]
            if self.current is not None or key not in self.registry:
                raise ValueError("unknown or nested report number")
            self.current = key
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self.current is not None:
            if "".join(self.parts) != formatted(self.registry[self.current]):
                raise ValueError("report number differs from aggregates")
            self.references.append(self.current)
            self.current = None


def audit_html(html: str, aggregate: Aggregates) -> list[str]:
    if aggregate.numbers != number_registry(aggregate):
        raise ValueError("aggregate numeric registry differs from data")
    if any(marker in html.casefold() for marker in MARKERS):
        raise ValueError("internal markers in visible report")
    parser = NumberAudit(aggregate.numbers)
    parser.feed(html)
    parser.close()
    if parser.current is not None:
        raise ValueError("unclosed numeric display")
    return parser.references


def render(
    aggregate: Aggregates, context: SiteContext, gaps: Gaps, findings: Findings, plan: Plan
) -> tuple[bytes, list[str]]:
    redacted = []

    def text(value: str) -> str:
        if any(marker in value.casefold() for marker in MARKERS):
            redacted.append("service_marker_fragment")
            return "Фрагмент скрыт: обнаружены служебные метки."
        return escape(value, quote=True)

    def number(key: str) -> str:
        cell = aggregate.numbers.get(key)
        return (
            f'<span data-metric="{escape(key, quote=True)}">{formatted(cell)}</span>'
            if cell
            else "Нет данных"
        )

    def bullets(values: list[str]) -> str:
        return "<ul>" + "".join("<li>" + text(v) + "</li>" for v in values) + "</ul>"

    body = [
        "<header><p>GEO-аудит · сохранённые данные</p><h1>"
        + text(aggregate.target.name)
        + "</h1></header>"
    ]
    if aggregate.synthetic:
        body.append(
            '<p class="note demo">Учебные данные. Все ответы вымышлены; это '
            "не аудит настоящей компании.</p>"
        )
    body.append(
        '<p class="note">Независимая проверка и неясные случаи отражены отдельно. '
        "Отчёт не обещает рост упоминаний, трафика или продаж.</p>"
    )
    body.append('<section><h2>Основные показатели</h2><div class="cards">')
    for rate in aggregate.rates:
        if rate.scope != "all-all":
            continue
        interval = (
            number(rate.metric_id + "-low") + " — " + number(rate.metric_id + "-high")
            if rate.interval_low is not None
            else "Недостаточно разных вопросов для оценки разброса"
        )
        body.append(
            '<div class="card">'
            + text(LABELS[rate.metric])
            + "<strong>"
            + number(rate.metric_id)
            + "</strong><p>"
            + interval
            + '</p><p class="muted">'
            + text(QUALITY[rate.reliability])
            + "</p><p>Вопросов с данными: "
            + number(rate.metric_id + "-answered")
            + " / "
            + number(rate.metric_id + "-questions")
            + "; неясных ответов: "
            + number(rate.metric_id + "-unknown")
            + "</p></div>"
        )
    body.append(
        "</div><p>Всего записей: "
        + number("total_answers")
        + "; пригодных: "
        + number("eligible_answers")
        + "; исключённых: "
        + number("excluded_answers")
        + ". Вопросов с ответами: "
        + number("answered_questions")
        + ". Ошибки сбора не считаются отсутствием компании.</p></section>"
    )
    body.append("<section><h2>Главные наблюдения</h2>")
    for finding in findings.items:
        body.append(
            "<article><h3>"
            + text(finding.title)
            + "</h3><p>"
            + text(finding.description)
            + "</p>"
            + " · ".join(number(key) for key in finding.number_ids)
            + "</article>"
        )
    body.append(
        '</section><section><h2>По системам и видам вопросов</h2><div class="table"><table>'
        "<thead><tr><th>Система и "
        "группа</th><th>Показатель</th><th>Доля</th><th>Надёжность</th></t"
        "r></thead><tbody>"
    )
    for rate in aggregate.rates:
        if rate.scope == "all-all":
            continue
        cohort = aggregate.cohorts.get(rate.cohort_id or "")
        label = (cohort.provider + " · " + cohort.model) if cohort else "Все системы"
        label += (" · группа " + rate.group) if rate.group else " · все группы"
        body.append(
            "<tr><td>"
            + text(label)
            + "</td><td>"
            + text(LABELS[rate.metric])
            + "</td><td>"
            + number(rate.metric_id)
            + "</td><td>"
            + text(QUALITY[rate.reliability])
            + "</td></tr>"
        )
    body.append(
        "</tbody></table></div></section><section><h2>Устойчивость по вопросам и системам</h2>"
    )
    for key, label in (
        ("always", "Назвали во всех полученных повторах"),
        ("sometimes", "Назвали в части повторов"),
        ("never", "Не назвали ни в одном"),
        ("uncertain", "Осталась неопределённость"),
    ):
        body.append("<p>" + label + ": " + number("stability-" + key) + "</p>")
    body.append("</section><section><h2>Техническая доступность</h2>")
    if context.status == "not_supplied":
        body.append(
            '<p class="note">Нет данных: сохранённый технический обход сайта не передан.</p>'
        )
    else:
        body.append(
            '<div class="table"><table><thead><tr><th>Проверка</th><th>Статус<'
            "/th><th>Основание</th></tr></thead><tbody>"
        )
        statuses = {
            # Translation of a technical check result, not a credential.
            "pass": "Пройдена",  # nosec B105
            "fail": "Не пройдена",
            "warning": "Предупреждение",
            "unknown": "Нет данных",
            "not_applicable": "Не применима",
        }
        for check in context.checks:
            body.append(
                "<tr><td>"
                + text(CHECK_LABELS[check.kind])
                + "</td><td>"
                + statuses[check.status]
                + "</td><td>"
                + text(check.observation)
                + "</td></tr>"
            )
        body.append("</tbody></table></div>")
    body.append(
        "</section><section><h2>Другие названия и источники</h2><p>"
        "Названия ниже — кандидаты, а не подтверждённый полный список конкурентов.</p>"
    )
    if not aggregate.competitors:
        body.append("<p>Нет данных: другие названия не извлечены структурными правилами.</p>")
    for entity in sorted(aggregate.competitors, key=lambda e: (-len(e.answer_ids), e.name))[:10]:
        body.append(
            "<p>"
            + text(entity.name)
            + ": ответов — "
            + number(entity.entity_id + "-answers")
            + "</p>"
        )
    if not aggregate.sources:
        body.append("<p>Нет данных: источники не переданы или не распознаны.</p>")
    from ..step5.pipeline import digest

    for source in sorted(aggregate.sources, key=lambda s: (-len(s.answer_ids), s.host))[:10]:
        body.append(
            "<p>"
            + text(source.host)
            + ": цитирующих ответов — "
            + number("host-" + digest(source.host.encode())[:24])
            + " · "
            + text(source.kind)
            + "</p>"
        )
    if aggregate.source_inspection:
        body.append(
            "<h3>Явная проверка внешних страниц</h3><p>Прочитано страниц: "
            + number("source-pages-observed")
            + "</p>"
        )
        for observation in aggregate.source_inspection.observations:
            state = (
                "Нет данных: страница не прочитана."
                if observation.status != "observed"
                else "Есть буквальное совпадение названия."
                if observation.matches
                else "В сохранённом фрагменте буквальное название не найдено."
            )
            body.append("<p>" + text(observation.url) + " — " + state + "</p>")
            if observation.other_matches:
                body.append(
                    "<p>Буквально найдены другие названия: "
                    + text(", ".join(sorted({m.name for m in observation.other_matches})))
                    + "</p>"
                )
        body.append(
            '<p class="note">Совпадение названия не подтверждает принадлежность компании. '
            "Ссылка в ответе не доказывает чтение страницы моделью.</p>"
        )
    else:
        body.append(
            '<p class="note">Внешние страницы не посещались. '
            "Ссылка в ответе не доказывает чтение страницы моделью.</p>"
        )
    body.append("</section>")
    body.append("<section><h2>Темы и план действий</h2>")
    if not gaps.topics:
        body.append(
            "<p>Нет тем, прошедших условия отбора. Это не доказательство "
            "полноты содержания сайта.</p>"
        )
    for period, title in (
        ("week1", "Неделя 1 — препятствия и проверка данных"),
        ("weeks2_4", "Недели 2–4 — содержание"),
        ("later", "Дальше — внешние источники"),
    ):
        body.append("<h3>" + title + "</h3>")
        selected = [a for a in plan.actions if a.period == period]
        if not selected:
            body.append("<p>Нет действий с достаточным основанием для этого периода.</p>")
        for action in selected:
            body.append(
                "<article><h3>"
                + text(action.title)
                + "</h3><p>"
                + text(action.instruction)
                + "</p><p>Как проверим: "
                + text(action.verify)
                + '</p><p class="muted">'
                + text(action.limitation)
                + "</p>"
            )
            if action.material:
                body.append(
                    "<h4>Заготовка — требуется проверка человеком</h4><p>"
                    + text(action.material.title)
                    + "</p>"
                )
                body.append(bullets([item.text for item in action.material.outline]))
                if action.material.quote:
                    body.append("<blockquote>" + text(action.material.quote) + "</blockquote>")
            else:
                body.append(
                    "<p>Готового материала нет: содержательная подготовка ожидает проверки.</p>"
                )
            body.append("</article>")
    body.append(
        "</section><section><h2>Ограничения</h2>"
        + bullets(aggregate.warnings + plan.limitations)
        + "</section>"
    )
    body.append(
        '<p class="downloads"><a href="aggregates.json">Расчёты</a> · '
        '<a href="findings.json">Находки</a> · <a href="plan.json">План</a> · '
        '<a href="report_data.json">Данные отчёта</a></p>'
    )
    html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>GEO — отчёт</title><style>"
        + CSS
        + "</style></head><body><main>"
        + "".join(body)
        + "</main></body></html>"
    )
    audit_html(html, aggregate)
    return html.encode(), redacted
