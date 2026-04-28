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
    trace_path: Path


APPROVAL_FILE = "approval.yml"
APPROVAL_TRACE_FILE = "approval-events.jsonl"


def approve_repo_all(root: Path, repo_id: str, *, rationale: str | None = None) -> ApprovalResult:
    """Approve every generated artifact in a draft pack."""

    root = root.resolve()
    entry = _find_draft_repo(root, repo_id)
    artifact_root = root / "repos" / entry.id
    artifacts = _artifact_inventory(root, artifact_root)
    if not artifacts:
        raise ApprovalError(f"repo {entry.id} has no generated draft artifacts to approve")

    timestamp = _timestamp()
    approval_path = artifact_root / APPROVAL_FILE
    pack_ref = approval_path.relative_to(root).as_posix()
    protected_artifacts = [
        {
            "path": artifact,
            "sha256": _sha256(root / artifact),
            "protected": True,
        }
        for artifact in artifacts
    ]
    approval_metadata = {
        "schema_version": 1,
        "repo_id": entry.id,
        "status": "approved-unverified",
        "decision": "approved",
        "pack_ref": pack_ref,
        "actor": "user",
        "timestamp": timestamp,
        "rationale": rationale or "Approved full draft pack",
        "approved_artifacts": artifacts,
        "excluded_artifacts": [],
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
        excluded_artifacts=[],
        rationale=approval_metadata["rationale"],
    )

    return ApprovalResult(
        repo_id=entry.id,
        approval_path=approval_path,
        approved_artifacts=tuple(artifacts),
        trace_path=trace_path,
    )


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
