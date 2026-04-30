"""Stable JSON and Markdown artifacts for dependency campaigns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.dependency_context import DependencyInventory
from orgs_ai_harness.dependency_risk import DependencyRiskReport

DEPENDENCY_CAMPAIGN_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DependencyCampaignArtifacts:
    json_payload: dict[str, object]
    markdown: str


@dataclass(frozen=True)
class WrittenDependencyCampaignArtifacts:
    artifacts: DependencyCampaignArtifacts
    json_path: Path | None
    markdown_path: Path | None


def build_dependency_campaign_artifacts(
    inventory: DependencyInventory,
    risk: DependencyRiskReport,
) -> DependencyCampaignArtifacts:
    """Build deterministic machine and human dependency campaign artifacts."""

    payload = _json_payload(inventory, risk)
    return DependencyCampaignArtifacts(json_payload=payload, markdown=_markdown_payload(payload))


def write_dependency_campaign_artifacts(
    inventory: DependencyInventory,
    risk: DependencyRiskReport,
    *,
    json_path: Path | None,
    markdown_path: Path | None,
) -> WrittenDependencyCampaignArtifacts:
    """Write requested dependency campaign artifacts and return the in-memory payloads."""

    artifacts = build_dependency_campaign_artifacts(inventory, risk)
    resolved_json_path = _write_json(json_path, artifacts.json_payload)
    resolved_markdown_path = _write_text(markdown_path, artifacts.markdown)
    return WrittenDependencyCampaignArtifacts(artifacts, resolved_json_path, resolved_markdown_path)


def _json_payload(inventory: DependencyInventory, risk: DependencyRiskReport) -> dict[str, object]:
    risk_by_repo = {repo.repo_id: repo for repo in risk.repos}
    return {
        "schema_version": DEPENDENCY_CAMPAIGN_SCHEMA_VERSION,
        "status": "artifact-only",
        "campaign": {
            "name": inventory.campaign_name,
            "package_filters": list(inventory.package_filters),
        },
        "summary": {
            "overall_risk": risk.overall_risk.value,
            "eligible_repos": len(inventory.repos),
            "skipped_repos": len(inventory.skipped_repos),
        },
        "repos": [_repo_payload(repo, risk_by_repo.get(repo.repo_id)) for repo in inventory.repos],
        "rollout_plan": [
            {
                "position": step.position,
                "repo_id": step.repo_id,
                "risk": step.risk.value,
                "suggested_commands": list(step.suggested_commands),
                "suggested_evals": list(step.suggested_evals),
                "reasons": list(step.reasons),
            }
            for step in risk.rollout_plan
        ],
        "skipped_repos": [
            {"repo_id": skipped.repo_id, "reason": skipped.reason} for skipped in inventory.skipped_repos
        ],
        "warnings": [
            {"code": warning.code, "source": warning.source, "message": warning.message} for warning in risk.warnings
        ],
    }


def _repo_payload(repo, repo_risk) -> dict[str, object]:
    return {
        "repo_id": repo.repo_id,
        "repo_name": repo.repo_name,
        "repo_path": repo.repo_path.as_posix(),
        "lifecycle_status": repo.lifecycle_status,
        "dependency_files": [
            {
                "path": item.path,
                "ecosystem": item.ecosystem,
                "manager": item.manager,
                "status": item.status,
                "package_name": item.package_name,
                "dependencies": list(item.dependencies),
                "dev_dependencies": list(item.dev_dependencies),
                "detail": item.detail,
            }
            for item in repo.dependency_files
        ],
        "lockfiles": [
            {"path": item.path, "ecosystem": item.ecosystem, "manager": item.manager, "status": item.status}
            for item in repo.lockfiles
        ],
        "package_manager_evidence": [
            {
                "ecosystem": item.ecosystem,
                "manager": item.manager,
                "source": item.source,
                "detail": item.detail,
            }
            for item in repo.package_manager_evidence
        ],
        "generated_pack": {
            "coverage_status": repo.generated_pack.coverage_status,
            "pack_ref": repo.generated_pack.pack_ref,
            "approval_status": repo.generated_pack.approval_status,
            "approval_verified": repo.generated_pack.approval_verified,
            "eval_task_count": repo.generated_pack.eval_task_count,
            "skills_status": repo.generated_pack.skills_status,
            "resolvers_status": repo.generated_pack.resolvers_status,
            "scan_status": repo.generated_pack.scan_status,
        },
        "missing_evidence": [
            {"kind": item.kind, "path": item.path, "reason": item.reason} for item in repo.missing_evidence
        ],
        "risk": _repo_risk_payload(repo_risk),
        "warnings": [
            {"code": warning.code, "source": warning.source, "message": warning.message} for warning in repo.warnings
        ],
    }


def _repo_risk_payload(repo_risk) -> dict[str, object]:
    if repo_risk is None:
        return {"overall": "unknown", "items": [], "suggested_commands": [], "suggested_evals": [], "warnings": []}
    return {
        "overall": repo_risk.overall_risk.value,
        "items": [
            {
                "level": item.level.value,
                "category": item.category,
                "reasons": list(item.reasons),
                "evidence": list(item.evidence),
            }
            for item in repo_risk.items
        ],
        "suggested_commands": [
            {
                "command": suggestion.command,
                "permission": suggestion.permission.value,
                "sources": list(suggestion.sources),
                "reasons": list(suggestion.reasons),
            }
            for suggestion in repo_risk.validation_suggestions
        ],
        "suggested_evals": [
            {
                "eval_id": suggestion.eval_id,
                "matched_files": list(suggestion.matched_files),
                "expected_files": list(suggestion.expected_files),
                "source": suggestion.source,
            }
            for suggestion in repo_risk.eval_suggestions
        ],
        "warnings": [
            {"code": warning.code, "source": warning.source, "message": warning.message}
            for warning in repo_risk.warnings
        ],
    }


def _markdown_payload(payload: dict[str, object]) -> str:
    campaign = _dict(payload.get("campaign"))
    summary = _dict(payload.get("summary"))
    lines = [
        f"# Dependency Campaign Artifact: {campaign.get('name', '-')}",
        "",
        f"- Schema version: {payload['schema_version']}",
        f"- Status: {payload['status']}",
        f"- Overall risk: {summary.get('overall_risk', 'unknown')}",
        f"- Eligible repos: {summary.get('eligible_repos', 0)}",
        f"- Skipped repos: {summary.get('skipped_repos', 0)}",
        "",
        "## Package Filters",
        "",
    ]
    _append_string_list(lines, _list(campaign.get("package_filters")), empty="None.")

    lines.extend(["", "## Rollout Plan", ""])
    rollout = _list(payload.get("rollout_plan"))
    if rollout:
        for step in rollout:
            step_dict = _dict(step)
            lines.append(
                f"- {step_dict.get('position', '-')}. `{step_dict.get('repo_id', '-')}` ({step_dict.get('risk', '-')})"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Repositories", ""])
    for repo in _list(payload.get("repos")):
        repo_dict = _dict(repo)
        risk = _dict(repo_dict.get("risk"))
        lines.append(f"### {repo_dict.get('repo_id', '-')}")
        lines.append(f"- Lifecycle: {repo_dict.get('lifecycle_status', '-')}")
        lines.append(f"- Risk: {risk.get('overall', '-')}")
        lines.append(f"- Dependency files: {len(_list(repo_dict.get('dependency_files')))}")
        lines.append(f"- Lockfiles: {len(_list(repo_dict.get('lockfiles')))}")
        lines.append("")
        lines.append("Risk items:")
        risk_items = _list(risk.get("items"))
        if risk_items:
            for item in risk_items:
                item_dict = _dict(item)
                evidence = ", ".join(f"`{value}`" for value in _list(item_dict.get("evidence"))) or "-"
                lines.append(f"- {item_dict.get('level', '-')}: {item_dict.get('category', '-')} - {evidence}")
        else:
            lines.append("- None.")
        lines.append("")

    lines.extend(["## Suggested Checks", ""])
    for repo in _list(payload.get("repos")):
        repo_dict = _dict(repo)
        risk = _dict(repo_dict.get("risk"))
        commands = _list(risk.get("suggested_commands"))
        if commands:
            lines.append(f"### {repo_dict.get('repo_id', '-')}")
            for command in commands:
                command_dict = _dict(command)
                lines.append(f"- `{command_dict.get('command', '-')}` ({command_dict.get('permission', '-')})")
    if not any(_list(_dict(_dict(repo).get("risk")).get("suggested_commands")) for repo in _list(payload.get("repos"))):
        lines.append("- None.")

    lines.extend(["", "## Suggested Evals", ""])
    for repo in _list(payload.get("repos")):
        repo_dict = _dict(repo)
        risk = _dict(repo_dict.get("risk"))
        evals = _list(risk.get("suggested_evals"))
        if evals:
            lines.append(f"### {repo_dict.get('repo_id', '-')}")
            for eval_item in evals:
                eval_dict = _dict(eval_item)
                lines.append(f"- `{eval_dict.get('eval_id', '-')}`")
    if not any(_list(_dict(_dict(repo).get("risk")).get("suggested_evals")) for repo in _list(payload.get("repos"))):
        lines.append("- None.")

    lines.extend(["", "## Missing Evidence", ""])
    missing_seen = False
    for repo in _list(payload.get("repos")):
        repo_dict = _dict(repo)
        for missing in _list(repo_dict.get("missing_evidence")):
            missing_seen = True
            missing_dict = _dict(missing)
            lines.append(
                f"- `{repo_dict.get('repo_id', '-')}` {missing_dict.get('kind', '-')}: "
                f"`{missing_dict.get('path', '-')}` - {missing_dict.get('reason', '-')}"
            )
    if not missing_seen:
        lines.append("- None.")

    lines.extend(["", "## Skipped Repos", ""])
    skipped = _list(payload.get("skipped_repos"))
    if skipped:
        for item in skipped:
            skipped_dict = _dict(item)
            lines.append(f"- `{skipped_dict.get('repo_id', '-')}` - {skipped_dict.get('reason', '-')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Warnings", ""])
    warnings = _list(payload.get("warnings"))
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
