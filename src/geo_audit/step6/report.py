"""Local rule annotation viewer; no percentages or model verification are fabricated."""

import base64
import hashlib

from ..step5.models import Answer, PromptSet, Target
from ..step5.report import CSS, SCRIPT, escaped, options
from .models import Annotation, Step6Summary

LABELS = {"yes": "да", "no": "нет", "uncertain": "не определено", None: "нет пригодного ответа"}
STATUS_LABELS = {
    "rules_complete": "Разобрано правилами",
    "pending_model": "Ожидает модель",
    "not_eligible": "Непригодный ответ",
}
ROLE_LABELS = {
    "recommended": "Рекомендует",
    "discouraged": "Не рекомендует",
    "mentioned": "Упоминает",
}
REASON_LABELS = {
    "ambiguous_alias": "Неоднозначное сокращение или имя",
    "quoted_or_reported_advice": "Чужая цитата, условный или косвенный совет",
    "interrogative_advice": "Вопрос вместо утверждения",
    "negation_scope": "Сложное отрицание",
    "unresolved_role_language": "Совет нельзя уверенно связать с компанией",
    "unresolved_pronoun": "Неясная ссылка через местоимение",
    "implicit_target_identity": "Неявное название целевой компании",
    "conflicting_ranks": "Разные ранги в одном ответе",
    "unresolved_segment": "Не удалось надёжно выделить контекст",
    "rule_baseline_only": "Решение локальных правил; независимой проверки пока нет",
    "delivery_not_usable": "Нет полного пригодного текста",
    "explicit_rule_baseline": "Найдена явная конструкция",
}


def reason_labels(codes: list[str]) -> str:
    return "; ".join(REASON_LABELS.get(code, code) for code in codes)


