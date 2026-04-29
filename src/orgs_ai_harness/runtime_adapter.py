"""Adapter contracts for the read-only runtime loop."""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_context import RuntimeContext
from orgs_ai_harness.runtime_tools import ToolRegistry

JsonObject: TypeAlias = dict[str, JsonValue]
AdapterDecision: TypeAlias = "ToolCallDecision | FinalResponseDecision"
SubprocessRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[str]]
DEFAULT_CODEX_LOCAL_COMMAND = ("codex-local",)
DEFAULT_CODEX_LOCAL_TIMEOUT_SECONDS = 30.0


class RuntimeAdapterError(Exception):
    """Raised when an adapter decision or input contract is invalid."""


@dataclass(frozen=True)
class ToolCallDecision:
    tool_id: str
    tool_input: JsonObject
    rationale: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not self.tool_id:
            raise RuntimeAdapterError("tool-call decision requires a non-empty tool_id")
        if not isinstance(self.tool_input, dict):
            raise RuntimeAdapterError("tool-call decision requires JSON object tool_input")
        _validate_json_value(self.tool_input, path="tool_input")
        if self.rationale is not None and not isinstance(self.rationale, str):
            raise RuntimeAdapterError("tool-call decision rationale must be a string when provided")

    def to_json(self) -> JsonObject:
        payload: JsonObject = {"type": "tool_call", "tool_id": self.tool_id, "tool_input": self.tool_input}
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        return payload


@dataclass(frozen=True)
class FinalResponseDecision:
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not self.summary:
            raise RuntimeAdapterError("final-response decision requires a non-empty summary")

    def to_json(self) -> JsonObject:
        return {"type": "final_response", "summary": self.summary}


@dataclass(frozen=True)
class RuntimeAdapterObservation:
    adapter_decision_event_id: str
    tool_call_event_id: str
    tool_id: str
    result: JsonObject

    def to_json(self) -> JsonObject:
        return {
            "adapter_decision_event_id": self.adapter_decision_event_id,
            "tool_call_event_id": self.tool_call_event_id,
            "tool_id": self.tool_id,
            "result": self.result,
        }


@dataclass(frozen=True)
class RuntimeAdapterInput:
    goal: str
    context: list[JsonObject]
    tools: tuple[JsonObject, ...]
    skill_catalog: JsonObject
    observations: tuple[RuntimeAdapterObservation, ...] = ()
    permission_mode: str = "read-only"

    def to_json(self) -> JsonObject:
        return {
            "goal": self.goal,
            "context": cast(JsonValue, self.context),
            "tools": cast(JsonValue, list(self.tools)),
            "skill_catalog": self.skill_catalog,
            "observations": cast(JsonValue, [observation.to_json() for observation in self.observations]),
            "permission_mode": self.permission_mode,
        }


class RuntimeAdapter(Protocol):
    def decide(self, adapter_input: RuntimeAdapterInput) -> AdapterDecision:
        """Return the next runtime decision."""
        ...


def assemble_runtime_prompt(
    adapter_input: RuntimeAdapterInput,
    *,
    context_budget_chars: int = 8000,
    skill_budget_chars: int = 4000,
    observation_budget_chars: int = 6000,
) -> str:
    """Build a deterministic provider-neutral prompt for a read-only adapter."""

    sections = [
        (
            "instructions",
            "You are choosing the next decision for a read-only runtime session.\n"
            "Return exactly one JSON object with either type=tool_call or type=final_response.\n"
            "For tool_call, include tool_id and tool_input. For final_response, include summary.\n"
            "Do not request tools outside the supplied catalog or outside the active permission mode.",
        ),
        ("goal", adapter_input.goal),
        ("permission_mode", adapter_input.permission_mode),
        ("runtime_context", _render_bounded_json(adapter_input.context, context_budget_chars)),
        ("tool_catalog", _render_bounded_json(list(adapter_input.tools), context_budget_chars)),
        ("skill_catalog", _render_bounded_json(adapter_input.skill_catalog, skill_budget_chars)),
        (
            "prior_observations",
            _render_bounded_json(
                [observation.to_json() for observation in adapter_input.observations],
                observation_budget_chars,
            ),
        ),
    ]
    return "\n\n".join(f"## {name}\n{body}" for name, body in sections)


