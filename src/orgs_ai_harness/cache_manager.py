"""Repo-local pinned cache management for approved skill packs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class CacheManagerError(Exception):
    """Raised when cache refresh cannot be completed."""


@dataclass(frozen=True)
class CacheRefreshResult:
    repo_id: str
    repo_path: Path
    cache_root: Path
    pointer_path: Path
    pack_ref: str
    source_pack_ref: str
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
            raise CacheManagerError(f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}")
        if entry.coverage_status not in {"approved-unverified", "verified"}:
            raise CacheManagerError(
                f"repo {entry.id} must be approved-unverified or verified before cache refresh: "
                f"status={entry.coverage_status}"
            )
        if entry.local_path is None:
            raise CacheManagerError(f"repo {entry.id} has no local path; run 'harness repo set-path'")
        return entry
    raise CacheManagerError(f"repo id is not registered: {normalized_repo_id}")


def _resolve_repo_path(root: Path, entry: RepoEntry) -> Path:
    assert entry.local_path is not None
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
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
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
