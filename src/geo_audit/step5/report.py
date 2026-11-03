"""Self-contained local step 5 viewer: escaped text, no fetches and no external assets."""

import base64
import hashlib
from html import escape

from .models import Answer, IntakeRecord, MechanicalAnswer, PromptSet, Step5Summary, Target

CSS = """
:root{color-scheme:dark;font:16px system-ui,sans-serif;background:#10151e;color:#e9eef7}
*{box-sizing:border-box}body{margin:0}main{max-width:1220px;margin:auto;padding:32px 24px}
h1{font-size:32px;margin:8px 0 16px}h2{font-size:22px;margin-top:32px}p{line-height:1.6}
.muted{color:#b4c0d3}.notice{padding:16px;background:#243149;border-radius:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.tile,.answer{background:#192230;border:1px solid #33435b;border-radius:12px;padding:18px}
.value{font-size:30px;font-weight:700;margin:8px 0}.label{color:#c2cde0;font-size:14px}
.toolbar{display:flex;flex-wrap:wrap;gap:12px;position:sticky;top:0;background:#10151e;
padding:14px 0;z-index:1}input,select{font:inherit;color:inherit;background:#192230;
border:1px solid #647695;border-radius:8px;padding:10px;max-width:100%}
input{flex:1;min-width:200px}label{display:grid;gap:6px;font-size:13px}
#answers{display:grid;gap:14px}.badge{display:inline-block;border:1px solid #647695;
padding:3px 8px;border-radius:20px;font-size:13px;margin:0 6px 6px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.65 system-ui,sans-serif}
code{overflow-wrap:anywhere}mark{background:#ffe08a;color:#1c2637;border-radius:3px}
summary{cursor:pointer;padding:8px 0;color:#d0ddf5}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #33435b;text-align:left;padding:10px;overflow-wrap:anywhere}
.table-wrap{overflow:auto}.answer h3{font-size:18px;margin:6px 0 12px}
.answer[hidden]{display:none}button{font:inherit;padding:10px;border-radius:8px;cursor:pointer}
@media print{.toolbar{display:none}details>pre{display:block}.answer{break-inside:avoid}}
"""

SCRIPT = """
'use strict';
const cards = Array.from(document.querySelectorAll('.answer'));
const search = document.getElementById('search');
const provider = document.getElementById('provider');
const group = document.getElementById('group');
const status = document.getElementById('status');
const signal = document.getElementById('signal');
const indexes = cards.map(card => card.textContent.toLocaleLowerCase());
function update() {
  const query = search.value.toLocaleLowerCase().trim();
  let count = 0;
  cards.forEach((card, i) => {
    const show = (!query || indexes[i].includes(query)) &&
      (!provider.value || card.dataset.provider === provider.value) &&
      (!group.value || card.dataset.group === group.value) &&
      (!status.value || card.dataset.status === status.value) &&
      (!signal.value || card.dataset[signal.value] === 'yes');
    card.hidden = !show;
    if (show) count++;
  });
  document.getElementById('visible').textContent = String(count);
}
[search, provider, group, status, signal].forEach(el => el.addEventListener('input', update));
document.getElementById('reset').addEventListener('click', () => {
  [search, provider, group, status, signal].forEach(el => {el.value = '';});
  update();
});
"""


def escaped(value: object) -> str:
    return escape(str(value), quote=True)


def highlighted(text: str, answer: MechanicalAnswer) -> str:
    """Merge overlapping original spans; never insert unescaped answer HTML."""
    intervals = sorted((c.start, c.end) for c in answer.candidates)
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not 0 <= start < end <= len(text):
            raise ValueError("invalid highlight span")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts: list[str] = []
    position = 0
    for start, end in merged:
        parts.extend((escaped(text[position:start]), "<mark>", escaped(text[start:end]), "</mark>"))
        position = end
    parts.append(escaped(text[position:]))
    return "".join(parts)


