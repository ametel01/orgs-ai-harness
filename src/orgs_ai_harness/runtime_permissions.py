"""Runtime permission levels and command-risk classification."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum


class PermissionLevel(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"
    HIGH_RISK = "high-risk"


class PermissionError(ValueError):
    """Raised when a permission mode cannot be parsed."""


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    required: PermissionLevel
    active: PermissionLevel
    reason: str


_PERMISSION_ORDER = {
    PermissionLevel.READ_ONLY: 0,
    PermissionLevel.WORKSPACE_WRITE: 1,
    PermissionLevel.FULL_ACCESS: 2,
    PermissionLevel.HIGH_RISK: 3,
}

_READ_ONLY_COMMANDS = {
    "pwd",
    "ls",
    "rg",
    "git status",
    "git rev-parse",
    "git log",
}

_WORKSPACE_VALIDATION_PREFIXES = {
    "python -m",
    "uv run pytest",
    "uv run pyright",
    "uv run ruff",
    "uv run harness validate",
    "make test",
    "make verify",
    "make lint",
}

_HIGH_RISK_EXECUTABLES = {"curl", "wget", "ssh", "scp", "rsync", "docker", "gh"}
_HIGH_RISK_GIT = {"push", "pull", "fetch", "clone"}
_DESTRUCTIVE_EXECUTABLES = {"rm", "mv", "chmod", "chown", "sudo"}


def parse_permission_level(raw: str | PermissionLevel) -> PermissionLevel:
    if isinstance(raw, PermissionLevel):
        return raw
    try:
        return PermissionLevel(raw)
    except ValueError as exc:
        raise PermissionError(f"unknown permission level: {raw}") from exc


def permission_allows(active: str | PermissionLevel, required: str | PermissionLevel) -> PermissionDecision:
    active_level = parse_permission_level(active)
    required_level = parse_permission_level(required)
    allowed = _PERMISSION_ORDER[active_level] >= _PERMISSION_ORDER[required_level]
    reason = "allowed" if allowed else f"{required_level.value} permission required"
    return PermissionDecision(allowed=allowed, required=required_level, active=active_level, reason=reason)


def classify_command(argv: list[str] | tuple[str, ...]) -> PermissionLevel:
    """Classify a shell command before execution."""

    if not argv:
        return PermissionLevel.HIGH_RISK
    executable = argv[0]
    if executable in _DESTRUCTIVE_EXECUTABLES or executable in _HIGH_RISK_EXECUTABLES:
        return PermissionLevel.HIGH_RISK
    if executable == "git" and len(argv) > 1 and argv[1] in _HIGH_RISK_GIT:
        return PermissionLevel.HIGH_RISK

    prefix = " ".join(shlex.quote(part) for part in argv[:3])
    two_part_prefix = " ".join(shlex.quote(part) for part in argv[:2])
    one_part_prefix = shlex.quote(executable)
    if (
        prefix in _READ_ONLY_COMMANDS
        or two_part_prefix in _READ_ONLY_COMMANDS
        or one_part_prefix in _READ_ONLY_COMMANDS
    ):
        return PermissionLevel.READ_ONLY
    if prefix in _WORKSPACE_VALIDATION_PREFIXES or two_part_prefix in _WORKSPACE_VALIDATION_PREFIXES:
        return PermissionLevel.WORKSPACE_WRITE
    return PermissionLevel.HIGH_RISK
