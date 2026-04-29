"""Runtime tool registry and built-in local tools."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_permissions import PermissionLevel, classify_command, permission_allows

JsonObject = dict[str, JsonValue]
ToolCallable = Callable[[JsonObject, "ToolExecutionContext"], "ToolResult"]


class RuntimeToolError(Exception):
    """Raised when a runtime tool cannot be found or dispatched."""


@dataclass(frozen=True)
class ToolExecutionContext:
    cwd: Path
    workspace: Path
    permission_mode: PermissionLevel = PermissionLevel.READ_ONLY


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_id: str
    message: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    payload: JsonObject = field(default_factory=dict)
    changed_files: tuple[str, ...] = ()
    denied: bool = False

    def to_json(self) -> JsonObject:
        return {
            "ok": self.ok,
            "tool_id": self.tool_id,
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "payload": self.payload,
            "changed_files": list(self.changed_files),
            "denied": self.denied,
        }


@dataclass(frozen=True)
class RuntimeTool:
    tool_id: str
    description: str
    input_schema: JsonObject
    required_permission: PermissionLevel
    handler: ToolCallable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RuntimeTool] = {}

    def register(self, tool: RuntimeTool) -> None:
        if not tool.tool_id:
            raise RuntimeToolError("tool id cannot be empty")
        if tool.tool_id in self._tools:
            raise RuntimeToolError(f"tool already registered: {tool.tool_id}")
        self._tools[tool.tool_id] = tool

    def get(self, tool_id: str) -> RuntimeTool:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise RuntimeToolError(f"unknown runtime tool: {tool_id}") from exc

    def list_tools(self) -> tuple[RuntimeTool, ...]:
        return tuple(self._tools[tool_id] for tool_id in sorted(self._tools))

    def dispatch(self, tool_id: str, tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(tool_input, dict):
            raise RuntimeToolError("tool input must be an object")
        tool = self.get(tool_id)
        decision = permission_allows(context.permission_mode, tool.required_permission)
        if not decision.allowed:
            return ToolResult(
                ok=False,
                tool_id=tool_id,
                message=decision.reason,
                payload={"required_permission": decision.required.value, "active_permission": decision.active.value},
                denied=True,
            )
        return tool.handler(tool_input, context)


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RuntimeTool(
            tool_id="local.cwd",
            description="Return the current runtime working directory.",
            input_schema={"type": "object", "additionalProperties": False},
            required_permission=PermissionLevel.READ_ONLY,
            handler=_cwd_tool,
        )
    )
    registry.register(
        RuntimeTool(
            tool_id="local.git_status",
            description="Return porcelain git status for the workspace.",
            input_schema={"type": "object", "additionalProperties": False},
            required_permission=PermissionLevel.READ_ONLY,
            handler=_git_status_tool,
        )
    )
    registry.register(
        RuntimeTool(
            tool_id="local.search_text",
            description="Search workspace text files for a literal pattern.",
            input_schema={"type": "object", "properties": {"pattern": {"type": "string"}}},
            required_permission=PermissionLevel.READ_ONLY,
            handler=_search_text_tool,
        )
    )
    registry.register(
        RuntimeTool(
            tool_id="local.shell",
            description="Run an argv-safe local command when permission policy allows it.",
            input_schema={"type": "object", "properties": {"argv": {"type": "array"}}},
            required_permission=PermissionLevel.READ_ONLY,
            handler=_shell_tool,
        )
    )
    registry.register(
        RuntimeTool(
            tool_id="local.write_file",
            description="Write a text file inside the workspace with audit metadata.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
            required_permission=PermissionLevel.WORKSPACE_WRITE,
            handler=_write_file_tool,
        )
    )
    return registry


def _cwd_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    return ToolResult(
        ok=True,
        tool_id="local.cwd",
        message="cwd inspected",
        payload={"cwd": str(context.cwd.resolve()), "workspace": str(context.workspace.resolve())},
    )


def _git_status_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    git = shutil.which("git")
    if git is None:
        return ToolResult(ok=False, tool_id="local.git_status", message="git executable not found", exit_code=127)
    result = subprocess.run(  # nosec B603
        [git, "status", "--short", "--branch"],
        cwd=context.workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    return ToolResult(
        ok=result.returncode == 0,
        tool_id="local.git_status",
        message="git status inspected" if result.returncode == 0 else "git status failed",
        stdout=_bounded(result.stdout),
        stderr=_bounded(result.stderr),
        exit_code=result.returncode,
    )


def _search_text_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise RuntimeToolError("local.search_text requires non-empty string input: pattern")
    matches: list[str] = []
    for path in sorted(context.workspace.rglob("*")):
        if len(matches) >= 50:
            break
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern in text:
            matches.append(path.relative_to(context.workspace).as_posix())
    return ToolResult(
        ok=True,
        tool_id="local.search_text",
        message=f"found {len(matches)} matching file(s)",
        payload={"matches": cast(JsonValue, matches)},
    )


def _shell_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    argv = tool_input.get("argv")
    if not isinstance(argv, list) or not all(isinstance(part, str) and part for part in argv):
        raise RuntimeToolError("local.shell requires argv as a non-empty string array")
    command_argv = cast(list[str], argv)
    required = classify_command(command_argv)
    decision = permission_allows(context.permission_mode, required)
    if not decision.allowed:
        return ToolResult(
            ok=False,
            tool_id="local.shell",
            message=decision.reason,
            payload={"argv": cast(JsonValue, command_argv), "required_permission": required.value},
            denied=True,
        )
    result = subprocess.run(  # nosec B603
        command_argv,
        cwd=context.workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return ToolResult(
        ok=result.returncode == 0,
        tool_id="local.shell",
        message="command completed" if result.returncode == 0 else "command failed",
        stdout=_bounded(result.stdout),
        stderr=_bounded(result.stderr),
        exit_code=result.returncode,
        payload={"argv": cast(JsonValue, command_argv), "required_permission": required.value},
    )


def _write_file_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    raw_path = tool_input.get("path")
    content = tool_input.get("content")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeToolError("local.write_file requires non-empty string input: path")
    if not isinstance(content, str):
        raise RuntimeToolError("local.write_file requires string input: content")
    target = (context.workspace / raw_path).resolve()
    workspace = context.workspace.resolve()
    if not _is_relative_to(target, workspace):
        return ToolResult(ok=False, tool_id="local.write_file", message="path is outside workspace", denied=True)
    relative = target.relative_to(workspace).as_posix()
    if relative.startswith("org-agent-skills/repos/") or relative.startswith(".git/"):
        return ToolResult(ok=False, tool_id="local.write_file", message="path is protected", denied=True)
    before_exists = target.exists()
    before_size = target.stat().st_size if before_exists else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ToolResult(
        ok=True,
        tool_id="local.write_file",
        message="file written",
        payload={
            "path": relative,
            "before_exists": before_exists,
            "before_size": before_size,
            "after_size": target.stat().st_size,
        },
        changed_files=(relative,),
    )


def _bounded(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[output truncated]\n"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
