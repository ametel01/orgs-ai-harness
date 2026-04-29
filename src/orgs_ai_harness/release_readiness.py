"""Artifact-only release readiness command contract."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ReleaseReadinessError(Exception):
    """Raised when release readiness input cannot be resolved."""


@dataclass(frozen=True)
class ReleaseReadinessInput:
    repo_id: str
    repo_path: Path
    status: str
    version: str | None = None
    base: str | None = None
    head: str | None = None


def collect_release_readiness_input(
    root: Path,
    repo_id: str,
    *,
    version: str | None = None,
    base: str | None = None,
    head: str | None = None,
) -> ReleaseReadinessInput:
    """Resolve release readiness inputs for one registered local repo."""

    root = root.resolve()
    entry = _find_release_repo(root, repo_id)
    repo_path = _resolve_repo_path(root, entry)
    normalized_version = _normalize_optional("version", version)
    normalized_base = _normalize_optional("base", base)
    normalized_head = _normalize_optional("head", head)

    if (normalized_base is None) != (normalized_head is None):
        raise ReleaseReadinessError("release readiness requires both --base and --head when either is provided")
    if normalized_base is not None and normalized_head is not None:
        _ensure_git_refs(repo_path, normalized_base, normalized_head)

    return ReleaseReadinessInput(
        repo_id=entry.id,
        repo_path=repo_path,
        status="artifact-only",
        version=normalized_version,
        base=normalized_base,
        head=normalized_head,
    )


def _find_release_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ReleaseReadinessError("repo id cannot be empty")

    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if entry.external or entry.coverage_status == "external":
            raise ReleaseReadinessError(
                f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}"
            )
        if not entry.active:
            raise ReleaseReadinessError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.local_path is None:
            raise ReleaseReadinessError(
                f"repo {normalized_repo_id} has no local path; run 'harness repo discover --clone' "
                "or 'harness repo set-path'"
            )
        return entry

    raise ReleaseReadinessError(f"repo id is not registered: {normalized_repo_id}")


def _resolve_repo_path(root: Path, entry: RepoEntry) -> Path:
    if entry.local_path is None:
        raise ReleaseReadinessError(f"repo {entry.id} has no local path")
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.is_dir():
        raise ReleaseReadinessError(f"repo path does not exist: {repo_path}")
    return repo_path


def _normalize_optional(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ReleaseReadinessError(f"{label} cannot be empty")
    return normalized


def _ensure_git_refs(repo_path: Path, base: str, head: str) -> None:
    git = shutil.which("git")
    if git is None:
        raise ReleaseReadinessError("git executable not found")

    for label, ref in (("base", base), ("head", head)):
        result = subprocess.run(  # nosec B603
            [git, "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or f"git rev-parse exited with code {result.returncode}"
            )
            raise ReleaseReadinessError(f"cannot resolve {label} ref {ref!r}: {detail}")
