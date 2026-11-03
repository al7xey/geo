"""Local application orchestration. Identity screens are test-only; passwords are never used."""

import hashlib
import hmac
import json
import os
import secrets
import subprocess  # nosec B404
import sys
import threading
import traceback
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..evidence import EvidenceStore
from ..step5.collector import validate_approval
from ..step5.extract import folded
from ..step5.models import Group
from ..step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded, unique_json
from ..step5.replay import replay_session
from ..step5.workflow import run as analyze
from ..step9.pipeline import run_step9
from ..step10.pipeline import run_step10
from ..step11.pipeline import run_step11
from ..step12.models import ReportData
from ..step12.pipeline import run_step12
from ..urls import normalize_input
from .gateway import CollectionGateway, MockGateway
from .model_gateway import LiveGateway, ModelRequestError
from .model_jobs import ModelJobs
from .models import Project, Question, QuestionUpdate, ShareSettings
from .store import Store


class AppError(Exception):
    def __init__(
        self, code: str, message: str, status: int = 400, fields: dict[str, str] | None = None
    ):
        self.code, self.message, self.status = code, message, status
        self.fields = fields or {}
        super().__init__(message)


def now() -> str:
    return datetime.now(UTC).isoformat()


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class Application:
    def __init__(self, data_dir: Path, fixture_dir: Path, gateway: CollectionGateway | None = None):
        self.store = Store(data_dir)
        self.gateway = gateway or MockGateway(fixture_dir)
        self.busy = threading.Lock()
        self.cancellations: dict[str, threading.Event] = {}
        self.thread: threading.Thread | None = None
        self.closed = threading.Event()

    def project(self, project_id: str, owner: str) -> Project:
        try:
            return self.store.owned(project_id, owner)
        except KeyError:
            raise AppError("not_found", "Проверка не найдена.", 404) from None

    def projection(self, project: Project) -> dict[str, Any]:
        result = project.model_dump(
            mode="json",
            exclude={"user_id", "share_hash", "source_step2_dir", "review_dir", "site_dir"},
        )
        result["has_share"] = project.share_hash is not None
        if project.mode == "live":
            result["answer_count"] = sum(q.active for q in project.questions) * len(
                project.providers
            )
        if project.mode == "demo":
            result["answer_count"] = sum(q.active for q in project.questions) * 24
        result["demo_notice"] = (
            "Учебный сценарий биржи ссылок. Введённый сайт не посещался; "
            "Ответы заданы заранее и не относятся к изменённым вопросам."
            if project.mode == "demo"
            else "Обрабатываются загруженные файлы. Новых запросов к моделям нет."
        )
        if project.mode == "live":
            result["demo_notice"] = (
                "Сбор через API четырёх настроенных систем, по одному ответу на вопрос. "
                "Сбор ответов выполняется без интернет-поиска. "
                "При наличии подготовленного профиля используются технические данные сайта "
                "и выполняется ограниченная независимая проверка разметки."
            )
        return result

    def list_projects(self, owner: str) -> list[dict[str, Any]]:
        with self.store.lock:
            return [
                self.projection(p)
                for p in sorted(
                    self.store.projects.values(), key=lambda p: p.created_at, reverse=True
                )
                if p.user_id == owner
            ]

    def create(self, owner: str, url: str) -> dict[str, Any]:
        try:
            address = normalize_input(url)
        except Exception:
            raise AppError("invalid_url", "Укажите адрес сайта, например example.ru.") from None
        with self.store.lock:
            if (
                len(self.store.projects) >= 100
                or sum(p.user_id == owner for p in self.store.projects.values()) >= 30
            ):
                raise AppError("project_limit", "Достигнут предел локальных проверок.", 409)
            draft = self.gateway.prepare()
            project = Project(
                project_id=secrets.token_hex(16),
                user_id=owner,
                url=address.url,
                domain=address.host_ascii,
                created_at=now(),
                updated_at=now(),
                target_name=draft["name"],
                target_domain=draft["domain"],
                aliases=draft["aliases"],
                ambiguous_aliases=draft["ambiguous_aliases"],
                questions=draft["questions"],
                answer_count=480,
                providers=["mock_openai", "mock_anthropic", "mock_google"],
            )
            if isinstance(self.gateway, LiveGateway):
                project = project.model_copy(
                    update={
                        "mode": "live",
                        "synthetic": False,
                        "preparation_status": "not_started",
                        "target_name": address.host_ascii,
                        "aliases": [address.host_ascii],
                        "ambiguous_aliases": [],
                        "questions": [],
                        "providers": [p.name for p in self.gateway.providers],
                        "repeats": 1,
                        "target_domain": address.host_ascii,
                        "answer_count": 0,
                    }
                )
            self.store.save(project)
            return self.projection(project)

    def prepare_questions(self, owner: str, project_id: str, description: str) -> dict[str, Any]:
        with self.store.lock:
            project = self.project(project_id, owner)
            if (
                project.mode != "live"
                or project.state != "draft"
                or project.preparation_status != "not_started"
            ):
                raise AppError(
                    "immutable", "Подготовка уже выполнялась. Автоматического повтора нет.", 409
                )
            root = Path(__file__).resolve().parents[3]
            if not (root / "private/prompts/prompt_step2_profile_and_queries.md").is_file():
                raise AppError(
                    "missing_prompt", "Добавьте исходный файл правил шага 2 в private/prompts."
                )
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise AppError("missing_key", "Исходный шаг 2 требует ANTHROPIC_API_KEY.")
            if not self.busy.acquire(blocking=False):
                raise AppError("busy", "Другая проверка выполняется. Дождитесь её завершения.", 409)
            project = project.model_copy(
                update={
                    "preparation_status": "running",
                    "stage": 1,
                    "message": "Шаги 0–2: читаем сайт, строим профиль и создаём вопросы.",
                    "updated_at": now(),
                }
            )
            try:
                self.store.save(project)
                self.thread = threading.Thread(
                    target=self.prepare_worker, args=(project, description), daemon=True
                )
                self.thread.start()
            except BaseException:
                self.busy.release()
                raise
            return self.projection(project)

    def prepare_worker(self, project: Project, description: str) -> None:
        try:
            root = Path(__file__).resolve().parents[3]
            output = EvidenceStore(self.store.io.root / project.project_id / "preparation")
            output.prepare(new=True)
            receipt = output.root / "receipt.json"
            command = [
                sys.executable,
                "-X",
                "utf8",
                str(root / "tools/ui_prepare.py"),
                "--url",
                project.url,
                "--description",
                description,
                "--receipt",
                str(receipt),
            ]
            from ..secure_io import open_confined

            with open_confined(output.root / "preparation.log", writing=True) as log:
                result = subprocess.run(
                    command,
                    cwd=root,
                    env=dict(os.environ, PYTHONUTF8="1"),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    timeout=900,
                    check=False,
                )  # nosec B603
            if result.returncode:
                raise ModelRequestError(
                    "Подготовка вопросов не завершена. Проверьте описание сайта, "
                    "доступ Claude и preparation.log. Ответы не собирались."
                )
            payload = json.loads(output.read_bytes("receipt.json", limit=MAX_METADATA_BYTES))
            source = EvidenceStore(Path(payload["step2_dir"]))
            profile = json.loads(source.read_bytes("profile.json", limit=MAX_METADATA_BYTES))
            prompts = json.loads(source.read_bytes("prompts.json", limit=MAX_METADATA_BYTES))[
                "prompts"
            ]
            aliases = [v["variant"] for v in profile["name_variants"] if v.get("variant")]
            if not aliases or not prompts:
                raise ValueError("generated profile or questions are empty")
            counts = dict.fromkeys("ABCD", 0)
            questions = []
            for row in prompts:
                group = cast(Group, "ABCD"[row["level"] - 1])
                branded = bool(row.get("has_brand"))
                active = counts[group] < 5 and not branded
                counts[group] += int(active)
                questions.append(
                    Question(
                        id=row["id"],
                        text=row["text"],
                        group=group,
                        active=active,
                        has_brand=branded,
                    )
                )
            with self.store.lock:
                latest = self.store.projects[project.project_id]
                self.store.save(
                    latest.model_copy(
                        update={
                            "preparation_status": "ready",
                            "stage": 3,
                            "target_domain": payload["report"]["domain"],
                            "target_name": aliases[0],
                            "aliases": aliases,
                            "ambiguous_aliases": [
                                v["variant"]
                                for v in profile["name_variants"]
                                if v.get("ambiguous", True)
                            ],
                            "questions": questions,
                            "source_step2_dir": str(source.root),
                            "site_dir": payload["site_dir"],
                            "revision": latest.revision + 1,
                            "message": (
                                "Профиль и вопросы созданы шагом 2. Проверьте и подтвердите их."
                            ),
                            "updated_at": now(),
                        }
                    )
                )
        except Exception as error:
            with self.store.lock:
                latest = self.store.projects[project.project_id]
                message = (
                    str(error)
                    if isinstance(error, ModelRequestError)
                    else (
                        "Подготовка вопросов не завершена. Подробности в preparation. "
                        "Платного сбора ответов не было."
                    )
                )
                self.store.save(
                    latest.model_copy(
                        update={
                            "preparation_status": "error",
                            "message": message,
                            "updated_at": now(),
                        }
                    )
                )
        finally:
            self.busy.release()

    def approve_upstream(self, project: Project, questions: list[Question]) -> Path:
        root = Path(__file__).resolve().parents[3]
        output = EvidenceStore(
            self.store.io.root / project.project_id / ("review-" + secrets.token_hex(8))
        )
        output.prepare(new=True)
        state: dict[str, Any] = {"names": project.aliases, "prompts": [], "custom": []}
        source = EvidenceStore(Path(str(project.source_step2_dir)))
        profile = json.loads(source.read_bytes("profile.json", limit=MAX_METADATA_BYTES))
        state.update(
            category=profile.get("category", {}).get("main", ""),
            client_terms=profile.get("category", {}).get("client_terms", []),
        )
        for question in questions:
            if question.active:
                key = "custom" if question.origin == "custom" else "prompts"
                state[key].append(
                    {
                        "id": question.id,
                        "text": question.text,
                        "level": "ABCD".index(question.group) + 1,
                    }
                )
        receipt = output.root / "approval.json"
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(root / "tools/ui_prepare.py"),
                "--step2-dir",
                str(project.source_step2_dir),
                "--approve",
                str(output.root),
                "--receipt",
                str(receipt),
            ],
            cwd=root,
            input=encoded(state),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )  # nosec B603
        payload = json.loads(output.read_bytes("approval.json", limit=MAX_METADATA_BYTES))
        if result.returncode or not payload.get("saved"):
            errors = {key: " ".join(value) for key, value in payload.get("errors", {}).items()}
            raise AppError(
                "invalid_questions",
                "Исправьте вопросы по правилам исходного шага 3.",
                fields=errors,
            )
        return output.root

    def save_questions(self, owner: str, project_id: str, update: QuestionUpdate) -> dict[str, Any]:
        with self.store.lock:
            project = self.project(project_id, owner)
            if project.state != "draft" or project.imported:
                raise AppError(
                    "immutable",
                    "Вопросы этой проверки уже закреплены. Создайте новую проверку.",
                    409,
                )
            if project.revision != update.revision:
                raise AppError(
                    "revision_conflict",
                    "Вопросы изменены в другой вкладке. Обновите страницу.",
                    409,
                )
            if project.preparation_status != "ready":
                raise AppError("not_prepared", "Сначала дождитесь подготовки вопросов.", 409)
            if project.mode == "live" and sum(q.active for q in update.questions) > 20:
                raise AppError(
                    "question_limit", "Для экономного запуска выберите не более 20 вопросов."
                )
            ids = [q.id for q in update.questions]
            fields = {}
            texts = set()
            if (
                len(ids) != len(set(ids))
                or sum(q.origin == "custom" for q in update.questions) > 10
            ):
                raise AppError(
                    "invalid_questions",
                    "Допускается до десяти своих вопросов с разными идентификаторами.",
                )
            questions = []
            aliases = [folded(name)[0] for name in project.aliases]
            for question in update.questions:
                text = question.text.strip()
                key = normalized(text)
                if question.active:
                    if len(text) < 5 or len(text) > 2000:
                        fields[question.id] = "Введите вопрос длиной от 5 до 2000 символов."
                    if key in texts:
                        fields[question.id] = "Такой вопрос уже есть."
                    texts.add(key)
                brand = any(name and name in folded(text)[0] for name in aliases)
                questions.append(question.model_copy(update={"text": text, "has_brand": brand}))
            if not any(q.active for q in questions):
                fields["form"] = "Оставьте хотя бы один вопрос."
            if fields:
                raise AppError("invalid_questions", "Исправьте отмеченные вопросы.", fields=fields)
            review_dir = (
                str(self.approve_upstream(project, questions))
                if project.source_step2_dir
                else project.review_dir
            )
            if review_dir:
                approved_flags = json.loads(
                    EvidenceStore(Path(review_dir)).read_bytes(
                        "step4_input.json", limit=MAX_METADATA_BYTES
                    )
                )["prompts"]
                by_id = {row["id"]: row for row in approved_flags}
                questions = [
                    q.model_copy(
                        update={
                            "has_brand": by_id[q.id]["has_brand"],
                            "origin": by_id[q.id]["origin"],
                        }
                    )
                    if q.id in by_id
                    else q
                    for q in questions
                ]
            project = project.model_copy(
                update={
                    "review_dir": review_dir,
                    "questions": questions,
                    "revision": project.revision + 1,
                    "updated_at": now(),
                }
            )
            self.store.save(project)
            return self.projection(project)

    def profile(self, project: Project) -> dict[str, Any]:
        if project.review_dir:
            return cast(
                dict[str, Any],
                json.loads(
                    EvidenceStore(Path(project.review_dir)).read_bytes(
                        "profile.json", limit=MAX_METADATA_BYTES
                    )
                ),
            )
        return {
            "domain": project.target_domain,
            "name_variants": [
                {"variant": name, "ambiguous": name in project.ambiguous_aliases}
                for name in project.aliases
            ],
        }

    def approved(self, project: Project) -> dict[str, Any]:
        if project.review_dir:
            return cast(
                dict[str, Any],
                json.loads(
                    EvidenceStore(Path(project.review_dir)).read_bytes(
                        "step4_input.json", limit=MAX_METADATA_BYTES
                    )
                ),
            )
        prompts = [
            dict(
                id=q.id,
                text=q.text,
                level="ABCD".index(q.group) + 1,
                origin=q.origin,
                has_brand=q.has_brand,
                counts_in_organic_visibility=not q.has_brand,
            )
            for q in project.questions
            if q.active
        ]
        return dict(
            schema_version="step4-input-1",
            domain=project.target_domain,
            country="Россия",
            language="русский",
            name_variants=project.aliases,
            prompts=prompts,
            prompts_sha256=digest(
                json.dumps(
                    prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ),
        )

    def import_answers(
        self,
        owner: str,
        project_id: str,
        profile: dict[str, Any],
        approved: dict[str, Any],
        answers: str,
        synthetic: bool,
    ) -> dict[str, Any]:
        from ..step5.collector import build_artifacts
        from ..step5.models import PromptSet, Target

        inputs = {
            "profile.json": encoded(profile),
            "step4-input.json": encoded(approved),
            "answers.jsonl": answers.encode(),
            "policy.json": encoded({"synthetic": synthetic}),
        }
        files = build_artifacts(inputs)
        target = Target.model_validate_json(files["target.json"])
        prompts = PromptSet.model_validate_json(files["prompts.json"])
        summary = unique_json(files["summary.json"])
        if not isinstance(summary, dict):
            raise ValueError("invalid import summary")
        with self.store.lock:
            project = self.project(project_id, owner)
            if project.state != "draft":
                raise AppError("immutable", "Нельзя заменить входы запущенной проверки.", 409)
            source = EvidenceStore(self.store.io.root / project_id / "import")
            source.prepare(new=True)
            for name, data in inputs.items():
                source.atomic_bytes(name, data)
            project = project.model_copy(
                update={
                    "mode": "import",
                    "synthetic": synthetic,
                    "preparation_status": "ready",
                    "source_step2_dir": None,
                    "review_dir": None,
                    "site_dir": None,
                    "target_name": target.name,
                    "target_domain": target.domains[0],
                    "aliases": target.aliases,
                    "ambiguous_aliases": target.ambiguous_aliases,
                    "imported": True,
                    "answer_count": summary["selected_answers"],
                    "providers": sorted(summary["delivery_by_system"]),
                    "repeats": None,
                    "questions": [
                        Question(
                            id=q.prompt_id,
                            text=q.text,
                            group=q.group,
                            has_brand=not q.visibility_eligible,
                        )
                        for q in prompts.prompts
                    ],
                    "revision": project.revision + 1,
                    "updated_at": now(),
                }
            )
            self.store.save(project)
            return self.projection(project)

    def start(self, owner: str, project_id: str, revision: int) -> dict[str, Any]:
        with self.store.lock:
            project = self.project(project_id, owner)
            if project.preparation_status != "ready" or (
                project.source_step2_dir and not project.review_dir
            ):
                raise AppError(
                    "not_approved", "Дождитесь подготовки и подтвердите вопросы на шаге 3.", 409
                )
            if project.revision != revision:
                raise AppError(
                    "revision_conflict", "Вопросы изменились. Проверьте их перед запуском.", 409
                )
            if project.state in {"queued", "running", "cancelling", "done"}:
                raise AppError("already_started", "Проверка уже запущена или завершена.", 409)
            if not self.busy.acquire(blocking=False):
                raise AppError(
                    "busy", "Другая проверка ещё выполняется. Дождитесь её завершения.", 409
                )
            event = threading.Event()
            self.cancellations[project_id] = event
            project = project.model_copy(
                update={
                    "state": "queued",
                    "run_id": secrets.token_hex(16),
                    "message": "Начинаем локальную обработку.",
                    "stage": 4,
                    "updated_at": now(),
                }
            )
            try:
                self.store.save(project)
                self.thread = threading.Thread(
                    target=self.execute, args=(project, event), daemon=True
                )
                self.thread.start()
            except BaseException:
                self.busy.release()
                raise
            return self.projection(project)

    def update_job(self, project_id: str, stage: int, message: str, event: threading.Event) -> None:
        if event.is_set() or self.closed.is_set():
            raise InterruptedError("cancel requested")
        with self.store.lock:
            project = self.store.projects[project_id]
            self.store.save(
                project.model_copy(
                    update={
                        "state": "running",
                        "stage": stage,
                        "message": message,
                        "updated_at": now(),
                    }
                )
            )

    def execute(self, project: Project, event: threading.Event) -> None:
        try:
            root = EvidenceStore(self.store.io.root / project.project_id / str(project.run_id))
            root.prepare(new=True)
            inputs = EvidenceStore(root.root / "input")
            inputs.prepare(new=True)
            if project.imported:
                source = EvidenceStore(self.store.io.root / project.project_id / "import")
                approved = source.read_bytes("step4-input.json", limit=MAX_METADATA_BYTES)
                profile = source.read_bytes("profile.json", limit=MAX_METADATA_BYTES)
                answers = source.read_bytes("answers.jsonl", limit=MAX_ANSWERS_BYTES)
            else:
                approved, profile = encoded(self.approved(project)), encoded(self.profile(project))
                validate_approval(approved, profile)
                inputs.atomic_bytes("step4_input.json", approved)
                inputs.atomic_bytes("profile.json", profile)
                if project.mode == "live":
                    if not isinstance(self.gateway, LiveGateway):
                        raise ModelRequestError(
                            "Запустите сервер с параметром --live и настройте .env."
                        )
                    self.update_job(
                        project.project_id,
                        4,
                        "Получаем ответы через API. Повторы отключены.",
                        event,
                    )
                    answers = self.gateway.collect_saved(project, inputs, event)
                else:
                    answers = b"".join(encoded(row) for row in self.gateway.collect(project))
            validate_approval(approved, profile)
            inputs.atomic_bytes("step4_input.json", approved)
            inputs.atomic_bytes("profile.json", profile)
            inputs.atomic_bytes("answers.jsonl", answers)
            model_jobs = (
                ModelJobs(self.gateway, root.root / "model-jobs", event)
                if (
                    isinstance(self.gateway, LiveGateway)
                    and project.source_step2_dir
                    and not project.imported
                )
                else None
            )
            with replay_session():
                self.update_job(project.project_id, 5, "Разбираем сохранённые ответы.", event)
                analyze(
                    inputs.root / "step4_input.json",
                    inputs.root,
                    root.root / "analysis",
                    synthetic=project.synthetic,
                    review_executor=model_jobs.reviews if model_jobs else None,
                )
                self.update_job(
                    project.project_id, 9, "Рассчитываем доли и разброс по вопросам.", event
                )
                run_step9(
                    root.root / "analysis/step8",
                    root.root / "step9",
                    site_dir=Path(project.site_dir) if project.site_dir else None,
                )
                self.update_job(project.project_id, 10, "Выделяем темы для проверки.", event)
                run_step10(
                    root.root / "step9",
                    root.root / "step10",
                    results_file=model_jobs.gaps(root.root / "step9") if model_jobs else None,
                )
                self.update_job(
                    project.project_id, 11, "Составляем находки и план действий.", event
                )
                run_step11(root.root / "step10", root.root / "step11")
                self.update_job(project.project_id, 12, "Собираем отчёт.", event)
                run_step12(root.root / "step11", root.root / "report")
            self.update_job(project.project_id, 12, "Отчёт готов.", event)
            with self.store.lock:
                latest = self.store.projects[project.project_id]
                self.store.save(latest.model_copy(update={"state": "done", "updated_at": now()}))
        except InterruptedError:
            with self.store.lock:
                latest = self.store.projects[project.project_id]
                self.store.save(
                    latest.model_copy(
                        update={
                            "state": "cancelled",
                            "message": "Обработка остановлена.",
                            "updated_at": now(),
                        }
                    )
                )
        except Exception as error:
            with self.store.lock:
                latest = self.store.projects[project.project_id]
                message = (
                    str(error)
                    if isinstance(error, ModelRequestError)
                    else ("Обработка не завершена. Ответы сохранены. Повторных запросов к API нет.")
                )
                EvidenceStore(self.store.io.root).atomic_bytes(
                    "last-error.json",
                    encoded(
                        {
                            "type": type(error).__name__,
                            "project_id": project.project_id,
                            "traceback": traceback.format_exc(),
                        }
                    ),
                )
                self.store.save(
                    latest.model_copy(
                        update={"state": "error", "message": message, "updated_at": now()}
                    )
                )
        finally:
            self.busy.release()

    def cancel(self, owner: str, project_id: str) -> dict[str, Any]:
        with self.store.lock:
            project = self.project(project_id, owner)
            if project.state not in {"queued", "running", "cancelling"}:
                raise AppError("not_running", "Проверка сейчас не выполняется.", 409)
            self.cancellations[project_id].set()
            project = project.model_copy(
                update={"state": "cancelling", "message": "Останавливаем после текущего этапа."}
            )
            self.store.save(project)
            return self.projection(project)

    def result_dir(self, project: Project) -> Path:
        if project.state != "done" or not project.run_id:
            raise AppError("not_ready", "Отчёт ещё не готов.", 409)
        return self.store.io.root / project.project_id / project.run_id

    def report(self, project: Project) -> dict[str, Any]:
        root = self.result_dir(project)
        raw = EvidenceStore(root / "report").read_bytes(
            "report_data.json", limit=MAX_METADATA_BYTES
        )
        data = ReportData.model_validate_json(raw).model_dump(mode="json")
        data["project"] = self.projection(project)
        data["model_review"] = None
        review_usage = root / "model-jobs/usage.json"
        if review_usage.is_file():
            data["model_review"] = json.loads(
                EvidenceStore(review_usage.parent).read_bytes(
                    review_usage.name, limit=MAX_METADATA_BYTES
                )
            )
        data["collection"] = None
        usage_path = root / "input/api-usage.json"
        if project.mode == "live" and usage_path.is_file():
            data["collection"] = json.loads(
                EvidenceStore(usage_path.parent).read_bytes(
                    usage_path.name, limit=MAX_METADATA_BYTES
                )
            )
        from ..step9.models import SiteContext

        context = SiteContext.model_validate_json(
            EvidenceStore(root / "report/input").read_bytes(
                "context.json", limit=MAX_METADATA_BYTES
            )
        )
        data["technical"] = {
            "status": context.status,
            "checks": [check.model_dump(mode="json") for check in context.checks],
        }
        data["comparison"] = {"available": False, "reason": "Нет предыдущей сопоставимой проверки."}
        previous = sorted(
            (
                p
                for p in self.store.projects.values()
                if p.user_id == project.user_id
                and p.domain == project.domain
                and p.state == "done"
                and p.created_at < project.created_at
                and p.target_domain == project.target_domain
                and p.synthetic == project.synthetic
            ),
            key=lambda p: p.created_at,
            reverse=True,
        )
        if previous:
            prior = ReportData.model_validate_json(
                EvidenceStore(self.result_dir(previous[0]) / "report").read_bytes(
                    "report_data.json", limit=MAX_METADATA_BYTES
                )
            ).model_dump(mode="json")
            before, after = prior["aggregates"], data["aggregates"]

            def question_set(rows: list[dict[str, Any]]) -> set[tuple[str, str, bool]]:
                return {(q["prompt_id"], normalized(q["text"]), q["organic"]) for q in rows}

            if (
                question_set(before["questions"]) == question_set(after["questions"])
                and before["cohorts"] == after["cohorts"]
            ):
                rates_before = {
                    r["metric"]: r["percent"] for r in before["rates"] if r["scope"] == "all-all"
                }
                rates_after = {
                    r["metric"]: r["percent"] for r in after["rates"] if r["scope"] == "all-all"
                }
                data["comparison"] = {
                    "available": True,
                    "previous_id": previous[0].project_id,
                    "created_at": previous[0].created_at,
                    "before": rates_before,
                    "after": rates_after,
                }
            else:
                data["comparison"] = {
                    "available": False,
                    "reason": "Вопросы или настройки различаются: прямое сравнение некорректно.",
                }
        return data

    def examples(self, project: Project, group: str | None = None) -> list[dict[str, Any]]:
        root = self.result_dir(project)
        source = EvidenceStore(root / "analysis/step6")
        rows = [
            unique_json(line)
            for line in source.read_bytes("annotations.jsonl", limit=MAX_ANSWERS_BYTES).splitlines()
        ]
        answers = (
            EvidenceStore(root / "analysis/bundle")
            .read_bytes("answers.jsonl", limit=MAX_ANSWERS_BYTES)
            .splitlines()
        )
        result = []
        for row in rows:
            if not isinstance(row, dict) or (group and row["group"] != group):
                continue
            original = unique_json(answers[int(str(row["source_line"])) - 1])
            if isinstance(original, dict):
                result.append(
                    {
                        "answer_id": row["answer_id"],
                        "prompt_id": row["prompt_id"],
                        "group": row["group"],
                        "provider": row["provider"],
                        "model": row["model"],
                        "text": str(original["text"])[:4000],
                        "status": row["delivery_status"],
                        "presence": row["target_presence"],
                        "recommended": row["target_recommended"],
                        "sources": original["sources"],
                    }
                )
            if len(result) >= 50:
                break
        return result

    def completion(
        self, owner: str, project_id: str, action_id: str, completed: bool
    ) -> dict[str, Any]:
        with self.store.lock:
            project = self.project(project_id, owner)
            actions = self.report(project)["plan"]["actions"]
            if action_id not in {a["action_id"] for a in actions}:
                raise AppError("unknown_action", "Пункт плана не найден.")
            values = set(project.completed_actions)
            values.add(action_id) if completed else values.discard(action_id)
            project = project.model_copy(
                update={"completed_actions": sorted(values), "updated_at": now()}
            )
            self.store.save(project)
            return self.projection(project)

    def share(self, owner: str, project_id: str, settings: ShareSettings) -> dict[str, str]:
        with self.store.lock:
            project = self.project(project_id, owner)
            self.result_dir(project)
            token = secrets.token_urlsafe(32)
            project = project.model_copy(
                update={
                    "share_hash": hashlib.sha256(token.encode()).hexdigest(),
                    "share_settings": settings.model_dump(),
                }
            )
            self.store.save(project)
            return {"token": token}

    def shared(self, token: str) -> Project:
        sha = hashlib.sha256(token.encode()).hexdigest()
        with self.store.lock:
            project = next(
                (
                    p
                    for p in self.store.projects.values()
                    if p.share_hash and hmac.compare_digest(p.share_hash, sha)
                ),
                None,
            )
            if project is None:
                raise AppError("not_found", "Ссылка недействительна или доступ закрыт.", 404)
            return project

    def shared_report(self, token: str) -> dict[str, Any]:
        project = self.shared(token)
        data = self.report(project)
        data["project"].pop("disputes", None)
        data["project"].pop("share_settings", None)
        data["project"].pop("run_id", None)
        if not project.share_settings["show_prompts"]:
            data["project"]["questions"] = []
            data["aggregates"]["questions"] = []
            data["gaps"]["topics"] = []
            data["plan"]["actions"] = [a for a in data["plan"]["actions"] if not a["question_ids"]]
        data["comparison"] = {
            "available": False,
            "reason": "История проверок не включена в общую ссылку.",
        }
        data["readonly"] = True
        data["show_answers"] = project.share_settings["show_answers"]
        return data
