"""Fixed rules reference exact observations; no claims that a change causes growth."""

from collections import defaultdict
from pathlib import Path

from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_METADATA_BYTES, digest, encoded
from ..step9 import storage
from ..step9.models import Aggregates, SiteContext
from ..step9.pipeline import json_model
from ..step10.models import Gaps
from ..step10.pipeline import verify_publication as verify_step10
from .models import Action, Basis, Finding, Findings, Plan

SOURCE_FILES = ("aggregates.json", "context.json", "gaps.json")
INPUT_FILES = (*SOURCE_FILES, "source-manifest.json")
PERIODS = {"week1": 0, "weeks2_4": 1, "later": 2}
CHECK_LABELS = {
    "robots_policy": "доступ роботов",
    "indexing_directives": "индексация",
    "snippet_directives": "показ фрагментов",
    "canonical_signal": "основной адрес страницы",
    "observed_access": "доступность страницы",
    "https_transport": "защищённое соединение",
    "sitemap": "карта сайта",
    "javascript": "текст без скриптов",
    "ua_comparison": "ответы разным роботам",
}


def identifier(prefix: str, key: str) -> str:
    return prefix + "-" + digest(key.encode())[:24]


def validate_basis(basis: Basis, aggregate: Aggregates, context: SiteContext, gaps: Gaps) -> None:
    known = {
        "aggregate": {"review_metrics", "eligible_answers", "total_answers", *aggregate.numbers},
        "question": {q.prompt_id for q in aggregate.questions},
        "check": {c.check_id for c in context.checks},
        "gap": {t.topic_id for t in gaps.topics},
        "source": {s.host for s in aggregate.sources},
        "context": {"site_context_status"},
    }
    if basis.key not in known[basis.source]:
        raise ValueError("finding or plan has no source basis")


