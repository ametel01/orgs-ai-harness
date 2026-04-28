"""Human approval and protection metadata for generated repo packs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries, save_repo_entries


class ApprovalError(Exception):
    """Raised when repo pack approval cannot be completed."""


@dataclass(frozen=True)
class ApprovalResult:
    repo_id: str
    approval_path: Path
    approved_artifacts: tuple[str, ...]
    excluded_artifacts: tuple[str, ...]
    trace_path: Path


@dataclass(frozen=True)
class RejectionResult:
    repo_id: str
    rejection_path: Path
    trace_path: Path


APPROVAL_FILE = "approval.yml"
APPROVAL_TRACE_FILE = "approval-events.jsonl"


def approve_repo_all(root: Path, repo_id: str, *, rationale: str | None = None) -> ApprovalResult:
    """Approve every generated artifact in a draft pack."""

    return approve_repo(root, repo_id, exclusions=(), rationale=rationale)


def approve_repo(
    root: Path,
    repo_id: str,
    *,
    exclusions: tuple[str, ...] = (),
    rationale: str | None = None,
) -> ApprovalResult:
    """Approve a draft pack while optionally excluding generated artifacts."""

    root = root.resolve()
    entry = _find_draft_repo(root, repo_id)
    artifact_root = root / "repos" / entry.id
    artifacts = _artifact_inventory(root, artifact_root)
    if not artifacts:
        raise ApprovalError(f"repo {entry.id} has no generated draft artifacts to approve")
    excluded_artifacts = _resolve_exclusions(root, artifact_root, artifacts, exclusions)
    approved_artifacts = [artifact for artifact in artifacts if artifact not in set(excluded_artifacts)]
    if not approved_artifacts:
        raise ApprovalError(f"repo {entry.id} approval cannot exclude every generated artifact")

    timestamp = _timestamp()
    approval_path = artifact_root / APPROVAL_FILE
    pack_ref = approval_path.relative_to(root).as_posix()
    protected_artifacts = [
        {
            "path": artifact,
            "sha256": _sha256(root / artifact),
            "protected": True,
        }
        for artifact in approved_artifacts
    ]
    approval_metadata = {
        "schema_version": 1,
        "repo_id": entry.id,
        "status": "approved-unverified",
        "decision": "approved",
        "pack_ref": pack_ref,
        "actor": "user",
        "timestamp": timestamp,
        "rationale": rationale or _default_approval_rationale(excluded_artifacts),
        "approved_artifacts": approved_artifacts,
        "excluded_artifacts": excluded_artifacts,
        "protected_artifacts": protected_artifacts,
        "verified": False,
        "warnings": [
            {
                "code": "approved-unverified",
                "message": "Pack is human-approved but eval replay has not verified it.",
            }
        ],
    }
    approval_path.write_text(json.dumps(approval_metadata, indent=2) + "\n", encoding="utf-8")
    _update_repo_approval_state(root, entry.id, "approved-unverified", pack_ref)
    trace_path = _append_approval_event(
        root,
        repo_id=entry.id,
        pack_ref=pack_ref,
        timestamp=timestamp,
        decision="approved",
        excluded_artifacts=excluded_artifacts,
        rationale=approval_metadata["rationale"],
    )

    return ApprovalResult(
        repo_id=entry.id,
        approval_path=approval_path,
        approved_artifacts=tuple(approved_artifacts),
        excluded_artifacts=tuple(excluded_artifacts),
        trace_path=trace_path,
    )


def render_approval_review(root: Path, repo_id: str) -> str:
    """Render a read-only review view for a draft pack."""

    root = root.resolve()
    entry = _find_draft_repo(root, repo_id)
    artifact_root = root / "repos" / entry.id
    artifacts = _artifact_inventory(root, artifact_root)
    if not artifacts:
        raise ApprovalError(f"repo {entry.id} has no generated draft artifacts to review")

    unknowns = _open_unknowns(artifact_root / "unknowns.yml")
    commands = _requested_commands(artifact_root, entry.id)
    risks = _risk_notes(artifact_root, unknowns, commands)
    diff = _prior_diff(root, artifact_root, artifacts)

    lines = [
        f"Approval Review: {entry.id}",
        "",
        "Generated Artifacts",
        *[f"- {artifact}" for artifact in artifacts],
        "",
        "Command Permissions Requested",
        *[f"- {command}" for command in commands],
        "",
        "Risk Notes",
        *[f"- {risk}" for risk in risks],
        "",
        "Unresolved Unknowns",
        *[f"- {unknown}" for unknown in unknowns],
        "",
        "Prior Approved Diff",
        *[f"- {item}" for item in diff],
        "",
        "Next Commands",
        f"- harness approve {entry.id} --all",
        f"- harness approve {entry.id} --exclude <artifact>",
    ]
    return "\n".join(lines) + "\n"


def reject_repo(root: Path, repo_id: str, *, rationale: str | None = None) -> RejectionResult:
    """Reject a generated draft pack while preserving its artifacts."""

    root = root.resolve()
    entry = _find_draft_repo(root, repo_id)
    artifact_root = root / "repos" / entry.id
    artifacts = _artifact_inventory(root, artifact_root)
    if not artifacts:
        raise ApprovalError(f"repo {entry.id} has no generated draft artifacts to reject")

    timestamp = _timestamp()
    rejection_path = artifact_root / APPROVAL_FILE
    pack_ref = rejection_path.relative_to(root).as_posix()
    rejection_rationale = rationale.strip() if rationale is not None else "Rejected by user"
    if not rejection_rationale:
        raise ApprovalError("rejection rationale cannot be empty")
    rejection_metadata = {
        "schema_version": 1,
        "repo_id": entry.id,
        "status": "rejected",
        "decision": "rejected",
        "pack_ref": pack_ref,
        "actor": "user",
        "timestamp": timestamp,
        "rationale": rejection_rationale,
        "approved_artifacts": [],
        "excluded_artifacts": artifacts,
        "protected_artifacts": [],
        "verified": False,
    }
    rejection_path.write_text(json.dumps(rejection_metadata, indent=2) + "\n", encoding="utf-8")
    _update_repo_approval_state(root, entry.id, "needs-investigation", pack_ref)
    trace_path = _append_approval_event(
        root,
        repo_id=entry.id,
        pack_ref=pack_ref,
        timestamp=timestamp,
        decision="rejected",
        excluded_artifacts=artifacts,
        rationale=rejection_rationale,
    )
    return RejectionResult(repo_id=entry.id, rejection_path=rejection_path, trace_path=trace_path)


def _find_draft_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ApprovalError("repo id cannot be empty")

    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if not entry.active:
            raise ApprovalError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.coverage_status != "draft":
            raise ApprovalError(f"repo {normalized_repo_id} is not in draft status")
        artifact_root = root / "repos" / entry.id
        if not artifact_root.is_dir():
            raise ApprovalError(f"repo {normalized_repo_id} has no draft pack at {artifact_root.relative_to(root)}")
        return entry

    raise ApprovalError(f"repo id is not registered: {normalized_repo_id}")


def _artifact_inventory(root: Path, artifact_root: Path) -> list[str]:
    ignored = {APPROVAL_FILE}
    artifacts: list[str] = []
    for path in sorted(artifact_root.rglob("*")):
        if not path.is_file() or path.name in ignored:
            continue
        artifacts.append(path.relative_to(root).as_posix())
    return artifacts


def _resolve_exclusions(
    root: Path,
    artifact_root: Path,
    artifacts: list[str],
    exclusions: tuple[str, ...],
) -> list[str]:
    resolved: set[str] = set()
    artifact_set = set(artifacts)
    for raw_exclusion in exclusions:
        exclusion = raw_exclusion.strip()
        if not exclusion:
            raise ApprovalError("approval exclusion cannot be empty")
        root_relative = _root_relative_exclusion(root, artifact_root, exclusion)
        matches = [
            artifact
            for artifact in artifacts
            if artifact == root_relative or artifact.startswith(f"{root_relative}/")
        ]
        if not matches and root_relative in artifact_set:
            matches = [root_relative]
        if not matches:
            raise ApprovalError(f"approval exclusion does not match a generated artifact: {exclusion}")
        resolved.update(matches)
    return sorted(resolved)


def _root_relative_exclusion(root: Path, artifact_root: Path, exclusion: str) -> str:
    normalized = Path(exclusion).as_posix().strip("/")
    artifact_prefix = artifact_root.relative_to(root).as_posix()
    if normalized == artifact_prefix or normalized.startswith(f"{artifact_prefix}/"):
        return normalized
    return (artifact_root / normalized).relative_to(root).as_posix()


def _default_approval_rationale(excluded_artifacts: list[str]) -> str:
    if excluded_artifacts:
        return "Approved draft pack with excluded artifacts"
    return "Approved full draft pack"


def _open_unknowns(path: Path) -> list[str]:
    if not path.is_file():
        return ["unknowns.yml is missing"]
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["unknowns.yml is malformed"]
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        return ["unknowns.yml does not contain an unknowns list"]
    rendered = []
    for unknown in unknowns:
        if not isinstance(unknown, dict) or unknown.get("status") != "open":
            continue
        unknown_id = unknown.get("id", "unknown")
        question = unknown.get("question", "unresolved question")
        severity = unknown.get("severity", "unknown severity")
        rendered.append(f"{unknown_id} [{severity}]: {question}")
    return rendered or ["None"]


def _requested_commands(artifact_root: Path, repo_id: str) -> list[str]:
    commands = [f"harness validate {repo_id}"]
    manifest_path = artifact_root / "scripts" / "manifest.yml"
    if not manifest_path.is_file():
        return commands
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        commands.append("scripts/manifest.yml is malformed")
        return commands
    scripts = manifest.get("scripts")
    if not isinstance(scripts, list):
        commands.append("scripts/manifest.yml does not contain a scripts list")
        return commands
    for script in scripts:
        if isinstance(script, dict) and isinstance(script.get("path"), str):
            commands.append(f"python {artifact_root.name}/{script['path']}")
    return commands


def _risk_notes(artifact_root: Path, unknowns: list[str], commands: list[str]) -> list[str]:
    risks = [
        "Pack is generated and not verified by eval replay.",
        "Approval will mark accepted artifacts as protected source-of-truth files.",
    ]
    if unknowns != ["None"]:
        risks.append("Open unknowns remain and should be reviewed before approval.")
    if any(command for command in commands if command != "harness validate"):
        risks.append("Generated scripts and validation commands request local execution permission.")
    report_path = artifact_root / "pack-report.md"
    if report_path.is_file() and "not verified" in report_path.read_text(encoding="utf-8"):
        risks.append("Pack report states the generated pack is not verified.")
    return risks


def _prior_diff(root: Path, artifact_root: Path, current_artifacts: list[str]) -> list[str]:
    approval_path = artifact_root / APPROVAL_FILE
    if not approval_path.is_file():
        return ["No prior approved pack found."]
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["Prior approval metadata is malformed."]

    protected = approval.get("protected_artifacts")
    if not isinstance(protected, list):
        return ["Prior approval metadata has no protected artifact list."]
    prior_hashes = {
        item["path"]: item.get("sha256")
        for item in protected
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    current = set(current_artifacts)
    prior = set(prior_hashes)
    added = sorted(current - prior)
    removed = sorted(prior - current)
    changed = sorted(
        path
        for path in current & prior
        if (root / path).is_file() and _sha256(root / path) != prior_hashes[path]
    )
    unchanged = len((current & prior) - set(changed))
    return [
        f"Added: {len(added)}",
        f"Removed: {len(removed)}",
        f"Changed: {len(changed)}",
        f"Unchanged: {unchanged}",
    ]


def _update_repo_approval_state(root: Path, repo_id: str, coverage_status: str, pack_ref: str) -> None:
    entries = load_repo_entries(root / "harness.yml")
    updated: list[RepoEntry] = []
    for entry in entries:
        if entry.id == repo_id:
            updated.append(
                RepoEntry(
                    id=entry.id,
                    name=entry.name,
                    owner=entry.owner,
                    purpose=entry.purpose,
                    url=entry.url,
                    default_branch=entry.default_branch,
                    local_path=entry.local_path,
                    coverage_status=coverage_status,
                    active=entry.active,
                    deactivation_reason=entry.deactivation_reason,
                    pack_ref=pack_ref,
                    external=entry.external,
                )
            )
        else:
            updated.append(entry)
    save_repo_entries(root / "harness.yml", tuple(updated))


def _append_approval_event(
    root: Path,
    *,
    repo_id: str,
    pack_ref: str,
    timestamp: str,
    decision: str,
    excluded_artifacts: list[str],
    rationale: str,
) -> Path:
    trace_root = root / "trace-summaries"
    trace_root.mkdir(parents=True, exist_ok=True)
    trace_path = trace_root / APPROVAL_TRACE_FILE
    event = {
        "schema_version": 1,
        "event_id": f"evt_approval_{repo_id}_{timestamp.replace(':', '').replace('-', '')}",
        "event_type": "approval",
        "timestamp": timestamp,
        "repo_id": repo_id,
        "pack_ref": pack_ref,
        "actor": "user",
        "adapter": None,
        "payload": {
            "decision": decision,
            "excluded_artifacts": excluded_artifacts,
            "rationale": rationale,
        },
    }
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return trace_path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
