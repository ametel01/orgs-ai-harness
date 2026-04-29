"""Stable JSON and Markdown artifacts for PR review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.pr_review import ReviewChangedFiles
from orgs_ai_harness.pr_risk import PrRiskReport, build_pr_risk_report
from orgs_ai_harness.review_context import ReviewContext, build_review_context

PR_REVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PrReviewArtifacts:
    json_payload: dict[str, object]
    markdown: str


@dataclass(frozen=True)
class WrittenPrReviewArtifacts:
    artifacts: PrReviewArtifacts
    json_path: Path | None
    markdown_path: Path | None


def build_pr_review_artifacts(root: Path, review: ReviewChangedFiles) -> PrReviewArtifacts:
    """Build deterministic machine and human review artifacts for changed files."""

    root = root.resolve()
    risk = build_pr_risk_report(root, review)
    context = build_review_context(root, review.repo_id, review.changed_files)
    payload = _json_payload(review, risk, context)
    return PrReviewArtifacts(json_payload=payload, markdown=_markdown_payload(payload))


def write_pr_review_artifacts(
    root: Path,
    review: ReviewChangedFiles,
    *,
    json_path: Path | None,
    markdown_path: Path | None,
) -> WrittenPrReviewArtifacts:
    """Write requested review artifacts and return the in-memory payloads."""

    artifacts = build_pr_review_artifacts(root, review)
    resolved_json_path = _write_json(json_path, artifacts.json_payload)
    resolved_markdown_path = _write_text(markdown_path, artifacts.markdown)
    return WrittenPrReviewArtifacts(artifacts, resolved_json_path, resolved_markdown_path)


def _json_payload(review: ReviewChangedFiles, risk: PrRiskReport, context: ReviewContext) -> dict[str, object]:
    return {
        "schema_version": PR_REVIEW_SCHEMA_VERSION,
        "status": "artifact-only",
        "repo_id": review.repo_id,
        "repo_path": review.repo_path.as_posix(),
        "source": review.source,
        "base": review.base,
        "head": review.head,
        "changed_files": list(review.changed_files),
        "risk": {
            "overall": risk.overall_risk.value,
            "items": [
                {
                    "path": item.path,
                    "level": item.level.value,
                    "category": item.category,
                    "reasons": list(item.reasons),
                }
                for item in risk.file_risks
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
        "context": {
            "artifact_root": context.artifact_root.as_posix(),
            "artifacts": [
                {
                    "name": artifact.name,
                    "path": artifact.path,
                    "status": artifact.status,
                    "reason": artifact.reason,
                }
                for artifact in context.artifacts
            ],
            "changed_paths": [
                {
                    "raw_path": path.raw_path,
                    "normalized_path": path.normalized_path,
                    "classification": path.classification,
                    "reason": path.reason,
                    "exists": path.exists,
                }
                for path in context.changed_paths
            ],
            "matched_skills": [
                {
                    "name": skill.name,
                    "path": skill.path,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "matched_paths": list(skill.matched_paths),
                    "match_reasons": list(skill.match_reasons),
                }
                for skill in context.matched_skills
            ],
            "evidence_matches": [
                {
                    "category": match.category,
                    "evidence_paths": list(match.evidence_paths),
                    "changed_paths": list(match.changed_paths),
                }
                for match in context.evidence_matches
            ],
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
            "missing_coverage": [
                {"kind": missing.kind, "path": missing.path, "reason": missing.reason}
                for missing in context.missing_coverage
            ],
        },
    }


def _markdown_payload(payload: dict[str, object]) -> str:
    risk = _dict(payload.get("risk"))
    context = _dict(payload.get("context"))
    lines = [
        f"# PR Review Artifact: {payload['repo_id']}",
        "",
        f"- Schema version: {payload['schema_version']}",
        f"- Status: {payload['status']}",
        f"- Source: {payload['source']}",
        f"- Overall risk: {risk.get('overall', 'unknown')}",
        "",
        "## Changed Files",
        "",
    ]
    changed_files = _list(payload.get("changed_files"))
    lines.extend(f"- `{path}`" for path in changed_files)
    lines.extend(["", "## Risk Items", ""])
    risk_items = _list(risk.get("items"))
    if risk_items:
        for item in risk_items:
            item_dict = _dict(item)
            lines.append(
                f"- `{item_dict.get('path', '-')}`: {item_dict.get('level', '-')}, {item_dict.get('category', '-')}"
            )
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

    lines.extend(["", "## Matched Skills", ""])
    skills = _list(context.get("matched_skills"))
    if skills:
        for skill in skills:
            skill_dict = _dict(skill)
            lines.append(f"- `{skill_dict.get('name', '-')}`: `{skill_dict.get('path', '-')}`")
    else:
        lines.append("- None.")

    lines.extend(["", "## Missing Coverage", ""])
    missing_items = _list(context.get("missing_coverage"))
    if missing_items:
        for missing in missing_items:
            missing_dict = _dict(missing)
            kind = missing_dict.get("kind", "-")
            path = missing_dict.get("path", "-")
            reason = missing_dict.get("reason", "-")
            lines.append(f"- {kind}: `{path}` - {reason}")
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
