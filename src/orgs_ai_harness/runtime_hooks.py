"""Pre-tool and post-tool lifecycle hooks for runtime dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_permissions import PermissionLevel
from orgs_ai_harness.runtime_tools import ToolExecutionContext, ToolRegistry, ToolResult

JsonObject = dict[str, JsonValue]


@dataclass(frozen=True)
class ToolHookContext:
    session_id: str
    tool_id: str
    tool_input: JsonObject
    permission_mode: PermissionLevel
    cwd: Path
    workspace: Path
    prior_decision: JsonObject


@dataclass(frozen=True)
class ToolHookDecision:
    allowed: bool
    reason: str = "allowed"
    metadata: JsonObject | None = None


class PreToolHook(Protocol):
    def __call__(self, context: ToolHookContext) -> ToolHookDecision: ...


class PostToolHook(Protocol):
    def __call__(self, context: ToolHookContext, result: ToolResult) -> JsonObject | None: ...


class HookedToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        pre_hooks: tuple[PreToolHook, ...] = (),
        post_hooks: tuple[PostToolHook, ...] = (),
    ) -> None:
        self.registry = registry
        self.pre_hooks = pre_hooks
        self.post_hooks = post_hooks

    def dispatch(
        self,
        session_id: str,
        tool_id: str,
        tool_input: JsonObject,
        context: ToolExecutionContext,
    ) -> ToolResult:
        hook_context = ToolHookContext(
            session_id=session_id,
            tool_id=tool_id,
            tool_input=tool_input,
            permission_mode=context.permission_mode,
            cwd=context.cwd,
            workspace=context.workspace,
            prior_decision={},
        )
        for hook in self.pre_hooks:
            try:
                decision = hook(hook_context)
            except Exception as exc:
                return ToolResult(
                    ok=False,
                    tool_id=tool_id,
                    message=f"pre-tool hook failed closed: {exc}",
                    payload={"hook_error": str(exc)},
                    denied=True,
                )
            if not decision.allowed:
                return ToolResult(
                    ok=False,
                    tool_id=tool_id,
                    message=decision.reason,
                    payload=decision.metadata or {},
                    denied=True,
                )

        result = self.registry.dispatch(tool_id, tool_input, context)
        warnings: list[JsonObject] = []
        for hook in self.post_hooks:
            try:
                warning = hook(hook_context, result)
            except Exception as exc:
                warning = {"hook_error": str(exc)}
            if warning:
                warnings.append(cast(JsonObject, warning))
        if not warnings:
            return result
        payload = dict(result.payload)
        payload["hook_warnings"] = cast(JsonValue, warnings)
        return ToolResult(
            ok=result.ok,
            tool_id=result.tool_id,
            message=result.message,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            payload=payload,
            changed_files=result.changed_files,
            denied=result.denied,
        )