def _render_bounded_json(value: object, budget_chars: int) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(rendered) <= budget_chars:
        return rendered
    omitted = len(rendered) - budget_chars
    return rendered[: max(0, budget_chars)] + f"\n[section truncated: {omitted} chars omitted]"


@dataclass
class FixtureRuntimeAdapter:
    decisions: tuple[AdapterDecision, ...]
    input_history: list[RuntimeAdapterInput] = field(default_factory=list)

    def __init__(
        self, decisions: tuple[AdapterDecision | JsonObject, ...] | list[AdapterDecision | JsonObject]
    ) -> None:
        if not decisions:
            raise RuntimeAdapterError("fixture adapter requires at least one scripted decision")
        self.decisions = tuple(coerce_adapter_decision(decision) for decision in decisions)
        self.input_history = []
        self._cursor = 0

    @property
    def received_observations(self) -> tuple[RuntimeAdapterObservation, ...]:
        if not self.input_history:
            return ()
        return self.input_history[-1].observations

    def decide(self, adapter_input: RuntimeAdapterInput) -> AdapterDecision:
        self.input_history.append(adapter_input)
        if self._cursor >= len(self.decisions):
            raise RuntimeAdapterError("fixture adapter decision sequence exhausted")
        decision = self.decisions[self._cursor]
        self._cursor += 1
        return decision


