"""Private atomic JSON persistence with a bounded, single-writer in-process lock."""

import threading
from pathlib import Path

from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_METADATA_BYTES, unique_json
from .models import Project


class Store:
    def __init__(self, directory: Path):
        self.io = EvidenceStore(directory)
        self.lock = threading.RLock()
        if not self.io.root.exists():
            self.io.prepare(new=True)
        # Existing roots must pass the same confined read/write checks.
        else:
            self.io.prepare()
        self.projects: dict[str, Project] = {}
        try:
            raw = self.io.read_bytes("projects.json", limit=MAX_METADATA_BYTES)
        except (OSError, GeoAuditError) as error:
            if (self.io.root / "projects.json").exists():
                raise error
            raw = b"[]"
        rows = unique_json(raw)
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("invalid project index")
        for row in rows:
            project = Project.model_validate(row)
            if project.state in {"queued", "running", "cancelling"}:
                project = project.model_copy(
                    update={
                        "state": "error",
                        "message": "Сервер перезапущен. Запустите обработку ещё раз.",
                    }
                )
            if project.preparation_status == "running":
                project = project.model_copy(
                    update={
                        "preparation_status": "error",
                        "message": (
                            "Подготовка прервана перезапуском сервера. "
                            "Автоматических запросов к API нет."
                        ),
                    }
                )
            self.projects[project.project_id] = project

    def save(self, project: Project) -> None:
        from ..step5.pipeline import encoded

        projects = {**self.projects, project.project_id: project}
        self.io.atomic_bytes(
            "projects.json",
            encoded([p.model_dump(mode="json") for p in projects.values()]),
            limit=MAX_METADATA_BYTES,
        )
        self.projects = projects

    def owned(self, project_id: str, user_id: str) -> Project:
        project = self.projects.get(project_id)
        if not project or project.user_id != user_id:
            raise KeyError("project not found")
        return project
