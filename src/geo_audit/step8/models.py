"""Step 8 contracts separate observed names, identity scope and declared catalogs."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..step5.models import PublicationFile
from ..step6.models import Decision, Evidence
from ..urls import normalize_host
from .names import normalize_name

Name = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=0)]
Scope = Literal["target", "non_target_candidate", "unresolved"]
DomainKind = Literal["review", "media", "directory", "forum", "company", "other", "unknown"]


class AliasGroup(Contract):
    aliases: list[Name] = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class DomainLabel(Contract):
    host: str = Field(min_length=1, max_length=253)
    kind: DomainKind
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("host")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalize_host(value)


class EntityCatalog(Contract):
    schema_version: Literal["step8-catalog-1.0"] = "step8-catalog-1.0"
    alias_groups: list[AliasGroup] = Field(default_factory=list, max_length=1000)
    domain_labels: list[DomainLabel] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unambiguous_catalog(self) -> Self:
        seen: set[str] = set()
        for group in self.alias_groups:
            keys = [normalize_name(name) for name in group.aliases]
            if any(not key for key in keys) or len(set(keys)) < 2:
                raise ValueError("alias group needs distinct nonblank normalized names")
            if len(keys) != len(set(keys)) or seen.intersection(keys):
                raise ValueError("catalog aliases overlap")
            if not group.reason.strip():
                raise ValueError("alias evidence reason is blank")
            seen.update(keys)
        if len({item.host for item in self.domain_labels}) != len(self.domain_labels):
            raise ValueError("conflicting domain catalog entries")
        if any(not item.reason.strip() for item in self.domain_labels):
            raise ValueError("domain evidence reason is blank")
        return self


class Step8Policy(Contract):
    schema_version: Literal["step8-policy-1.0"] = "step8-policy-1.0"
    max_distinct_groups: int = Field(default=10000, ge=1, le=10000)
    max_occurrences: int = Field(default=50000, ge=1, le=50000)
    max_pair_evaluations: int = Field(default=10000, ge=1, le=100000)
    max_pair_jobs: int = Field(default=100, ge=0, le=1000)
    max_domain_jobs: int = Field(default=100, ge=0, le=1000)


class BrandOccurrence(Contract):
    occurrence_id: Identifier
    answer_id: Identifier
    primary_brand_index: Count
    primary_annotation_sha256: SHA256
    text_sha256: SHA256
    raw_name: Name
    observed_variants: list[Name] = Field(min_length=1)
    entity_id: Identifier
    identity_scope: Scope
    is_target: Decision
    recommended: Decision
    discouraged: Decision
    measurement_eligible: bool
    rank: int | None = Field(default=None, ge=1)
    evidence: list[Evidence] = Field(min_length=1)
    review_job_id: Identifier | None = None
    role_sources: dict[str, str]


class EntityGroup(Contract):
    entity_id: Identifier
    canonical_name: Name
    identity_scope: Scope
    variants: list[Name] = Field(min_length=1)
    normalized_names: list[str] = Field(min_length=1)
    occurrence_ids: list[Identifier] = Field(min_length=1)
    answer_ids: list[Identifier] = Field(min_length=1)
    eligible_answer_ids: list[Identifier]
    merge_basis: Literal["target_dictionary_context", "normalized_spelling", "manual_catalog"]
    catalog_reasons: list[str]
    identity_status: Literal["declared_target", "observed_name_candidate", "awaiting_step7"]


class DomainGroup(Contract):
    host: str
    kind: DomainKind
    owner: Literal["first_party", "external"]
    method: Literal["target_profile", "manual_catalog", "unclassified"]
    reason: str | None
    answer_ids: list[Identifier]
    eligible_citation_answer_ids: list[Identifier]
    eligible_text_answer_ids: list[Identifier]
    urls: list[str]
    resolution_status: Literal["not_attempted"] = "not_attempted"


class BrandMap(Contract):
    schema_version: Literal["step8-brand-map-1.0"] = "step8-brand-map-1.0"
    entities: list[EntityGroup]


class DomainTypes(Contract):
    schema_version: Literal["step8-domain-types-1.0"] = "step8-domain-types-1.0"
    domains: list[DomainGroup]


class EntityJob(Contract):
    schema_version: Literal["step8-job-1.0"] = "step8-job-1.0"
    job_id: Identifier
    task_type: Literal["compare_entity_names", "classify_source_host"]
    entity_ids: list[Identifier] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    host: str | None = None
    contexts: list[str] = Field(default_factory=list)
    reason: Literal["transliteration", "similar_spelling", "unknown_domain_type"]
    status: Literal["pending_api_key"] = "pending_api_key"
    executed: Literal[False] = False
    instruction: str


class Step8Summary(Contract):
    schema_version: Literal["step8-1.0"] = "step8-1.0"
    algorithm_version: Literal["step8-rules-1"] = "step8-rules-1"
    status: Literal["complete", "partial"]
    source_answers: Count
    occurrences: Count
    entity_groups: Count
    non_target_groups: Count
    unresolved_identity_groups: Count
    source_hosts: Count
    unknown_source_types: Count
    pair_jobs: Count
    domain_jobs: Count
    pair_evaluations: Count
    pair_search_complete: bool
    domains_deferred: Count
    model_calls: Literal[0] = 0
    network_requests: Literal[0] = 0


class PublicationManifest(Contract):
    schema_version: Literal["step8-publication-1.0"] = "step8-publication-1.0"
    committed: Literal[True] = True
    algorithm_version: Literal["step8-rules-1"] = "step8-rules-1"
    source_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=32)


SCHEMA_MODELS: tuple[type[Contract], ...] = (
    BrandMap,
    DomainTypes,
    EntityCatalog,
    Step8Policy,
    BrandOccurrence,
    EntityGroup,
    DomainGroup,
    EntityJob,
    Step8Summary,
    PublicationManifest,
)