def options(values: list[str]) -> str:
    return '<option value="">Все</option>' + "".join(
        f'<option value="{escaped(v)}">{escaped(v)}</option>' for v in values
    )


def render_report(
    prompts: PromptSet,
    target: Target,
    answers: list[MechanicalAnswer],
    intake: list[IntakeRecord],
    summary: Step5Summary,
    originals: dict[str, Answer],
) -> bytes:
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    style_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    policy = (
        f"default-src 'none'; script-src 'sha256-{script_hash}'; "
        f"style-src 'sha256-{style_hash}'; base-uri 'none'; form-action 'none'"
    )
    prompt_map = {p.prompt_id: p for p in prompts.prompts}
    tiles = [
        ("Входных записей", summary.input_lines),
        ("Принято", summary.accepted),
        ("Повторный импорт", summary.duplicates),
        ("Карантин", summary.quarantined),
    ]
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{escaped(policy)}">',
        "<title>Шаг 5 — разбор ответов</title>",
        f"<style>{CSS}</style></head><body><main>",
        '<div class="label">GEO · Шаг 5 · Локальный разбор</div>',
        f"<h1>{escaped(target.name)}: собранные ответы</h1>",
        '<p class="notice">Это механический разбор: совпадение названия ещё не подтверждает '
        "идентичность компании и рекомендацию. Независимая проверка не выполнена. "
        "Ссылки не открывались. Проценты видимости здесь не рассчитываются.</p>",
        "<noscript><p>Поиск и фильтры требуют JavaScript. Все записи доступны ниже без него.</p>"
        "</noscript>",
        '<div class="grid">',
    ]
    parts.extend(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{count}</div></div>'
        for label, count in tiles
    )
    parts.append("</div>")
    if any(a.origin == "synthetic" for a in answers):
        parts.append(
            '<p class="notice">В наборе есть синтетические данные: '
            "это тестовые тексты, не реальные ответы указанных систем.</p>"
        )
    parts.extend(['<h2>Четыре категории оснований</h2><div class="grid">'])
    for key, title in (
        ("observed_fact", "Наблюдаемый факт"),
        ("calculated_estimate", "Расчётная оценка"),
        ("hypothesis", "Гипотеза"),
        ("recommendation", "Рекомендация аудита"),
    ):
        note = (
            "О полученных записях и строках текста" if key == "observed_fact" else "Следующие этапы"
        )
        parts.append(
            f'<div class="tile"><div>{title}</div><div class="value">'
            f'{summary.evidence_categories[key]}</div><div class="label">{note}</div></div>'
        )
    parts.extend(
        [
            "</div><h2>Ответы для просмотра</h2>",
            '<p class="muted">A–D — уровни запросов, отдельно от четырёх категорий оснований. '
            "Пустые ответы, ошибки и обрывы сохранены со своими статусами.</p>",
            '<div class="toolbar"><label>Поиск<input id="search" type="search" '
            'placeholder="Текст, название, ID"></label>',
            f'<label>Система<select id="provider">{options(sorted({a.provider for a in answers}))}'
            "</select></label>",
            f'<label>Уровень<select id="group">{options(list("ABCD"))}</select></label>',
            f'<label>Доставка<select id="status">{options(["ok", "empty", "error", "truncated"])}'
            "</select></label>",
            '<label>Наблюдение<select id="signal"><option value="">Все</option>'
            '<option value="candidate">Есть кандидат названия</option>'
            '<option value="ambiguous">Есть неоднозначное название</option>'
            '<option value="cited">Целевой домен в источниках</option>'
            '</select></label><button id="reset" type="button">Сбросить</button></div>',
            f'<p aria-live="polite">Показано: <span id="visible">{len(answers)}</span> '
            f'из {len(answers)}</p><section id="answers">',
        ]
    )
    for a in answers:
        original = originals[a.answer_id]
        text_html = highlighted(original.text, a) if original.text else "Текст отсутствует"
        ambiguous = any(c.ambiguous for c in a.candidates)
        attrs = {
            "provider": a.provider,
            "group": a.group,
            "status": a.status,
            "candidate": "yes" if a.target_name_candidate else "no",
            "ambiguous": "yes" if ambiguous else "no",
            "cited": "yes" if a.target_domain_in_citations else "no",
        }
        attributes = " ".join(f'data-{k}="{escaped(v)}"' for k, v in attrs.items())
        parts.extend(
            [
                f'<article class="answer" id="{escaped(a.answer_id)}" {attributes}>',
                "<div>"
                + "".join(
                    f'<span class="badge">{escaped(v)}</span>'
                    for v in (a.answer_id, a.group, a.provider, a.status)
                )
                + "</div>",
                f"<h3>{escaped(prompt_map[a.prompt_id].text)}</h3>",
                f'<p class="label">Вопрос {escaped(a.prompt_id)} · Повтор {a.repeat_index} · '
                f"Происхождение: {escaped(a.origin)} · Строка исходника: {a.source_line}</p>",
                "<details><summary>Полный текст ответа и найденные совпадения</summary>",
                f"<pre>{text_html}</pre>",
                "</details>",
            ]
        )
        if a.candidates:
            parts.append("<ul>")
            for c in a.candidates:
                label = "Неоднозначный вариант" if c.ambiguous else "Точное совпадение варианта"
                where = "в URL текста" if c.location == "text_url" else "в тексте"
                parts.append(
                    f"<li>{label}: <code>{escaped(c.quote)}</code> · {where} · "
                    f"позиции {c.start}–{c.end}</li>"
                )
            parts.append("</ul>")
        else:
            parts.append('<p class="muted">Явных совпадений по переданному словарю не найдено.</p>')
        if a.sources:
            parts.append("<details><summary>Источники: " + str(len(a.sources)) + "</summary><ul>")
            for source in a.sources:
                label = "целевой домен" if source.target_host_match else "другой домен"
                parts.append(
                    f"<li><code>{escaped(source.url)}</code><br>"
                    f"{escaped(source.host)} · {label} · "
                    f"{escaped(', '.join(source.origins))} · переход не выполнялся</li>"
                )
            parts.append("</ul></details>")
        if a.invalid_source_count:
            parts.append(f"<p>Пропущено некорректных URL: {a.invalid_source_count}</p>")
        parts.append("</article>")
    parts.append("</section><h2>Журнал приёмки: дубликаты и карантин</h2>")
    exceptions = [r for r in intake if r.outcome != "accepted"]
    if exceptions:
        parts.append(
            '<div class="table-wrap"><table><thead><tr><th>Строка</th><th>ID</th>'
            "<th>Исход</th><th>Причина</th></tr></thead><tbody>"
        )
        for row in exceptions:
            parts.append(
                f"<tr><td>{row.line}</td><td>{escaped(row.answer_id or '—')}</td>"
                f"<td>{escaped(row.outcome)}</td><td>{escaped(row.reason)}</td></tr>"
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append("<p>Дубликатов импорта и карантинных записей нет.</p>")
    if summary.warnings:
        parts.append("<p>Замечания: " + escaped(", ".join(summary.warnings)) + "</p>")
    parts.extend(
        [
            "<h2>Метод и границы</h2><p>Исходные тексты сохранены в input/answers.jsonl. "
            "Подсветка показывает совпадения, а не доказанную рекомендацию. Позиции заданы "
            "в символах Python. Поле synthetic означает вымышленные тестовые данные; "
            "имитации систем не являются их реальными ответами.</p>",
            f'<p class="label">Версия: {escaped(summary.algorithm_version)} · '
            "Сетевых запросов: 0 · Вызовов моделей: 0</p>",
            f"</main><script>{SCRIPT}</script></body></html>",
        ]
    )
    return "".join(parts).encode("utf-8")
