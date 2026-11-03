"""Explicit local app contracts, separate from the unchanged steps 0-4."""

from typing import Literal

from pydantic import Field

from ..contracts import Contract, Identifier


class Question(Contract):
    id: Identifier
    text: str = Field(min_length=1, max_length=2000)
    group: Literal["A", "B", "C", "D"]
    origin: Literal["suggested", "edited", "custom"] = "suggested"
    active: bool = True
    has_brand: bool = False


class Project(Contract):
    project_id: Identifier
    user_id: Identifier
    url: str
    domain: str
    created_at: str
    mode: Literal["demo", "import", "live"] = "demo"
    synthetic: bool = True
    state: Literal["draft", "queued", "running", "cancelling", "cancelled", "done", "error"] = (
        "draft"
    )
    stage: int = Field(default=0, ge=0, le=12)
    message: str = "Вопросы готовы к проверке."
    preparation_status: Literal["not_started", "running", "ready", "error"] = "ready"
    source_step2_dir: str | None = None
    review_dir: str | None = None
    site_dir: str | None = None
    target_name: str
    target_domain: str
    aliases: list[str]
    ambiguous_aliases: list[str]
    questions: list[Question] = Field(max_length=50)
    revision: int = 1
    run_id: Identifier | None = None
    updated_at: str
    completed_actions: list[str] = Field(default_factory=list, max_length=100)
    disputes: list[str] = Field(default_factory=list, max_length=100)
    share_hash: str | None = None
    share_settings: dict[str, bool] = Field(
        default_factory=lambda: {"show_prompts": True, "show_answers": False}
    )
    imported: bool = False
    answer_count: int = 0
    providers: list[str] = Field(default_factory=list)
    repeats: int | None = 8


class QuestionUpdate(Contract):
    revision: int = Field(ge=1)
    questions: list[Question] = Field(min_length=1, max_length=50)


class ShareSettings(Contract):
    show_prompts: bool = True
    show_answers: bool = False