@dataclass(frozen=True)
class CodexLocalRuntimeAdapter:
    command_argv: tuple[str, ...] = DEFAULT_CODEX_LOCAL_COMMAND
    timeout_seconds: float = DEFAULT_CODEX_LOCAL_TIMEOUT_SECONDS
    runner: SubprocessRunner = subprocess.run

    def __post_init__(self) -> None:
        if not self.command_argv or not all(isinstance(part, str) and part for part in self.command_argv):
            raise RuntimeAdapterError("codex-local adapter command must be a non-empty argv tuple")
        if self.timeout_seconds <= 0:
            raise RuntimeAdapterError("codex-local adapter timeout must be greater than zero")

    @classmethod
    def from_environment(cls) -> CodexLocalRuntimeAdapter:
        raw_command = os.environ.get("ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND", "").strip()
        command = tuple(shlex.split(raw_command)) if raw_command else DEFAULT_CODEX_LOCAL_COMMAND
        raw_timeout = os.environ.get("ORGS_AI_HARNESS_CODEX_LOCAL_TIMEOUT", "").strip()
        timeout = DEFAULT_CODEX_LOCAL_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError as exc:
                raise RuntimeAdapterError("ORGS_AI_HARNESS_CODEX_LOCAL_TIMEOUT must be a number") from exc
        return cls(command_argv=command, timeout_seconds=timeout)

    def decide(self, adapter_input: RuntimeAdapterInput) -> AdapterDecision:
        prompt = assemble_runtime_prompt(adapter_input)
        try:
            result = self.runner(  # nosec B603
                list(self.command_argv),
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RuntimeAdapterError(f"codex-local adapter executable not found: {self.command_argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeAdapterError(f"codex-local adapter timed out after {self.timeout_seconds:g}s") from exc
        if result.returncode != 0:
            detail = _bounded_diagnostic(result.stderr or result.stdout)
            message = f"codex-local adapter exited with code {result.returncode}"
            if detail:
                message += f": {detail}"
            raise RuntimeAdapterError(message)
        if result.stderr.strip():
            raise RuntimeAdapterError(f"codex-local adapter wrote stderr: {_bounded_diagnostic(result.stderr)}")
        return parse_adapter_decision_output(result.stdout)


def coerce_adapter_decision(raw: AdapterDecision | JsonObject) -> AdapterDecision:
    if isinstance(raw, ToolCallDecision | FinalResponseDecision):
        return raw
    return adapter_decision_from_json(raw)


def parse_adapter_decision_output(output: str) -> AdapterDecision:
    """Parse exactly one JSON adapter decision object from model output."""

    stripped = output.strip()
    if not stripped:
        raise RuntimeAdapterError("adapter output is empty; expected exactly one JSON object")
    decoder = json.JSONDecoder(parse_constant=_reject_non_json_constant)
    try:
        raw, end = decoder.raw_decode(stripped)
    except RuntimeAdapterError:
        raise
    except json.JSONDecodeError as exc:
        raise RuntimeAdapterError(
            f"adapter output must be valid JSON object text: line {exc.lineno} column {exc.colno}"
        ) from exc
    if stripped[end:].strip():
        raise RuntimeAdapterError("adapter output must contain exactly one JSON object and no extra text")
    if not isinstance(raw, dict):
        raise RuntimeAdapterError("adapter output must be a JSON object")
    return adapter_decision_from_json(raw)


def adapter_decision_from_json(raw: object) -> AdapterDecision:
    if not isinstance(raw, dict):
        raise RuntimeAdapterError("adapter decision must be a JSON object")
    decision_type = raw.get("type")
    if decision_type == "tool_call":
        tool_id = raw.get("tool_id")
        tool_input = raw.get("tool_input")
        rationale = raw.get("rationale")
        if not isinstance(tool_id, str):
            raise RuntimeAdapterError("tool-call decision field tool_id must be a string")
        if not isinstance(tool_input, dict):
            raise RuntimeAdapterError("tool-call decision field tool_input must be an object")
        if rationale is not None and not isinstance(rationale, str):
            raise RuntimeAdapterError("tool-call decision field rationale must be a string")
        return ToolCallDecision(tool_id=tool_id, tool_input=cast(JsonObject, tool_input), rationale=rationale)
    if decision_type == "final_response":
        summary = raw.get("summary")
        if not isinstance(summary, str):
            raise RuntimeAdapterError("final-response decision field summary must be a string")
        return FinalResponseDecision(summary=summary)
    raise RuntimeAdapterError("adapter decision type must be 'tool_call' or 'final_response'")


def _reject_non_json_constant(constant: str) -> object:
    raise RuntimeAdapterError(f"adapter output contains non-JSON-safe value: {constant}")


def _bounded_diagnostic(text: str, limit: int = 800) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "\n[diagnostic truncated]"


def build_adapter_tool_catalog(registry: ToolRegistry) -> tuple[JsonObject, ...]:
    return tuple(
        {
            "tool_id": tool.tool_id,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "required_permission": tool.required_permission.value,
        }
        for tool in registry.list_tools()
    )


def build_adapter_skill_catalog(runtime_context: RuntimeContext, *, budget_chars: int = 4000) -> JsonObject:
    skills_section = next((section for section in runtime_context.sections if section.name == "skills"), None)
    if skills_section is None:
        return {"skills": [], "resolvers": []}
    return {
        "skills": cast(JsonValue, _bounded_records(skills_section.payload.get("skills"), budget_chars=budget_chars)),
        "resolvers": cast(
            JsonValue, _bounded_records(skills_section.payload.get("resolvers"), budget_chars=budget_chars)
        ),
    }


def _bounded_records(raw_records: JsonValue | None, *, budget_chars: int) -> list[JsonObject]:
    if not isinstance(raw_records, list):
        return []
    records: list[JsonObject] = []
    remaining = budget_chars
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue
        path = raw_record.get("path")
        content = raw_record.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        bounded_content = content[: max(0, remaining)]
        records.append({"path": path, "content": bounded_content})
        remaining -= len(bounded_content)
        if remaining <= 0:
            break
    return records


def _validate_json_value(value: JsonValue, *, path: str) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeAdapterError(f"{path} must be JSON-safe; non-finite float is not allowed")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RuntimeAdapterError(f"{path} object keys must be strings")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise RuntimeAdapterError(f"{path} contains unsupported JSON value: {type(value).__name__}")
