"""Runtime session recovery and resume summaries."""

from __future__ import annotations

from dataclasses import dataclass

from orgs_ai_harness.runtime_events import RuntimeEvent, RuntimeSessionRead


@dataclass(frozen=True)
class RuntimeRecoverySummary:
    session_id: str
    event_count: int
    malformed_count: int
    latest_recovery_marker: RuntimeEvent | None
    latest_error: RuntimeEvent | None
    pending_adapter_decision: RuntimeEvent | None
    pending_tool_call: RuntimeEvent | None
    final_response: RuntimeEvent | None

    @property
    def can_resume_read_only(self) -> bool:
        return self.final_response is None and self.pending_adapter_decision is None and self.pending_tool_call is None


def summarize_recovery(session: RuntimeSessionRead) -> RuntimeRecoverySummary:
    latest_recovery_marker: RuntimeEvent | None = None
    latest_error: RuntimeEvent | None = None
    pending_adapter_decision: RuntimeEvent | None = None
    pending_tool_call: RuntimeEvent | None = None
    final_response: RuntimeEvent | None = None
    completed_adapter_decisions: set[str] = set()
    completed_tool_calls: set[str] = set()

    for event in session.events:
        if event.event_type == "recovery_marker":
            latest_recovery_marker = event
        elif event.event_type == "error":
            latest_error = event
        elif event.event_type == "adapter_decision":
            pending_adapter_decision = event
        elif event.event_type == "adapter_observation":
            decision_id = event.payload.get("adapter_decision_event_id")
            if isinstance(decision_id, str):
                completed_adapter_decisions.add(decision_id)
                if pending_adapter_decision is not None and pending_adapter_decision.event_id == decision_id:
                    pending_adapter_decision = None
        elif event.event_type == "tool_call":
            pending_tool_call = event
        elif event.event_type == "tool_result":
            call_id = event.payload.get("tool_call_event_id")
            if isinstance(call_id, str):
                completed_tool_calls.add(call_id)
                if pending_tool_call is not None and pending_tool_call.event_id == call_id:
                    pending_tool_call = None
        elif event.event_type == "final_response":
            final_response = event
            decision_id = event.payload.get("adapter_decision_event_id")
            if isinstance(decision_id, str):
                completed_adapter_decisions.add(decision_id)
                if pending_adapter_decision is not None and pending_adapter_decision.event_id == decision_id:
                    pending_adapter_decision = None

    if pending_tool_call is not None and pending_tool_call.event_id in completed_tool_calls:
        pending_tool_call = None
    if pending_adapter_decision is not None and pending_adapter_decision.event_id in completed_adapter_decisions:
        pending_adapter_decision = None

    return RuntimeRecoverySummary(
        session_id=session.session_id,
        event_count=len(session.events),
        malformed_count=len(session.malformed),
        latest_recovery_marker=latest_recovery_marker,
        latest_error=latest_error,
        pending_adapter_decision=pending_adapter_decision,
        pending_tool_call=pending_tool_call,
        final_response=final_response,
    )
