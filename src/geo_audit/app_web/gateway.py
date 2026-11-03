"""Collection protocol and offline fixtures; live adapters are in model_gateway."""

from pathlib import Path
from typing import Any, Protocol, cast

from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, unique_json
from .models import Project, Question


class CollectionGateway(Protocol):
    def prepare(self) -> dict[str, Any]: ...
    def collect(self, project: Project) -> list[dict[str, Any]]: ...


class MockGateway:
    def __init__(self, fixture: Path):
        source = EvidenceStore(fixture)
        self.target = cast(
            dict[str, Any], unique_json(source.read_bytes("target.json", limit=MAX_METADATA_BYTES))
        )
        self.prompts = cast(
            dict[str, Any], unique_json(source.read_bytes("prompts.json", limit=MAX_METADATA_BYTES))
        )
        self.answers: list[dict[str, Any]] = [
            cast(dict[str, Any], unique_json(line))
            for line in source.read_bytes("answers.jsonl", limit=MAX_ANSWERS_BYTES).splitlines()
        ]

    def prepare(self) -> dict[str, Any]:
        return {
            "name": self.target["name"],
            "domain": self.target["domains"][0],
            "aliases": self.target["aliases"],
            "ambiguous_aliases": self.target["ambiguous_aliases"],
            "questions": [
                Question(id=q["prompt_id"], text=q["text"], group=q["group"])
                for q in self.prompts["prompts"]
            ],
        }

    def collect(self, project: Project) -> list[dict[str, Any]]:
        active = {q.id for q in project.questions if q.active}
        rows = [
            dict(
                answer_id=a["answer_id"],
                prompt_id=a["prompt_id"],
                system=a["provider"],
                model=a["model"],
                repeat=a["repeat_index"],
                status=a["status"] if a["status"] in {"ok", "empty"} else "error",
                text=a["text"],
                sources=[s["url"] for s in a["sources"]],
            )
            for a in self.answers
            if a["prompt_id"] in active
        ]
        known = {row["prompt_id"] for row in rows}
        for question in project.questions:
            if question.active and question.id not in known:
                for provider in ("mock_openai", "mock_anthropic", "mock_google"):
                    for repeat in range(1, 9):
                        text = (
                            "Рекомендую " + project.target_name + " для этой задачи."
                            if repeat % 3
                            else "Для выбора нужно уточнить условия проекта."
                        )
                        rows.append(
                            dict(
                                answer_id=f"{question.id}-{provider}-{repeat}",
                                prompt_id=question.id,
                                system=provider,
                                model=provider + "_fixture_v1",
                                repeat=repeat,
                                status="ok",
                                text=text,
                                sources=[],
                            )
                        )
        return rows