def render_report(
    target: Target,
    prompts: PromptSet,
    annotations: list[Annotation],
    summary: Step6Summary,
    originals: dict[str, Answer],
) -> bytes:
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    style_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    csp = (
        f"default-src 'none'; script-src 'sha256-{script_hash}'; "
        f"style-src 'sha256-{style_hash}'; base-uri 'none'; form-action 'none'"
    )
    queries = {p.prompt_id: p.text for p in prompts.prompts}
    providers = options(sorted({a.provider for a in annotations}))
    statuses = '<option value="">Все</option>' + "".join(
        f'<option value="{code}">{label}</option>' for code, label in STATUS_LABELS.items()
    )
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{escaped(csp)}">',
        f"<title>Шаг 6 — разметка ответов</title><style>{CSS}</style></head><body><main>",
        '<div class="label">GEO · Шаг 6 · Предварительная разметка правилами</div>',
        f"<h1>{escaped(target.name)}: присутствие и роль в ответах</h1>",
        '<p class="notice">API отключён. Здесь решения локальных правил, а не заключения '
        "проверяющей модели. Неоднозначные случаи ожидают разметки; независимая проверка "
        "шага 7 не выполнялась. Доли и оценки видимости появятся на этапе подсчёта.</p>",
        "<p>«Рекомендуют» ниже означает роль компании в чужом ответе, "
        "а не рекомендацию самого аудита. Цитата доказывает наличие фразы, "
        "но не достоверность утверждения о компании.</p>",
    ]
    if any(a.origin == "synthetic" for a in annotations):
        parts.append(
            '<p class="notice">Синтетические мок-данные. Это вымышленные ответы, '
            "не результаты реальных систем.</p>"
        )
    parts.append('<div class="grid">')
    for label, count in (
        ("Записей", summary.input_answers),
        ("Разобрано правилами", summary.rules_complete),
        ("Ожидают модель", summary.pending_model),
        ("Непригодная доставка", summary.not_eligible),
    ):
        parts.append(f'<div class="tile"><div>{label}</div><div class="value">{count}</div></div>')
    parts.append("</div>")
    if summary.pending_reasons:
        parts.append("<details><summary>Почему требуется модель</summary><ul>")
        for code, count in sorted(summary.pending_reasons.items()):
            parts.append(f"<li>{escaped(REASON_LABELS.get(code, code))}: {count}</li>")
        parts.append(
            '</ul><p class="label">В одном ответе может быть несколько причин; '
            "это не число дополнительных запросов.</p></details>"
        )
    parts.extend(
        [
            "<h2>Разметка каждого ответа</h2>",
            '<div class="toolbar"><label>Поиск<input id="search" type="search" '
            'placeholder="Текст, название, ID"></label>',
            f'<label>Система<select id="provider">{providers}</select></label>',
            f'<label>Уровень<select id="group">{options(list("ABCD"))}</select></label>',
            f'<label>Разметка<select id="status">{statuses}</select></label>',
            '<label>Сигнал<select id="signal"><option value="">Все</option>'
            '<option value="candidate">Правила: компанию назвали</option>'
            '<option value="ambiguous">Нужна модель</option>'
            '<option value="cited">Целевой домен в источниках</option>'
            '<option value="recommended">Рекомендуют целевую компанию</option>'
            '<option value="discouraged">Не рекомендуют целевую компанию</option>'
            '<option value="mixed">Есть обе роли для разных сценариев</option></select></label>',
            '<button id="reset" type="button">Сбросить</button></div>',
            f'<p aria-live="polite">Показано: <span id="visible">{len(annotations)}</span> '
            f'из {len(annotations)}</p><section id="answers">',
        ]
    )
    for a in annotations:
        text_html = escaped(originals[a.answer_id].text) or "Текст отсутствует"
        attrs = {
            "provider": a.provider,
            "group": a.group,
            "status": a.status,
            "candidate": "yes" if a.target_presence == "yes" else "no",
            "ambiguous": "yes" if a.needs_model else "no",
            "cited": "yes" if a.target_cited == "yes" else "no",
            "recommended": "yes" if a.target_recommended == "yes" else "no",
            "discouraged": "yes" if a.target_discouraged == "yes" else "no",
            "mixed": "yes" if a.target_recommended == a.target_discouraged == "yes" else "no",
        }
        attributes = " ".join(f'data-{k}="{escaped(v)}"' for k, v in attrs.items())
        parts.extend(
            [
                f'<article class="answer" id="{escaped(a.answer_id)}" {attributes}>',
                "<div>"
                + "".join(
                    f'<span class="badge">{escaped(v)}</span>'
                    for v in (
                        a.answer_id,
                        a.group,
                        a.provider,
                        STATUS_LABELS[a.status],
                    )
                )
                + "</div>",
                f"<h3>{escaped(queries[a.prompt_id])}</h3>",
                f"<p>По правилам: назвали компанию — <b>{LABELS[a.target_presence]}</b>; "
                f"рекомендуют — <b>{LABELS[a.target_recommended]}</b>; "
                f"не рекомендуют — <b>{LABELS[a.target_discouraged]}</b>.</p>",
                f'<p class="label">Доставка: {escaped(a.delivery_status)} · '
                f"Строка исходника: {a.source_line} · Вид: {escaped(a.answer_kind or '—')} · "
                "Релевантность не оценена</p>",
            ]
        )
        for brand in a.brands:
            parts.append(f"<details><summary>{escaped(brand.name)} · роль и цитаты</summary>")
            basis = (
                "Вариант из словаря целевой компании"
                if brand.identity_basis == "target_dictionary"
                else "Кандидат названия из списка/таблицы; сущность не подтверждена"
            )
            parts.append(f'<p class="label">{basis}</p>')
            parts.append(
                f"<p>Целевая компания: {LABELS[brand.is_target]}; "
                f"рекомендуют: {LABELS[brand.recommended]}; "
                f"не рекомендуют: {LABELS[brand.discouraged]}; "
                f"явный ранг: {brand.rank if brand.rank is not None else 'не определён'}</p>"
            )
            for claim in brand.roles:
                if claim.context_evidence:
                    parts.append(
                        "<p>Основание контекста — заголовок:</p>"
                        f"<pre>{escaped(claim.context_evidence.quote)}</pre>"
                    )
                parts.append(
                    f"<p><b>{ROLE_LABELS[claim.role]}</b> · {escaped(claim.evidence.rule)}</p>"
                    f"<pre>{escaped(claim.evidence.quote)}</pre>"
                )
            if brand.rank_context_evidence:
                parts.append(
                    "<p>Заголовок, подтверждающий ранжирование:</p>"
                    f"<pre>{escaped(brand.rank_context_evidence.quote)}</pre>"
                )
            parts.append(f'<p class="label">{escaped(reason_labels(brand.reason_codes))}</p>')
            parts.append("</details>")
        parts.extend(
            [
                "<details><summary>Полный исходный ответ</summary>",
                f"<pre>{text_html}</pre></details>",
                f'<p class="label">{escaped(reason_labels(a.reason_codes))}</p></article>',
            ]
        )
    parts.extend(
        [
            "</section><h2>Границы результата</h2><p>Цитаты проверены в оригинальных текстах. "
            "Сокращения и неявные советы могут оставаться uncertain. Конкуренты извлекаются "
            "только как кандидаты из списков и таблиц; полнота не гарантирована. "
            "Ошибки доставки не превращаются в отсутствие компании. "
            "Файлы semantic_jobs.jsonl и executor.json содержат очередь "
            "и статус отключённой модели.</p>",
            f'<p class="label">{escaped(summary.algorithm_version)} · Сетевых запросов: 0 · '
            "Вызовов моделей: 0</p>",
            f"</main><script>{SCRIPT}</script></body></html>",
        ]
    )
    return "".join(parts).encode("utf-8")