def build_artifacts(inputs: dict[str, bytes]) -> dict[str, bytes]:
    if set(inputs) != set(INPUT_FILES):
        raise ValueError("unexpected planning inputs")
    storage.bind(inputs, 10, SOURCE_FILES)
    aggregate = json_model(inputs["aggregates.json"], Aggregates)
    context = json_model(inputs["context.json"], SiteContext)
    gaps = json_model(inputs["gaps.json"], Gaps)
    findings = []
    actions = []
    if aggregate.eligible_answers == 0:
        findings.append(
            Finding(
                finding_id="finding-no-answers",
                category="observed_fact",
                title="Нет пригодных ответов для измерения",
                description=(
                    "Проценты не рассчитаны. Нужны успешные ответы на вопросы без "
                    "подсказки названия."
                ),
                basis=[Basis(source="aggregate", key="eligible_answers")],
                number_ids=["eligible_answers"],
            )
        )
    else:
        rate = next(r for r in aggregate.rates if r.scope == "all-all" and r.metric == "presence")
        findings.append(
            Finding(
                finding_id="finding-presence",
                category="calculated_estimate",
                title="Присутствие компании в собранных ответах",
                description=(
                    "Показатель усреднён по вопросам. Неясные совпадения не засчитаны; "
                    "это оценка выбранного набора, а не всех будущих ответов."
                ),
                basis=[Basis(source="aggregate", key=rate.metric_id)],
                number_ids=[rate.metric_id],
            )
        )
    blocked = [c for c in context.checks if c.status in {"fail", "warning"}]
    if blocked:
        findings.append(
            Finding(
                finding_id="finding-technical",
                category="observed_fact",
                title="Есть технические препятствия или предупреждения",
                description="Основания сохранены в проверке сайта. Их влияние на выбор "
                "моделей не доказано.",
                basis=[Basis(source="check", key=c.check_id) for c in blocked[:10]],
            )
        )
        grouped = defaultdict(list)
        for check in blocked:
            grouped[check.kind].append(check)
        for kind, checks in sorted(grouped.items()):
            actions.append(
                Action(
                    action_id=identifier("action", kind),
                    period="week1",
                    title="Проверить техническое ограничение: " + CHECK_LABELS[kind],
                    instruction=(
                        "Проверьте сохранённое наблюдение и намеренность ограничения. "
                        "Изменяйте настройки только для тех страниц и роботов, которым "
                        "нужен доступ."
                    ),
                    basis=[Basis(source="check", key=c.check_id) for c in checks[:20]],
                    question_ids=[],
                    verify="Повторить соответствующую техническую проверку и сравнить её "
                    "основания.",
                    confidence="observed",
                    limitation=(
                        "Не обходить robots, вход или защиту сайта. Улучшение видимости "
                        "не гарантируется."
                    ),
                )
            )
    elif context.status == "not_supplied":
        findings.append(
            Finding(
                finding_id="finding-missing-site",
                category="observed_fact",
                title="Технические данные сайта не переданы",
                description="Доступность сайта не оценивается по одному тексту ответа модели.",
                basis=[Basis(source="context", key="site_context_status")],
            )
        )
        actions.append(
            Action(
                action_id="action-add-site",
                period="week1",
                title="Приложить сохранённый технический обход",
                instruction="Передайте проверенную папку шага чтения сайта; без неё "
                "технических выводов нет.",
                basis=[Basis(source="context", key="site_context_status")],
                question_ids=[],
                verify="Проверить контрольные суммы и совпадение домена с профилем компании.",
                confidence="observed",
                limitation="Отсутствие данных не означает, что сайт недоступен.",
            )
        )
    if gaps.topics:
        findings.append(
            Finding(
                finding_id="finding-gaps",
                category="observed_fact",
                title="Есть вопросы для проверки покрытия тем",
                description=(
                    "В собранных ответах компания не названа, а другие кандидаты рекомендованы. "
                    "Причина этого и отсутствие нужных страниц ещё не доказаны."
                ),
                basis=[Basis(source="gap", key=t.topic_id) for t in gaps.topics],
            )
        )
        for topic in gaps.topics:
            actions.append(
                Action(
                    action_id=identifier("action", topic.topic_id),
                    period="weeks2_4",
                    title="Проверить содержание по теме: " + topic.title,
                    instruction=(
                        "Сопоставьте вопросы с существующими страницами. Дополните подходящую "
                        "страницу; новую создавайте только после проверки содержания и "
                        "деятельности компании."
                    ),
                    basis=[Basis(source="gap", key=topic.topic_id)],
                    question_ids=topic.question_ids,
                    material=topic.material,
                    verify=(
                        "Проверить цитаты, факты и соответствие теме. "
                        "После публикации сравнить ответы на тех же вопросах при "
                        "сопоставимых настройках."
                    ),
                    confidence="needs_review",
                    limitation=(
                        "Группировка предварительная. Текст — заготовка, требующая "
                        "проверки; рост не обещается."
                    ),
                )
            )
    if any(m.reliability != "medium" for m in aggregate.review_metrics.values()):
        findings.append(
            Finding(
                finding_id="finding-quality",
                category="observed_fact",
                title="Надёжность смысловой разметки ограничена",
                description="Неясные роли и неполная независимая выборка отмечены отдельно от "
                "процентов.",
                basis=[Basis(source="aggregate", key="review_metrics")],
            )
        )
        actions.append(
            Action(
                action_id="action-review",
                period="week1",
                title="Проверить спорные упоминания и роли",
                instruction="Используйте готовые слепые задания. Без исполнителя не считайте "
                "проверку завершённой.",
                basis=[Basis(source="aggregate", key="review_metrics")],
                question_ids=[],
                verify="Сверить дословные цитаты, принадлежность компании и согласие "
                "случайной выборки.",
                confidence="observed",
                limitation="Ручная проверка и модельная независимая выборка различаются.",
            )
        )
    external = [s for s in aggregate.sources if s.owner == "external" and s.answer_ids]
    if external:
        actions.append(
            Action(
                action_id="action-external",
                period="later",
                title="Проверить часто цитируемые внешние источники",
                instruction=(
                    "Проверьте релевантность источников и наличие компании. "
                    "Ссылка в ответе не доказывает чтение страницы моделью."
                ),
                basis=[
                    Basis(source="source", key=s.host)
                    for s in sorted(external, key=lambda s: (-len(s.answer_ids), s.host))[:5]
                ],
                question_ids=[],
                verify="Сохранить наблюдение страницы, дату и точную цитату; "
                "неизвестность оставить неизвестностью.",
                confidence="needs_review",
                limitation="Посещение внешних страниц требует отдельного явного запуска.",
            )
        )
    findings_model = Findings(items=findings[:4])
    plan = Plan(
        synthetic=aggregate.synthetic,
        actions=sorted(
            actions, key=lambda a: (PERIODS[a.period], -len(a.question_ids), a.action_id)
        ),
        limitations=[
            "План основан на наблюдениях и не устанавливает причины выбора модели.",
            "Предлагаемые изменения не гарантируют рост упоминаний, трафика или продаж.",
        ],
    )
    based_items: list[Finding | Action] = [*findings_model.items, *plan.actions]
    for item in based_items:
        for basis in item.basis:
            validate_basis(basis, aggregate, context, gaps)
    for finding in findings_model.items:
        if any(key not in aggregate.numbers for key in finding.number_ids):
            raise ValueError("finding contains an unknown number")
    files = {
        "findings.json": encoded(findings_model.model_dump(mode="json")),
        "plan.json": encoded(plan.model_dump(mode="json")),
        "aggregates.json": inputs["aggregates.json"],
        "context.json": inputs["context.json"],
        "gaps.json": inputs["gaps.json"],
        "categories/observed_fact.jsonl": b"".join(
            encoded(f.model_dump(mode="json"))
            for f in findings_model.items
            if f.category == "observed_fact"
        ),
        "categories/calculated_estimate.jsonl": b"".join(
            encoded(f.model_dump(mode="json"))
            for f in findings_model.items
            if f.category == "calculated_estimate"
        ),
        "categories/hypothesis.jsonl": b"",
        "categories/recommendation.jsonl": b"".join(
            encoded(a.model_dump(mode="json")) for a in plan.actions
        ),
        "categories/index.json": encoded(
            {
                "hypothesis": {"status": "not_established", "count": 0},
                "note": "Причины не выдумываются; рекомендации — действия, а не обещания.",
            }
        ),
        "validation.json": encoded(
            {
                "every_action_has_basis": True,
                "ordering": "period_then_question_count",
                "network_requests": 0,
                "model_calls": 0,
            }
        ),
    }
    return storage.artifacts(inputs, files)


def run_step11(step10_dir: Path, output_dir: Path) -> Plan:
    storage.separate(step10_dir, output_dir)
    verify_step10(step10_dir)
    source = EvidenceStore(step10_dir)
    inputs = {name: source.read_bytes(name, limit=MAX_METADATA_BYTES) for name in SOURCE_FILES}
    inputs["source-manifest.json"] = source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES)
    return json_model(storage.publish(inputs, output_dir, 11, build_artifacts)["plan.json"], Plan)


def verify_publication(directory: Path) -> Plan:
    return json_model(
        storage.verify(directory, 11, INPUT_FILES, build_artifacts)["plan.json"], Plan
    )
