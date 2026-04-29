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
DEFAULT_TEXT_LIMIT = 8000
DEFAULT_LIST_LIMIT = 200


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
                payload={
                    "required_permission": decision.required.value,
                    "active_permission": decision.active.value,
                    "reason": decision.reason,
                },
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
            tool_id="local.read_file",
            description="Read bounded UTF-8 text content from a file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            required_permission=PermissionLevel.READ_ONLY,
            handler=_read_file_tool,
        )
    )
    registry.register(
        RuntimeTool(
            tool_id="local.list_files",
            description="List workspace files deterministically with bounded results.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
            required_permission=PermissionLevel.READ_ONLY,
            handler=_list_files_tool,
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


def _read_file_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    raw_path = tool_input.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeToolError("local.read_file requires non-empty string input: path")
    max_chars = _positive_int(tool_input.get("max_chars"), DEFAULT_TEXT_LIMIT)
    target = _resolve_workspace_path(context.workspace, raw_path)
    if target is None:
        return _denied_result(
            "local.read_file",
            "path is outside workspace",
            context,
            required=PermissionLevel.READ_ONLY,
        )
    workspace = context.workspace.resolve()
    relative = target.relative_to(workspace).as_posix()
    if _is_unsafe_read_path(relative):
        return _denied_result("local.read_file", "path is protected", context, required=PermissionLevel.READ_ONLY)
    if not target.is_file():
        return ToolResult(
            ok=False,
            tool_id="local.read_file",
            message="file does not exist",
            payload={"path": relative},
            exit_code=1,
        )
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(
            ok=False,
            tool_id="local.read_file",
            message="file is not valid UTF-8 text",
            payload={"path": relative},
            exit_code=1,
        )
    content = _bounded(text, max_chars)
    return ToolResult(
        ok=True,
        tool_id="local.read_file",
        message="file read",
        payload={
            "path": relative,
            "content": content,
            "size": target.stat().st_size,
            "truncated": len(content) != len(text),
        },
    )


def _list_files_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    raw_path = tool_input.get("path", ".")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeToolError("local.list_files requires string input: path when provided")
    limit = _positive_int(tool_input.get("limit"), DEFAULT_LIST_LIMIT)
    root = _resolve_workspace_path(context.workspace, raw_path)
    if root is None:
        return _denied_result(
            "local.list_files",
            "path is outside workspace",
            context,
            required=PermissionLevel.READ_ONLY,
        )
    workspace = context.workspace.resolve()
    relative_root = root.relative_to(workspace).as_posix()
    if relative_root == ".":
        relative_root = ""
    if _is_unsafe_read_path(relative_root):
        return _denied_result("local.list_files", "path is protected", context, required=PermissionLevel.READ_ONLY)
    if not root.exists():
        return ToolResult(
            ok=False,
            tool_id="local.list_files",
            message="path does not exist",
            payload={"path": relative_root or "."},
            exit_code=1,
        )
    candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    files: list[str] = []
    skipped_protected = 0
    for path in candidates:
        relative = path.relative_to(workspace).as_posix()
        if _is_unsafe_read_path(relative):
            skipped_protected += 1
            continue
        if len(files) >= limit:
            break
        files.append(relative)
    visible_count = max(0, len(candidates) - skipped_protected)
    return ToolResult(
        ok=True,
        tool_id="local.list_files",
        message=f"listed {len(files)} file(s)",
        payload={
            "path": relative_root or ".",
            "files": cast(JsonValue, files),
            "truncated": visible_count > len(files),
            "limit": limit,
        },
    )


def _shell_tool(tool_input: JsonObject, context: ToolExecutionContext) -> ToolResult:
    argv = tool_input.get("argv")
    if not isinstance(argv, list) or not all(isinstance(part, str) and part for part in argv):
        raise RuntimeToolError("local.shell requires argv as a non-empty string array")
    command_argv = cast(list[str], argv)
    required = classify_command(command_argv)
    decision = permission_allows(context.permission_mode, required)
    if not decision.allowed:
        return _denied_result(
            "local.shell",
            decision.reason,
            context,
            required=required,
            payload={"argv": cast(JsonValue, command_argv)},
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
    target = _resolve_workspace_path(context.workspace, raw_path)
    if target is None:
        return _denied_result(
            "local.write_file",
            "path is outside workspace",
            context,
            required=PermissionLevel.WORKSPACE_WRITE,
        )
    workspace = context.workspace.resolve()
    relative = target.relative_to(workspace).as_posix()
    if _is_write_protected_path(relative):
        return _denied_result(
            "local.write_file",
            "path is protected",
            context,
            required=PermissionLevel.WORKSPACE_WRITE,
        )
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


def _positive_int(value: JsonValue | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and value > 0:
        return value
    raise RuntimeToolError("limit values must be positive integers")


def _resolve_workspace_path(workspace: Path, raw_path: str) -> Path | None:
    target = (workspace / raw_path).resolve()
    workspace = workspace.resolve()
    if not _is_relative_to(target, workspace):
        return None
    return target


def _is_unsafe_read_path(relative_path: str) -> bool:
    if relative_path in {"", "."}:
        return False
    parts = Path(relative_path).parts
    return ".git" in parts


def _is_write_protected_path(relative_path: str) -> bool:
    return _is_unsafe_read_path(relative_path) or relative_path.startswith("org-agent-skills/repos/")


def _denied_result(
    tool_id: str,
    message: str,
    context: ToolExecutionContext,
    *,
    required: PermissionLevel,
    payload: JsonObject | None = None,
) -> ToolResult:
    diagnostic: JsonObject = dict(payload or {})
    diagnostic.update(
        {
            "active_permission": context.permission_mode.value,
            "required_permission": required.value,
            "reason": message,
        }
    )
    return ToolResult(ok=False, tool_id=tool_id, message=message, payload=diagnostic, denied=True)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
