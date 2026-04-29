"""Stable JSON and Markdown artifacts for release readiness."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.release_context import ReleaseContext, build_release_context
from orgs_ai_harness.release_readiness import ReleaseReadinessInput
from orgs_ai_harness.release_risk import ReleaseRiskReport, build_release_risk_report

RELEASE_READINESS_SCHEMA_VERSION = 1


class ReleaseArtifactError(Exception):
    """Raised when release readiness artifacts cannot be built."""


@dataclass(frozen=True)
class ReleaseReadinessArtifacts:
    json_payload: dict[str, object]
    markdown: str


@dataclass(frozen=True)
class WrittenReleaseReadinessArtifacts:
    artifacts: ReleaseReadinessArtifacts
    json_path: Path | None
    markdown_path: Path | None


def resolve_release_changed_files(
    readiness: ReleaseReadinessInput,
    *,
    files: tuple[str, ...] = (),
    files_from: Path | None = None,
) -> tuple[str, ...]:
    """Resolve deterministic changed-file evidence for a release readiness artifact."""

    input_modes = sum((bool(files), files_from is not None, readiness.base is not None or readiness.head is not None))
    if input_modes > 1:
        raise ReleaseArtifactError(
            "release readiness accepts at most one changed-file input: --files, --files-from, or --base/--head"
        )

    if files:
        return _normalize_changed_files(files)
    if files_from is not None:
        return _normalize_changed_files(tuple(_read_files_from(files_from).splitlines()))
    if readiness.base is not None and readiness.head is not None:
        return _changed_files_from_git(readiness)
    return ()


def build_release_readiness_artifacts(
    root: Path,
    readiness: ReleaseReadinessInput,
    *,
    changed_files: tuple[str, ...] = (),
) -> ReleaseReadinessArtifacts:
    """Build deterministic machine and human release readiness artifacts."""

    root = root.resolve()
    normalized_changed_files = _normalize_changed_files(changed_files) if changed_files else ()
    context = build_release_context(root, readiness.repo_id)
    risk = build_release_risk_report(root, readiness, changed_files=normalized_changed_files)
    payload = _json_payload(readiness, context, risk)
    return ReleaseReadinessArtifacts(json_payload=payload, markdown=_markdown_payload(payload))


def write_release_readiness_artifacts(
    root: Path,
    readiness: ReleaseReadinessInput,
    *,
    changed_files: tuple[str, ...],
    json_path: Path | None,
    markdown_path: Path | None,
) -> WrittenReleaseReadinessArtifacts:
    """Write requested release readiness artifacts and return the in-memory payloads."""

    artifacts = build_release_readiness_artifacts(root, readiness, changed_files=changed_files)
    resolved_json_path = _write_json(json_path, artifacts.json_payload)
    resolved_markdown_path = _write_text(markdown_path, artifacts.markdown)
    return WrittenReleaseReadinessArtifacts(artifacts, resolved_json_path, resolved_markdown_path)


def _json_payload(
    readiness: ReleaseReadinessInput,
    context: ReleaseContext,
    risk: ReleaseRiskReport,
) -> dict[str, object]:
    return {
        "schema_version": RELEASE_READINESS_SCHEMA_VERSION,
        "status": readiness.status,
        "repo_id": readiness.repo_id,
        "repo_path": readiness.repo_path.as_posix(),
        "release": {
            "version": readiness.version,
            "base": readiness.base,
            "head": readiness.head,
            "changed_files": list(risk.changed_files),
        },
        "lifecycle": {
            "registry_status": context.lifecycle.registry_status,
            "active": context.lifecycle.active,
            "external": context.lifecycle.external,
            "pack_ref": context.lifecycle.pack_ref,
            "supported": context.lifecycle.supported,
            "reason": context.lifecycle.reason,
            "approval_status": context.lifecycle.approval_status,
            "approval_decision": context.lifecycle.approval_decision,
            "approval_verified": context.lifecycle.approval_verified,
            "eval_status": context.lifecycle.eval_status,
            "eval_pass_rate": context.lifecycle.eval_pass_rate,
            "eval_task_count": context.lifecycle.eval_task_count,
        },
        "context": {
            "artifact_root": context.artifact_root.as_posix(),
            "local_repo": {
                "configured_path": context.local_repo.configured_path,
                "resolved_path": context.local_repo.resolved_path.as_posix()
                if context.local_repo.resolved_path is not None
                else None,
                "status": context.local_repo.status,
                "reason": context.local_repo.reason,
            },
            "artifacts": [
                {
                    "name": artifact.name,
                    "path": artifact.path,
                    "status": artifact.status,
                    "reason": artifact.reason,
                }
                for artifact in context.artifacts
            ],
            "pack_report": {
                "path": context.pack_report.path,
                "title": context.pack_report.title,
                "status": context.pack_report.status,
                "fields": [{"key": key, "value": value} for key, value in context.pack_report.fields],
            }
            if context.pack_report is not None
            else None,
            "unknowns": [
                {
                    "id": unknown.id,
                    "question": unknown.question,
                    "severity": unknown.severity,
                    "status": unknown.status,
                    "evidence_paths": list(unknown.evidence_paths),
                }
                for unknown in context.unknowns
            ],
            "scan_evidence": [{"category": item.category, "paths": list(item.paths)} for item in context.scan_evidence],
            "generated_skills": [
                {
                    "name": skill.name,
                    "path": skill.path,
                    "description": skill.description,
                }
                for skill in context.generated_skills
            ],
            "generated_resolvers": [
                {
                    "skill": resolver.skill,
                    "intent": resolver.intent,
                    "when": list(resolver.when),
                }
                for resolver in context.generated_resolvers
            ],
        },
        "release_evidence": [
            {
                "category": item.category,
                "path": item.path,
                "status": item.status,
                "detail": item.detail,
            }
            for item in context.local_release_evidence
        ],
        "missing_evidence": [
            {"kind": missing.kind, "path": missing.path, "reason": missing.reason}
            for missing in context.missing_evidence
        ],
        "risk": {
            "overall": risk.overall_risk.value,
            "items": [
                {
                    "level": item.level.value,
                    "category": item.category,
                    "reasons": list(item.reasons),
                    "evidence": list(item.evidence),
                }
                for item in risk.items
            ],
            "suggested_commands": [
                {
                    "command": suggestion.command,
                    "permission": suggestion.permission.value,
                    "sources": list(suggestion.sources),
                    "reasons": list(suggestion.reasons),
                }
                for suggestion in risk.validation_suggestions
            ],
            "suggested_evals": [
                {
                    "eval_id": suggestion.eval_id,
                    "matched_files": list(suggestion.matched_files),
                    "expected_files": list(suggestion.expected_files),
                    "source": suggestion.source,
                }
                for suggestion in risk.eval_suggestions
            ],
            "warnings": [
                {"code": warning.code, "source": warning.source, "message": warning.message}
                for warning in risk.warnings
            ],
        },
    }


def _markdown_payload(payload: dict[str, object]) -> str:
    release = _dict(payload.get("release"))
    lifecycle = _dict(payload.get("lifecycle"))
    risk = _dict(payload.get("risk"))
    lines = [
        f"# Release Readiness Artifact: {payload['repo_id']}",
        "",
        f"- Schema version: {payload['schema_version']}",
        f"- Status: {payload['status']}",
        f"- Version: {release.get('version') or '-'}",
        f"- Base: {release.get('base') or '-'}",
        f"- Head: {release.get('head') or '-'}",
        f"- Lifecycle: {lifecycle.get('registry_status', '-')}",
        f"- Overall risk: {risk.get('overall', 'unknown')}",
        "",
        "## Changed Files",
        "",
    ]
    _append_string_list(lines, _list(release.get("changed_files")), empty="None.")

    lines.extend(["", "## Risk Items", ""])
    risk_items = _list(risk.get("items"))
    if risk_items:
        for item in risk_items:
            item_dict = _dict(item)
            evidence = ", ".join(f"`{value}`" for value in _list(item_dict.get("evidence"))) or "-"
            lines.append(f"- {item_dict.get('level', '-')}: {item_dict.get('category', '-')} - {evidence}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Suggested Checks", ""])
    commands = _list(risk.get("suggested_commands"))
    if commands:
        for command in commands:
            command_dict = _dict(command)
            lines.append(f"- `{command_dict.get('command', '-')}` ({command_dict.get('permission', '-')})")
    else:
        lines.append("- None.")

    lines.extend(["", "## Suggested Evals", ""])
    evals = _list(risk.get("suggested_evals"))
    if evals:
        for eval_item in evals:
            eval_dict = _dict(eval_item)
            lines.append(f"- `{eval_dict.get('eval_id', '-')}`")
    else:
        lines.append("- None.")

    lines.extend(["", "## Release Evidence", ""])
    release_evidence = _list(payload.get("release_evidence"))
    if release_evidence:
        for item in release_evidence:
            item_dict = _dict(item)
            detail = f" - {item_dict.get('detail')}" if item_dict.get("detail") else ""
            lines.append(
                f"- {item_dict.get('category', '-')}: `{item_dict.get('path', '-')}` "
                f"({item_dict.get('status', '-')}){detail}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Missing Evidence", ""])
    missing_items = _list(payload.get("missing_evidence"))
    if missing_items:
        for missing in missing_items:
            missing_dict = _dict(missing)
            lines.append(
                f"- {missing_dict.get('kind', '-')}: `{missing_dict.get('path', '-')}` - "
                f"{missing_dict.get('reason', '-')}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Warnings", ""])
    warnings = _list(risk.get("warnings"))
    if warnings:
        for warning in warnings:
            warning_dict = _dict(warning)
            lines.append(f"- {warning_dict.get('code', '-')}: {warning_dict.get('message', '-')}")
    else:
        lines.append("- None.")

    return "\n".join(lines) + "\n"


def _append_string_list(lines: list[str], values: list[object], *, empty: str) -> None:
    if values:
        lines.extend(f"- `{value}`" for value in values)
    else:
        lines.append(f"- {empty}")


def _normalize_changed_files(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        raw_value = raw_path.strip()
        if not raw_value:
            continue
        path = Path(raw_value)
        if path.is_absolute():
            raise ReleaseArtifactError(f"changed file must be repo-relative: {raw_value}")
        parts = path.parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ReleaseArtifactError(f"changed file must not contain traversal segments: {raw_value}")
        if ".git" in parts:
            raise ReleaseArtifactError(f"changed file must not be inside .git: {raw_value}")
        rendered = path.as_posix()
        if rendered not in seen:
            normalized.append(rendered)
            seen.add(rendered)
    return tuple(sorted(normalized))


def _read_files_from(files_from: Path) -> str:
    path = files_from.expanduser().resolve()
    if not path.is_file():
        raise ReleaseArtifactError(f"changed-file input does not exist: {path}")
    return path.read_text(encoding="utf-8")


def _changed_files_from_git(readiness: ReleaseReadinessInput) -> tuple[str, ...]:
    if readiness.base is None or readiness.head is None:
        return ()
    git = shutil.which("git")
    if git is None:
        raise ReleaseArtifactError("git executable not found")

    result = subprocess.run(  # nosec B603
        [git, "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{readiness.base}..{readiness.head}"],
        cwd=readiness.repo_path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git diff exited with code {result.returncode}"
        raise ReleaseArtifactError(f"cannot resolve release changed files: {detail}")
    return _normalize_changed_files(tuple(result.stdout.splitlines()))


def _write_json(path: Path | None, payload: dict[str, object]) -> Path | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return resolved


def _write_text(path: Path | None, content: str) -> Path | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return resolved


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
