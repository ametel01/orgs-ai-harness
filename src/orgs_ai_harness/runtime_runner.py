"""Deterministic runtime run-loop slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_context import assemble_runtime_context
from orgs_ai_harness.runtime_events import RuntimeSessionStore
from orgs_ai_harness.runtime_hooks import HookedToolDispatcher
from orgs_ai_harness.runtime_permissions import PermissionLevel
from orgs_ai_harness.runtime_recovery import RuntimeRecoverySummary, summarize_recovery
from orgs_ai_harness.runtime_tools import ToolExecutionContext, default_tool_registry


@dataclass(frozen=True)
class RuntimeRunResult:
    session_id: str
    session_path: Path
    summary: str


def run_read_only_session(
    workspace: Path,
    goal: str,
    *,
    session_root: Path | None = None,
    session_id: str | None = None,
) -> RuntimeRunResult:
    workspace = workspace.resolve()
    store = RuntimeSessionStore(session_root or workspace / ".agent-harness" / "sessions")
    session_id = session_id or store.create_session_id()
    context = ToolExecutionContext(cwd=workspace, workspace=workspace, permission_mode=PermissionLevel.READ_ONLY)
    dispatcher = HookedToolDispatcher(default_tool_registry())

    store.append_event(session_id, "session_started", {"goal": goal}, cwd=workspace, workspace=workspace)
    runtime_context = assemble_runtime_context(workspace)
    store.append_event(
        session_id,
        "context_assembled",
        {"sections": cast(JsonValue, runtime_context.to_json())},
        cwd=workspace,
        workspace=workspace,
    )

    for tool_id, tool_input in (("local.cwd", {}), ("local.git_status", {})):
        call_event = store.append_event(
            session_id,
            "tool_call",
            {"tool_id": tool_id, "input": tool_input},
            cwd=workspace,
            workspace=workspace,
        )
        result = dispatcher.dispatch(session_id, tool_id, tool_input, context)
        payload = result.to_json()
        payload["tool_call_event_id"] = call_event.event_id
        store.append_event(session_id, "tool_result", payload, cwd=workspace, workspace=workspace)

    summary = f"Read-only runtime session inspected {workspace.name} for goal: {goal}"
    store.append_event(session_id, "final_response", {"summary": summary}, cwd=workspace, workspace=workspace)
    return RuntimeRunResult(session_id=session_id, session_path=store.session_path(session_id), summary=summary)


def resume_read_only_session(session_root: Path, session_id: str) -> RuntimeRecoverySummary:
    store = RuntimeSessionStore(session_root)
    session = store.read_session(session_id)
    summary = summarize_recovery(session)
    if summary.can_resume_read_only:
        store.append_event(session_id, "recovery_marker", {"action": "read-only resume inspected"})
    return summarize_recovery(store.read_session(session_id))
