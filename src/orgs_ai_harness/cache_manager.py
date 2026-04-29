"""Repo-local pinned cache management for approved skill packs."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class CacheManagerError(Exception):
    """Raised when cache refresh cannot be completed."""


SUPPORTED_EXPORT_TARGETS = {"generic", "codex"}


@dataclass(frozen=True)
class CacheRefreshResult:
    repo_id: str
    repo_path: Path
    cache_root: Path
    pointer_path: Path
    pack_ref: str
    source_pack_ref: str
    status: str


@dataclass(frozen=True)
class ExportResult:
    repo_id: str
    target: str
    export_root: Path
    status: str


def refresh_cache(root: Path, repo_id: str) -> CacheRefreshResult:
    """Refresh a repo-local read-only cache from an approved or verified pack."""

    root = root.resolve()
    entry = _find_cacheable_repo(root, repo_id)
    repo_path = _resolve_repo_path(root, entry)
    artifact_root = root / "repos" / entry.id
    approval = _load_approval(artifact_root / "approval.yml")
    status = _pack_status(entry, approval)
    source_pack_ref = _source_pack_ref(entry, approval)
    pack_ref = _pack_commit_ref(root, artifact_root, approval)
    cache_root = repo_path / ".agent-harness" / "cache"
    applied_proposals = _applied_proposal_ids(root, entry.id)

    if cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.mkdir(parents=True)
    (cache_root / "exports").mkdir()
    (cache_root / "pack-ref").write_text(f"{pack_ref}\n", encoding="utf-8")
    (cache_root / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo_id": entry.id,
                "status": status,
                "pack_ref": pack_ref,
                "source_pack_ref": source_pack_ref,
                "org_skill_pack": str(root),
                "applied_proposals": applied_proposals,
                "warnings": _warnings(approval, status),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    _copy_org_context(root, cache_root)
    _copy_repo_pack(root, artifact_root, cache_root / "repos" / entry.id, approval)
    pointer_path = repo_path / ".agent-harness.yml"
    pointer_path.write_text(
        "\n".join(
            [
                f"org_skill_pack: {root}",
                f"repo_id: {entry.id}",
                f"pack_ref: {pack_ref}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _make_read_only(cache_root)

    return CacheRefreshResult(
        repo_id=entry.id,
        repo_path=repo_path,
        cache_root=cache_root,
        pointer_path=pointer_path,
        pack_ref=pack_ref,
        source_pack_ref=source_pack_ref,
        status=status,
    )


def export_cached_pack(
    root: Path,
    target: str,
    repo_id: str,
    *,
    allow_draft: bool = False,
    development: bool = False,
) -> ExportResult:
    """Export a cached pack to a managed runtime target directory."""

    root = root.resolve()
    normalized_target = target.strip()
    if normalized_target not in SUPPORTED_EXPORT_TARGETS:
        raise CacheManagerError(f"unsupported export target: {target}")
    entry = _find_local_repo(root, repo_id)
    repo_path = _resolve_repo_path(root, entry)
    cache_root = repo_path / ".agent-harness" / "cache"
    metadata = _load_cache_metadata(cache_root)
    _ensure_cache_includes_applied_proposals(root, entry.id, metadata)
    status = _metadata_status(metadata)
    _enforce_export_policy(entry.id, status, allow_draft=allow_draft, development=development)

    source_repo_root = cache_root / "repos" / entry.id
    skills_root = source_repo_root / "skills"
    if not skills_root.is_dir():
        raise CacheManagerError(f"cached pack has no skills directory: {skills_root}")

    export_root = cache_root / "exports" / normalized_target
    if export_root.exists():
        shutil.rmtree(export_root)
    export_root.mkdir(parents=True)
    shutil.copytree(skills_root, export_root / "skills")
    resolvers_path = source_repo_root / "resolvers.yml"
    if resolvers_path.is_file():
        shutil.copy2(resolvers_path, export_root / "resolvers.yml")
    _write_export_metadata(export_root, normalized_target, metadata, status)
    _make_read_only(export_root)

    return ExportResult(repo_id=entry.id, target=normalized_target, export_root=export_root, status=status)


def _find_local_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise CacheManagerError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if entry.local_path is None:
            raise CacheManagerError(f"repo {entry.id} has no local path; run 'harness repo set-path'")
        return entry
    raise CacheManagerError(f"repo id is not registered: {normalized_repo_id}")


def _find_cacheable_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise CacheManagerError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if not entry.active:
            raise CacheManagerError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.external or entry.coverage_status == "external":
            raise CacheManagerError(
                f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}"
            )
        if entry.coverage_status not in {"approved-unverified", "verified"}:
            raise CacheManagerError(
                f"repo {entry.id} must be approved-unverified or verified before cache refresh: "
                f"status={entry.coverage_status}"
            )
        if entry.local_path is None:
            raise CacheManagerError(f"repo {entry.id} has no local path; run 'harness repo set-path'")
        return entry
    raise CacheManagerError(f"repo id is not registered: {normalized_repo_id}")


def _load_cache_metadata(cache_root: Path) -> dict[str, object]:
    metadata_path = cache_root / "metadata.json"
    if not metadata_path.is_file():
        raise CacheManagerError(f"repo cache is missing; run 'harness cache refresh <repo-id>' first: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CacheManagerError(f"cache metadata is malformed: {metadata_path} ({exc.msg})") from exc
    if not isinstance(metadata, dict):
        raise CacheManagerError(f"cache metadata must be an object: {metadata_path}")
    return metadata


def _metadata_status(metadata: dict[str, object]) -> str:
    status = metadata.get("status")
    if not isinstance(status, str) or not status.strip():
        raise CacheManagerError("cache metadata missing status")
    return status


def _enforce_export_policy(
    repo_id: str,
    status: str,
    *,
    allow_draft: bool,
    development: bool,
) -> None:
    if status in {"approved-unverified", "verified"}:
        return
    if status == "draft" and allow_draft:
        return
    if status == "needs-investigation" and development:
        return
    if status == "draft":
        raise CacheManagerError(f"repo {repo_id} is draft; pass --allow-draft to export intentionally")
    if status == "needs-investigation":
        raise CacheManagerError(f"repo {repo_id} needs investigation; pass --development to force a development export")
    raise CacheManagerError(f"repo {repo_id} cannot be exported with status={status}")


def _write_export_metadata(
    export_root: Path,
    target: str,
    cache_metadata: dict[str, object],
    status: str,
) -> None:
    warnings = cache_metadata.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    if status == "approved-unverified" and not any(
        isinstance(warning, dict) and warning.get("code") == "approved-unverified" for warning in warnings
    ):
        warnings = [
            *warnings,
            {
                "code": "approved-unverified",
                "message": "Pack is human-approved but eval replay has not verified it.",
            },
        ]
    metadata = {
        "schema_version": 1,
        "target": target,
        "repo_id": cache_metadata.get("repo_id"),
        "status": status,
        "pack_ref": cache_metadata.get("pack_ref"),
        "source_pack_ref": cache_metadata.get("source_pack_ref"),
        "warnings": warnings,
    }
    (export_root / "pack-status.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _resolve_repo_path(root: Path, entry: RepoEntry) -> Path:
    if entry.local_path is None:
        raise CacheManagerError(f"repo {entry.id} has no local path; refresh the registry before cache operations")
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.exists():
        raise CacheManagerError(f"repo path does not exist: {repo_path}; repair it with 'harness repo set-path'")
    if not repo_path.is_dir():
        raise CacheManagerError(f"repo path is not a directory: {repo_path}; repair it with 'harness repo set-path'")
    return repo_path


def _load_approval(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise CacheManagerError(f"missing approval metadata: {path}")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CacheManagerError(f"approval metadata is malformed: {path} ({exc.msg})") from exc
    if not isinstance(artifact, dict):
        raise CacheManagerError(f"approval metadata must be an object: {path}")
    if artifact.get("decision") != "approved":
        raise CacheManagerError("cache refresh requires an approved pack")
    return artifact


def _pack_status(entry: RepoEntry, approval: dict[str, object]) -> str:
    status = approval.get("status")
    if isinstance(status, str) and status.strip():
        return status
    return entry.coverage_status


def _source_pack_ref(entry: RepoEntry, approval: dict[str, object]) -> str:
    if entry.pack_ref is not None:
        return entry.pack_ref
    value = approval.get("pack_ref")
    if isinstance(value, str) and value.strip():
        return value
    raise CacheManagerError(f"repo {entry.id} has no approved pack ref")


def _pack_commit_ref(root: Path, artifact_root: Path, approval: dict[str, object]) -> str:
    # Bandit: fixed git argv with shell=False.
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        # Bandit: fixed git argv with shell=False.
        status_result = subprocess.run(  # nosec B603 B607
            ["git", "status", "--porcelain", "--", str(artifact_root.relative_to(root))],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if status_result.returncode == 0 and not status_result.stdout.strip():
            return result.stdout.strip()

    hasher = hashlib.sha256()
    for relative in _approved_artifacts(approval):
        path = root / relative
        if path.is_file():
            hasher.update(relative.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
            hasher.update(b"\0")
    if hasher.digest() != hashlib.sha256().digest():
        return hasher.hexdigest()
    return hashlib.sha256(str(artifact_root).encode("utf-8")).hexdigest()


def _ensure_cache_includes_applied_proposals(root: Path, repo_id: str, metadata: dict[str, object]) -> None:
    current = set(_applied_proposal_ids(root, repo_id))
    cached_value = metadata.get("applied_proposals")
    cached = set(item for item in cached_value if isinstance(item, str)) if isinstance(cached_value, list) else set()
    missing = sorted(current - cached)
    if missing:
        raise CacheManagerError(
            f"repo {repo_id} cache is stale; run 'harness cache refresh {repo_id}' "
            f"to include applied proposal(s): {', '.join(missing)}"
        )


def _applied_proposal_ids(root: Path, repo_id: str) -> list[str]:
    proposals_root = root / "proposals"
    if not proposals_root.is_dir():
        return []
    proposal_ids: list[str] = []
    for proposal_root in sorted(path for path in proposals_root.iterdir() if path.is_dir()):
        metadata_path = proposal_root / "metadata.yml"
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(metadata, dict):
            continue
        if metadata.get("repo_id") == repo_id and metadata.get("status") == "applied":
            proposal_id = metadata.get("id")
            proposal_ids.append(proposal_id if isinstance(proposal_id, str) else proposal_root.name)
    return proposal_ids


def _copy_org_context(root: Path, cache_root: Path) -> None:
    org_source = root / "org"
    org_target = cache_root / "org"
    if org_source.is_dir():
        shutil.copytree(org_source, org_target)
    else:
        org_target.mkdir()


def _copy_repo_pack(root: Path, artifact_root: Path, target_root: Path, approval: dict[str, object]) -> None:
    target_root.mkdir(parents=True)
    for relative in _approved_artifacts(approval):
        source = root / relative
        if not source.is_file():
            raise CacheManagerError(f"approved artifact is missing: {relative}")
        destination = target_root / source.relative_to(artifact_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    approval_path = artifact_root / "approval.yml"
    if approval_path.is_file():
        shutil.copy2(approval_path, target_root / "approval.yml")


def _approved_artifacts(approval: dict[str, object]) -> tuple[str, ...]:
    approved = approval.get("approved_artifacts")
    if not isinstance(approved, list) or not approved:
        raise CacheManagerError("approval metadata has no approved artifacts")
    artifacts = tuple(item for item in approved if isinstance(item, str) and item.strip())
    if len(artifacts) != len(approved):
        raise CacheManagerError("approval metadata approved_artifacts must contain only strings")
    return artifacts


def _warnings(approval: dict[str, object], status: str) -> list[object]:
    warnings = approval.get("warnings")
    if isinstance(warnings, list):
        return warnings
    if status == "approved-unverified":
        return [
            {
                "code": "approved-unverified",
                "message": "Pack is human-approved but eval replay has not verified it.",
            }
        ]
    return []


def _make_read_only(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_file():
            child.chmod(0o444)
