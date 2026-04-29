"""Artifact-only pull request review input handling."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ReviewError(Exception):
    """Raised when PR review input cannot be resolved."""


@dataclass(frozen=True)
class ReviewChangedFiles:
    repo_id: str
    repo_path: Path
    changed_files: tuple[str, ...]
    source: str
    base: str | None = None
    head: str | None = None


def collect_changed_files(
    root: Path,
    repo_id: str,
    *,
    files: tuple[str, ...] = (),
    files_from: Path | None = None,
    base: str | None = None,
    head: str | None = None,
) -> ReviewChangedFiles:
    """Resolve an explicit or git-derived changed-file set for one repo."""

    root = root.resolve()
    entry = _find_review_repo(root, repo_id)
    repo_path = _resolve_repo_path(root, entry)

    input_modes = sum(
        (
            bool(files),
            files_from is not None,
            base is not None or head is not None,
        )
    )
    if input_modes != 1:
        raise ReviewError(
            "review changed-files requires exactly one input mode: --files, --files-from, or --base/--head"
        )

    if files:
        changed_files = _normalize_changed_files(files)
        source = "explicit"
    elif files_from is not None:
        changed_files = _normalize_changed_files(_read_files_from(files_from))
        source = f"files-from:{files_from}"
    else:
        if not base or not head:
            raise ReviewError("review changed-files requires both --base and --head when using git diff input")
        changed_files = _changed_files_from_git(repo_path, base, head)
        source = "git-diff"

    if not changed_files:
        raise ReviewError("review changed-files requires at least one changed file")

    return ReviewChangedFiles(
        repo_id=entry.id,
        repo_path=repo_path,
        changed_files=changed_files,
        source=source,
        base=base,
        head=head,
    )


def _find_review_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ReviewError("repo id cannot be empty")

    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if entry.external or entry.coverage_status == "external":
            raise ReviewError(f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}")
        if not entry.active:
            raise ReviewError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.local_path is None:
            raise ReviewError(
                f"repo {normalized_repo_id} has no local path; run 'harness repo discover --clone' "
                "or 'harness repo set-path'"
            )
        return entry

    raise ReviewError(f"repo id is not registered: {normalized_repo_id}")


def _resolve_repo_path(root: Path, entry: RepoEntry) -> Path:
    if entry.local_path is None:
        raise ReviewError(f"repo {entry.id} has no local path")
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.is_dir():
        raise ReviewError(f"repo path does not exist: {repo_path}")
    return repo_path


def _read_files_from(files_from: Path) -> tuple[str, ...]:
    path = files_from.expanduser().resolve()
    if not path.is_file():
        raise ReviewError(f"changed-file input does not exist: {path}")
    return tuple(path.read_text(encoding="utf-8").splitlines())


def _normalize_changed_files(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        raw_value = raw_path.strip()
        if not raw_value:
            continue
        path = Path(raw_value)
        if path.is_absolute():
            raise ReviewError(f"changed file must be repo-relative: {raw_value}")
        parts = path.parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ReviewError(f"changed file must not contain traversal segments: {raw_value}")
        if ".git" in parts:
            raise ReviewError(f"changed file must not be inside .git: {raw_value}")
        rendered = path.as_posix()
        if rendered not in seen:
            normalized.append(rendered)
            seen.add(rendered)
    return tuple(sorted(normalized))


def _changed_files_from_git(repo_path: Path, base: str, head: str) -> tuple[str, ...]:
    git = shutil.which("git")
    if git is None:
        raise ReviewError("git executable not found")

    result = subprocess.run(  # nosec B603
        [git, "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base}..{head}"],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git diff exited with code {result.returncode}"
        raise ReviewError(f"cannot resolve changed files for {base}..{head}: {detail}")
    return _normalize_changed_files(tuple(result.stdout.splitlines()))
