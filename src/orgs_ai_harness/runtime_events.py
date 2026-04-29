"""Append-only JSONL event storage for runtime sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from orgs_ai_harness.artifact_schemas import JsonValue

RUNTIME_EVENT_TYPES = {
    "session_started",
    "context_assembled",
    "message",
    "tool_call",
    "tool_result",
    "approval_event",
    "error",
    "recovery_marker",
    "final_response",
}


class RuntimeEventError(Exception):
    """Raised when runtime event storage cannot be read or written."""


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    session_id: str
    event_type: str
    timestamp: str
    payload: dict[str, JsonValue]
    cwd: str | None = None
    workspace: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        record: dict[str, JsonValue] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        if self.cwd is not None:
            record["cwd"] = self.cwd
        if self.workspace is not None:
            record["workspace"] = self.workspace
        return record


@dataclass(frozen=True)
class MalformedRuntimeRecord:
    line_number: int
    reason: str
    raw: str


@dataclass(frozen=True)
class RuntimeSessionRead:
    session_id: str
    events: tuple[RuntimeEvent, ...]
    malformed: tuple[MalformedRuntimeRecord, ...]


class RuntimeSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create_session_id(self) -> str:
        return f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"

    def session_path(self, session_id: str) -> Path:
        if "/" in session_id or session_id in {"", ".", ".."}:
            raise RuntimeEventError(f"invalid session id: {session_id}")
        return self.root / f"{session_id}.jsonl"

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
        *,
        cwd: Path | None = None,
        workspace: Path | None = None,
        timestamp: str | None = None,
    ) -> RuntimeEvent:
        if event_type not in RUNTIME_EVENT_TYPES:
            raise RuntimeEventError(f"unsupported runtime event type: {event_type}")
        path = self.session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = RuntimeEvent(
            event_id=f"{session_id}:{_next_event_number(path):04d}",
            session_id=session_id,
            event_type=event_type,
            timestamp=timestamp or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            payload=payload or {},
            cwd=str(cwd.resolve()) if cwd is not None else None,
            workspace=str(workspace.resolve()) if workspace is not None else None,
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.to_json(), sort_keys=True) + "\n")
        return event

    def read_session(self, session_id: str) -> RuntimeSessionRead:
        path = self.session_path(session_id)
        if not path.is_file():
            raise RuntimeEventError(f"session log does not exist: {path}")
        events: list[RuntimeEvent] = []
        malformed: list[MalformedRuntimeRecord] = []
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                raw = json.loads(raw_line)
                events.append(_event_from_json(raw))
            except (json.JSONDecodeError, RuntimeEventError, TypeError) as exc:
                malformed.append(MalformedRuntimeRecord(line_number=line_number, reason=str(exc), raw=raw_line))
        return RuntimeSessionRead(session_id=session_id, events=tuple(events), malformed=tuple(malformed))


def _next_event_number(path: Path) -> int:
    if not path.is_file():
        return 1
    return len(path.read_text(encoding="utf-8").splitlines()) + 1


def _event_from_json(raw: object) -> RuntimeEvent:
    if not isinstance(raw, dict):
        raise RuntimeEventError("record is not an object")
    required = ("event_id", "session_id", "event_type", "timestamp", "payload")
    for field in required:
        if field not in raw:
            raise RuntimeEventError(f"record missing field: {field}")
    if raw["event_type"] not in RUNTIME_EVENT_TYPES:
        raise RuntimeEventError(f"unsupported runtime event type: {raw['event_type']}")
    if not isinstance(raw["payload"], dict):
        raise RuntimeEventError("record payload must be an object")
    return RuntimeEvent(
        event_id=str(raw["event_id"]),
        session_id=str(raw["session_id"]),
        event_type=str(raw["event_type"]),
        timestamp=str(raw["timestamp"]),
        payload=raw["payload"],
        cwd=str(raw["cwd"]) if raw.get("cwd") is not None else None,
        workspace=str(raw["workspace"]) if raw.get("workspace") is not None else None,
    )
