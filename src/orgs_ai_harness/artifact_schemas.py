"""Typed contracts for JSON artifacts exchanged across harness modules."""

from __future__ import annotations

from typing import NotRequired, TypeAlias, TypedDict

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class AdapterMetrics(TypedDict, total=False):
    tool_calls: int
    file_reads: int
    searches: int
    command_attempts: int
    elapsed_adapter_steps: int


class EvalTask(TypedDict, total=False):
    id: str
    prompt: str
    expected_files: list[str]
    expected_commands: list[str]
    expected_contains: list[str]
    forbidden_contains: list[str]


class EvalScore(TypedDict):
    task_id: str
    required_facts_score: float
    required_files_score: float
    required_commands_score: float
    forbidden_claims_score: float
    evidence_score: float
    latency_or_steps: int
    passed: bool
    forbidden_hits: list[str]
    evidence: list[str]
    answer: str
    metrics: AdapterMetrics


class EvalRun(TypedDict):
    run: str
    pack_ref: str | None
    tasks: list[EvalScore]
    metrics: AdapterMetrics
    rediscovery_cost: int
    pass_rate: float


class ApprovalMetadata(TypedDict, total=False):
    decision: str
    approved_artifacts: list[str]
    pack_ref: str | None
    status: str
    verified: bool
    warnings: list[JsonValue]
    verification: dict[str, JsonValue]


class ProposalEvidence(TypedDict):
    created_from: str
    event_type: str
    event_id: JsonValue
    trace: str
    payload: JsonValue


class ProposalMetadata(TypedDict):
    schema_version: int
    id: str
    repo_id: str
    status: str
    risk: str
    proposal_type: str
    target_artifacts: list[str]
    affected_evals: list[str]
    evidence: list[str]
    created_from: list[str]
    created_at: str
    previous_source_commit: NotRequired[str]
    current_source_commit: NotRequired[str]
    applied_at: NotRequired[str]
    applied_artifacts: NotRequired[list[str]]
    rejected_at: NotRequired[str]
    rejection_reason: NotRequired[str]
