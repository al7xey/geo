"""Local review readiness and agreement viewer, without fake completed audits."""

import base64
import hashlib

from ..step5.report import CSS, escaped
from ..step6.models import Annotation
from .models import BlindJob, ReviewPolicy, SelectedAnswer, Step7Summary


def render_report(
    summary: Step7Summary,
    selection: list[SelectedAnswer],
    jobs: list[BlindJob],
    policy: ReviewPolicy,
    *,
    effective: list[dict[str, object]] | None = None,
    annotations: list[Annotation] | None = None,
    intake: list[dict[str, object]] | None = None,
) -> bytes:
    style_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    csp = (
        f"default-src 'none'; style-src 'sha256-{style_hash}'; base-uri 'none'; form-action 'none'"
    )
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{escaped(csp)}">',
        f"<title>Шаг 7 — независимая проверка</title><style>{CSS}</style></head><body><main>",
        '<div class="label">GEO · Шаг 7 · Независимая выборочная проверка</div>',
        "<h1>Готовность проверки</h1>",
        '<p class="notice">API отключён. Программа не вызывала проверяющую модель. '
        "Без импортированных результатов нет процента согласия и оценки надёжности. "
        "Импортированные сведения об исполнителе принимаются как заявленные, "
        "их подлинность программа не подтверждает.</p>",
        '<div class="grid">',
    ]
    for title, count in (
        ("Пригодных ответов", summary.eligible_answers),
        ("Случайные 5%", summary.random_sample_count),
        ("Неопределённых случаев", summary.uncertain_count),
        ("Уникальных заданий", summary.selected_count),
        ("Ожидают результатов", summary.pending_jobs),
        ("Уточнено полей", summary.resolved_fields),
        ("Остаются неопределёнными", summary.unresolved_answers),
        ("Уверенные решения расходятся", summary.conflicting_answers),
    ):
        parts.append(f'<div class="tile"><div>{title}</div><div class="value">{count}</div></div>')
    parts.append(
        "</div><p>Пересечений двух выборок: "
        + str(summary.overlap_count)
        + ". На каждый выбранный ответ сформировано одно слепое задание.</p>"
    )
    if summary.synthetic_input_answers:
        parts.append(
            f'<p class="notice">Синтетических входных записей: '
            f"{summary.synthetic_input_answers}. Это тестовые данные, не реальный аудит.</p>"
        )
    if summary.quarantined_rows or summary.error_results:
        parts.append(
            '<p class="notice">Результат частичный: отклонённых строк '
            f"{summary.quarantined_rows}, ошибок проверяющего {summary.error_results}. "
            "Они не превращены в отрицательные ответы. Причины — в quality.json "
            "и quarantine.jsonl.</p>"
        )
    if summary.model_reviewer:
        reviewer = summary.model_reviewer
        parts.append(
            "<p>Заявленный проверяющий: "
            f"{escaped(reviewer.provider)} / {escaped(reviewer.model)} / "
            f"{escaped(reviewer.family)}. Подлинность внешнего вызова не подтверждена.</p>"
        )
    parts.append('<h2>Согласие на случайной выборке</h2><div class="grid">')
    labels = {
        "identity": "Идентичность компании",
        "target_roles": "Роли целевой компании",
        "competitor_roles": "Роли кандидатов-конкурентов",
    }
    reliability = {
        "not_assessed": "Не оценена",
        "insufficient_sample": "Недостаточно сравнимых данных",
        "medium": "Средняя",
        "low": "Низкая — обнаружены расхождения",
    }
    for key, metric in summary.metrics.items():
        percent = (
            f"{metric.agreement_percent:.1f}%"
            if metric.agreement_percent is not None
            else "Нет данных"
        )
        parts.append(
            f'<div class="tile"><div>{labels[key]}</div><div class="value">{percent}</div>'
            f"<p>Совпало {metric.matches} из {metric.comparable} сравнимых; "
            f"из разных ответов: {metric.comparable_answers}; "
            f"назначено сравнений {metric.assigned_comparisons}.</p>"
            f"<p>Надёжность: {reliability[metric.reliability]}</p></div>"
        )
    parts.append(
        '</div><p class="muted">Проценты здесь относятся к согласию разметчиков, '
        "не к присутствию компании. В них входят только случайная часть и "
        "импортированные результаты с заявленным kind=model. Неопределённые пары "
        "не объявляются совпавшими. Согласие не доказывает истинность.</p>"
    )
    low = [labels[name] for name, value in summary.metrics.items() if value.reliability == "low"]
    if low:
        parts.append(
            '<p class="notice">Предупреждение: согласие ниже '
            f"{policy.agreement_threshold_percent}% для показателей: "
            f"{escaped(', '.join(low))}. Надёжность низкая. "
            "Первичные уверенные решения автоматически не исправлялись.</p>"
        )
    parts.append(
        f"<p>Порог: {policy.agreement_threshold_percent}%; минимум сравнимых: "
        f"{policy.minimum_comparable}; минимальное покрытие: "
        f"{policy.minimum_comparison_coverage * 100:.0f}%. Это настройки политики, "
        "не гарантия статистической точности.</p>"
    )
    parts.append(
        '<p class="muted">Минимум разных ответов: '
        f"{policy.minimum_distinct_answers or policy.minimum_comparable}. "
        "Несколько брендов в одном ответе не увеличивают это число.</p>"
    )
    parts.append(
        "<h2>Что передавать проверяющему</h2><p>Только blind_jobs.jsonl и схему "
        "содержательного результата. Файлы selection.json, annotations и эту страницу "
        "проверяющему не передавайте: они содержат внутреннюю информацию.</p>"
    )
    parts.append(
        "<p>Случайная выборка не исправляет уверенные решения. "
        "Уточняются только первичные uncertain; спорные уверенные значения "
        "сохраняются и отмечаются отдельно. Ранги не входят в эти две задачи.</p>"
    )
    parts.append("<h2>Назначенные задания — внутренний просмотр</h2>")
    job_map = {job.job_id: job for job in jobs}
    row_map = {str(row["answer_id"]): row for row in effective or []}
    primary_map = {row.answer_id: row for row in annotations or []}
    field_labels = {
        "target_presence": "Идентичность компании",
        "target_recommended": "Рекомендуют",
        "target_discouraged": "Не рекомендуют",
    }
    decisions = {"yes": "да", "no": "нет", "uncertain": "не определено", "None": "нет данных"}
    sources = {
        "primary_rules": "Правила шага 6",
        "imported_model": "Импорт модели",
        "imported_manual": "Ручная проверка",
    }
    for item in selection:
        job = job_map[item.job_id]
        preview = job.text[:2048]
        question = job.prompt_text[:512]
        why = []
        if item.random_sample:
            why.append("случайная выборка")
        if item.uncertain_case:
            why.append("неопределённый случай")
        parts.append(
            f'<details class="answer"><summary>{escaped(item.answer_id)} · '
            f"{escaped(', '.join(why))}</summary><p>{escaped(question)}</p>"
            f'<pre>{escaped(preview)}</pre><p class="label">{job.job_id}</p>'
        )
        if len(preview) < len(job.text) or len(question) < len(job.prompt_text):
            parts.append(
                '<p class="label">Сокращён только просмотр. Полные тексты сохранены '
                "в blind_jobs.jsonl и входных снимках.</p>"
            )
        row, primary = row_map.get(item.answer_id), primary_map.get(item.answer_id)
        if row is not None and primary is not None:
            parts.append(
                '<div class="table-wrap"><table><thead><tr><th>Поле</th>'
                "<th>Было</th><th>Стало</th><th>Основание</th></tr></thead><tbody>"
            )
            provenance = row["field_sources"]
            for field, title in field_labels.items():
                source = provenance.get(field) if isinstance(provenance, dict) else "primary_rules"
                before = decisions.get(str(getattr(primary, field)), str(getattr(primary, field)))
                after = decisions.get(str(row[field]), str(row[field]))
                source_label = sources.get(str(source), str(source))
                parts.append(
                    f"<tr><td>{title}</td><td>{escaped(before)}</td>"
                    f"<td>{escaped(after)}</td><td>{escaped(source_label)}</td></tr>"
                )
            parts.append("</tbody></table></div>")
            if row["confident_disagreements"] or row["brand_disagreements"]:
                parts.append(
                    '<p class="notice">Есть расхождения с уверенной первичной разметкой. '
                    "Её значения сохранены; подробности — в resolved_annotations.jsonl.</p>"
                )
            if row["outstanding_fields"]:
                remaining = row["outstanding_fields"]
                remaining_count = len(remaining) if isinstance(remaining, list) else 0
                parts.append(
                    f'<p class="label">Осталось неопределённых полей: {remaining_count}.</p>'
                )
        parts.append("</details>")
    invalid = [row for row in intake or [] if row["outcome"] == "quarantined"]
    if invalid:
        parts.append(
            '<h2>Отклонённые строки импорта</h2><div class="table-wrap"><table>'
            "<tr><th>Строка</th><th>Задание</th><th>Причина</th></tr>"
        )
        for row in invalid:
            parts.append(
                f"<tr><td>{escaped(row['line'])}</td><td>{escaped(row['job_id'])}</td>"
                f"<td>{escaped(row['reason'])}</td></tr>"
            )
        parts.append("</table></div>")
    parts.append(
        '<p class="label">Сетевых запросов: 0 · Вызовов моделей: 0 · '
        f"Заявленных модельных импортов: {summary.model_reviews} · "
        f"Ручных: {summary.manual_reviews} · Синтетических: {summary.synthetic_reviews}</p>"
    )
    parts.append("</main></body></html>")
    return "".join(parts).encode("utf-8")
