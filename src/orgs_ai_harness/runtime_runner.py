"""Deterministic runtime run-loop slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_adapter import (
    FinalResponseDecision,
    FixtureRuntimeAdapter,
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeAdapterInput,
    RuntimeAdapterObservation,
    ToolCallDecision,
    build_adapter_skill_catalog,
    build_adapter_tool_catalog,
    coerce_adapter_decision,
)
from orgs_ai_harness.runtime_context import assemble_runtime_context
from orgs_ai_harness.runtime_events import RuntimeSessionStore
from orgs_ai_harness.runtime_hooks import HookedToolDispatcher
from orgs_ai_harness.runtime_permissions import PermissionLevel
from orgs_ai_harness.runtime_recovery import RuntimeRecoverySummary, summarize_recovery
from orgs_ai_harness.runtime_tools import ToolExecutionContext, ToolRegistry, default_tool_registry


@dataclass(frozen=True)
class RuntimeRunResult:
    session_id: str
    session_path: Path
    summary: str
    ok: bool = True
    diagnostics: tuple[str, ...] = ()


def run_read_only_session(
    workspace: Path,
    goal: str,
    *,
    adapter: RuntimeAdapter | None = None,
    max_steps: int = 8,
    session_root: Path | None = None,
    session_id: str | None = None,
    tool_registry: ToolRegistry | None = None,
) -> RuntimeRunResult:
    workspace = workspace.resolve()
    store = RuntimeSessionStore(session_root or workspace / ".agent-harness" / "sessions")
    session_id = session_id or store.create_session_id()
    context = ToolExecutionContext(cwd=workspace, workspace=workspace, permission_mode=PermissionLevel.READ_ONLY)
    registry = tool_registry or default_tool_registry()
    dispatcher = HookedToolDispatcher(registry)
    adapter = adapter or FixtureRuntimeAdapter(
        [
            ToolCallDecision("local.cwd", {}),
            ToolCallDecision("local.git_status", {}),
            FinalResponseDecision(f"Read-only runtime session inspected {workspace.name} for goal: {goal}"),
        ]
    )
    if max_steps < 1:
        raise RuntimeAdapterError("max_steps must be at least 1")

    store.append_event(session_id, "session_started", {"goal": goal}, cwd=workspace, workspace=workspace)
    runtime_context = assemble_runtime_context(workspace)
    store.append_event(
        session_id,
        "context_assembled",
        {"sections": cast(JsonValue, runtime_context.to_json())},
        cwd=workspace,
        workspace=workspace,
    )
    observations: list[RuntimeAdapterObservation] = []
    tool_catalog = build_adapter_tool_catalog(registry)
    skill_catalog = build_adapter_skill_catalog(runtime_context)

    for step in range(1, max_steps + 1):
        adapter_input = RuntimeAdapterInput(
            goal=goal,
            context=cast(list[dict[str, JsonValue]], runtime_context.to_json()),
            tools=tool_catalog,
            skill_catalog=skill_catalog,
            observations=tuple(observations),
            permission_mode=PermissionLevel.READ_ONLY.value,
        )
        try:
            decision = coerce_adapter_decision(adapter.decide(adapter_input))
        except Exception as exc:
            return _finish_with_error(
                store,
                session_id,
                workspace,
                message=f"adapter decision failed: {exc}",
                error_type=type(exc).__name__,
            )

        decision_event = store.append_event(
            session_id,
            "adapter_decision",
            {"step": step, "decision": cast(JsonValue, decision.to_json())},
            cwd=workspace,
            workspace=workspace,
        )
        if isinstance(decision, FinalResponseDecision):
            store.append_event(
                session_id,
                "final_response",
                {"summary": decision.summary, "adapter_decision_event_id": decision_event.event_id},
                cwd=workspace,
                workspace=workspace,
            )
            return RuntimeRunResult(
                session_id=session_id,
                session_path=store.session_path(session_id),
                summary=decision.summary,
            )

        call_event = store.append_event(
            session_id,
            "tool_call",
            {
                "tool_id": decision.tool_id,
                "input": decision.tool_input,
                "adapter_decision_event_id": decision_event.event_id,
            },
            cwd=workspace,
            workspace=workspace,
        )
        try:
            result = dispatcher.dispatch(session_id, decision.tool_id, decision.tool_input, context)
        except Exception as exc:
            return _finish_with_error(
                store,
                session_id,
                workspace,
                message=f"adapter-selected tool failed: {exc}",
                error_type=type(exc).__name__,
                adapter_decision_event_id=decision_event.event_id,
            )
        payload = result.to_json()
        payload["tool_call_event_id"] = call_event.event_id
        payload["adapter_decision_event_id"] = decision_event.event_id
        store.append_event(session_id, "tool_result", payload, cwd=workspace, workspace=workspace)
        observation = RuntimeAdapterObservation(
            adapter_decision_event_id=decision_event.event_id,
            tool_call_event_id=call_event.event_id,
            tool_id=decision.tool_id,
            result=payload,
        )
        observations.append(observation)
        store.append_event(session_id, "adapter_observation", observation.to_json(), cwd=workspace, workspace=workspace)
        if result.denied:
            return _finish_with_error(
                store,
                session_id,
                workspace,
                message=f"adapter-selected tool denied: {result.message}",
                error_type="ToolDenied",
                adapter_decision_event_id=decision_event.event_id,
            )

    return _finish_with_error(
        store,
        session_id,
        workspace,
        message=f"adapter loop stopped after max_steps={max_steps}",
        error_type="MaxStepsExceeded",
    )


def resume_read_only_session(session_root: Path, session_id: str) -> RuntimeRecoverySummary:
    store = RuntimeSessionStore(session_root)
    session = store.read_session(session_id)
    summary = summarize_recovery(session)
    if summary.can_resume_read_only:
        store.append_event(session_id, "recovery_marker", {"action": "read-only resume inspected"})
    return summarize_recovery(store.read_session(session_id))


def _finish_with_error(
    store: RuntimeSessionStore,
    session_id: str,
    workspace: Path,
    *,
    message: str,
    error_type: str,
    adapter_decision_event_id: str | None = None,
) -> RuntimeRunResult:
    error_payload: dict[str, JsonValue] = {"message": message, "error_type": error_type}
    final_payload: dict[str, JsonValue] = {"summary": message, "ok": False, "error_type": error_type}
    if adapter_decision_event_id is not None:
        error_payload["adapter_decision_event_id"] = adapter_decision_event_id
        final_payload["adapter_decision_event_id"] = adapter_decision_event_id
    store.append_event(session_id, "error", error_payload, cwd=workspace, workspace=workspace)
    store.append_event(session_id, "final_response", final_payload, cwd=workspace, workspace=workspace)
    return RuntimeRunResult(
        session_id=session_id,
        session_path=store.session_path(session_id),
        summary=message,
        ok=False,
        diagnostics=(message,),
    )
