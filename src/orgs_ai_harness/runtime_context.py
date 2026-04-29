"""Structured startup context assembly for runtime sessions."""

from __future__ import annotations

import platform
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.org_pack import DEFAULT_PACK_DIR


@dataclass(frozen=True)
class RuntimeContextSection:
    name: str
    payload: dict[str, JsonValue]


@dataclass(frozen=True)
class RuntimeContext:
    sections: tuple[RuntimeContextSection, ...]

    def to_json(self) -> list[dict[str, JsonValue]]:
        return [{"name": section.name, "payload": section.payload} for section in self.sections]


def assemble_runtime_context(workspace: Path, *, budget_chars: int = 12000) -> RuntimeContext:
    """Collect bounded deterministic context for a runtime session."""

    workspace = workspace.resolve()
    sections = [
        _workspace_section(workspace),
        _git_section(workspace),
        _instructions_section(workspace, budget_chars=max(budget_chars // 3, 1000)),
        _harness_section(workspace, budget_chars=max(budget_chars // 3, 1000)),
        _skills_section(workspace, budget_chars=max(budget_chars // 3, 1000)),
    ]
    return RuntimeContext(tuple(sections))


def _workspace_section(workspace: Path) -> RuntimeContextSection:
    return RuntimeContextSection(
        name="workspace",
        payload={
            "cwd": str(workspace),
            "os": platform.system(),
            "platform": platform.platform(),
            "date": date.today().isoformat(),
            "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
    )


def _git_section(workspace: Path) -> RuntimeContextSection:
    branch = _run_git(workspace, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    status = _run_git(workspace, ["git", "status", "--short", "--branch"])
    commits = _run_git(workspace, ["git", "log", "--oneline", "-5"])
    payload: dict[str, JsonValue] = {
        "available": branch is not None,
        "branch": branch.strip() if branch else None,
        "status": cast(JsonValue, status.strip().splitlines() if status else []),
        "recent_commits": cast(JsonValue, commits.strip().splitlines() if commits else []),
    }
    return RuntimeContextSection(
        name="git",
        payload=payload,
    )


def _instructions_section(workspace: Path, *, budget_chars: int) -> RuntimeContextSection:
    names = ("AGENTS.md", "CLAUDE.md", ".windsurfrules")
    discovered: list[dict[str, JsonValue]] = []
    for directory in (workspace, *workspace.parents):
        for name in names:
            path = directory / name
            if path.is_file():
                discovered.append(_bounded_file_record(path, workspace, budget_chars))
        cursor_rules = directory / ".cursor" / "rules"
        if cursor_rules.is_dir():
            for path in sorted(cursor_rules.rglob("*")):
                if path.is_file():
                    discovered.append(_bounded_file_record(path, workspace, budget_chars))
    return RuntimeContextSection(name="instructions", payload={"files": cast(JsonValue, discovered[:12])})


def _harness_section(workspace: Path, *, budget_chars: int) -> RuntimeContextSection:
    pack_root = workspace / DEFAULT_PACK_DIR
    cache_root = workspace / ".agent-harness"
    payload: dict[str, JsonValue] = {
        "org_pack_present": (pack_root / "harness.yml").is_file(),
        "cache_present": cache_root.exists(),
        "cache_entries": [],
    }
    if (pack_root / "harness.yml").is_file():
        payload["harness_yml"] = _bounded((pack_root / "harness.yml").read_text(encoding="utf-8"), budget_chars)
    if cache_root.exists():
        payload["cache_entries"] = cast(
            JsonValue,
            [path.relative_to(workspace).as_posix() for path in sorted(cache_root.rglob("*")) if path.is_file()][:25],
        )
    return RuntimeContextSection(name="harness", payload=payload)


def _skills_section(workspace: Path, *, budget_chars: int) -> RuntimeContextSection:
    pack_root = workspace / DEFAULT_PACK_DIR
    records: list[dict[str, JsonValue]] = []
    for skills_root in (pack_root / "org" / "skills", pack_root / "repos"):
        if not skills_root.exists():
            continue
        for path in sorted(skills_root.rglob("SKILL.md")):
            records.append(_bounded_file_record(path, workspace, budget_chars))
            if len(records) >= 20:
                break
    resolvers = []
    for path in sorted(pack_root.rglob("resolvers.yml")) if pack_root.exists() else []:
        resolvers.append(_bounded_file_record(path, workspace, budget_chars))
        if len(resolvers) >= 10:
            break
    return RuntimeContextSection(
        name="skills",
        payload={"skills": cast(JsonValue, records[:20]), "resolvers": cast(JsonValue, resolvers[:10])},
    )


def _bounded_file_record(path: Path, workspace: Path, budget_chars: int) -> dict[str, JsonValue]:
    try:
        relative = path.relative_to(workspace).as_posix()
    except ValueError:
        relative = str(path)
    return {
        "path": relative,
        "content": _bounded(path.read_text(encoding="utf-8", errors="replace"), budget_chars),
    }


def _run_git(workspace: Path, argv: list[str]) -> str | None:
    result = subprocess.run(argv, cwd=workspace, text=True, capture_output=True, check=False)  # nosec B603
    if result.returncode != 0:
        return None
    return result.stdout


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[context truncated]\n"
