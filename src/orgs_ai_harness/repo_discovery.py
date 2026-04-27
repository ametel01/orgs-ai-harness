"""Repository discovery providers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from orgs_ai_harness.repo_registry import (
    RepoEntry,
    RepoRegistryError,
    add_repo_entries,
    derive_repo_id_from_url,
)


class RepoDiscoveryError(Exception):
    """Raised when repository discovery cannot be completed."""


@dataclass(frozen=True)
class DiscoveredRepo:
    id: str
    name: str
    owner: str | None
    url: str
    default_branch: str | None
    visibility: str | None
    archived: bool
    fork: bool
    description: str | None


def discover_github_org(org: str) -> tuple[DiscoveredRepo, ...]:
    """Discover repositories visible to `gh` for a GitHub organization."""

    target = org.strip()
    if not target:
        raise RepoDiscoveryError("GitHub org cannot be empty")
    return _run_gh_repo_list(target)


def discover_github_user(user: str) -> tuple[DiscoveredRepo, ...]:
    """Discover repositories visible to `gh` for a GitHub user profile."""

    target = user.strip()
    if not target:
        raise RepoDiscoveryError("GitHub user cannot be empty")
    return _run_gh_repo_list(target)


def select_discovered_repos(
    discovered: tuple[DiscoveredRepo, ...],
    selection_value: str,
) -> tuple[DiscoveredRepo, ...]:
    """Select discovered repos by comma-separated id or name."""

    requested = tuple(part.strip() for part in selection_value.split(",") if part.strip())
    if not requested:
        raise RepoDiscoveryError("--select must include at least one repo id or name")

    by_key: dict[str, DiscoveredRepo] = {}
    for repo in discovered:
        by_key[repo.id] = repo
        by_key[repo.name] = repo

    selected: list[DiscoveredRepo] = []
    missing: list[str] = []
    seen: set[str] = set()
    for key in requested:
        repo = by_key.get(key)
        if repo is None:
            missing.append(key)
            continue
        if repo.id not in seen:
            selected.append(repo)
            seen.add(repo.id)

    if missing:
        missing_list = ", ".join(missing)
        raise RepoDiscoveryError(f"selected repo(s) not found in discovery results: {missing_list}")

    return tuple(selected)


def register_discovered_repos(root: Path, selected: tuple[DiscoveredRepo, ...]) -> tuple[RepoEntry, ...]:
    """Write selected discovered repos to the existing repo registry."""

    entries = tuple(_repo_entry_from_discovered(repo) for repo in selected)
    try:
        return add_repo_entries(root, entries)
    except RepoRegistryError as exc:
        raise RepoDiscoveryError(str(exc)) from exc


def _run_gh_repo_list(target: str) -> tuple[DiscoveredRepo, ...]:
    if shutil.which("gh") is None:
        raise RepoDiscoveryError("GitHub CLI 'gh' is required. Install it and run 'gh auth login'.")

    command = [
        "gh",
        "repo",
        "list",
        target,
        "--limit",
        "1000",
        "--json",
        "name,owner,url,defaultBranchRef,visibility,isArchived,isFork,description",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise RepoDiscoveryError(f"failed to run gh: {exc}") from exc

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown gh failure"
        raise RepoDiscoveryError(f"gh repo discovery failed: {message}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RepoDiscoveryError("gh repo discovery returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RepoDiscoveryError("gh repo discovery returned unexpected JSON")

    return tuple(_discovered_repo_from_gh(record) for record in payload)


def _discovered_repo_from_gh(record: object) -> DiscoveredRepo:
    if not isinstance(record, dict):
        raise RepoDiscoveryError("gh repo discovery returned a non-object repo record")

    name = _required_string(record, "name")
    url = _required_string(record, "url")
    return DiscoveredRepo(
        id=derive_repo_id_from_url(url),
        name=name,
        owner=_owner_login(record.get("owner")),
        url=url,
        default_branch=_default_branch_name(record.get("defaultBranchRef")),
        visibility=_optional_string(record, "visibility"),
        archived=_bool_field(record, "isArchived"),
        fork=_bool_field(record, "isFork"),
        description=_optional_string(record, "description"),
    )


def _repo_entry_from_discovered(repo: DiscoveredRepo) -> RepoEntry:
    return RepoEntry(
        id=repo.id,
        name=repo.name,
        owner=repo.owner,
        purpose=None,
        url=repo.url,
        default_branch=repo.default_branch,
        local_path=None,
        coverage_status="selected",
        active=True,
        deactivation_reason=None,
        pack_ref=None,
        external=False,
    )


def _required_string(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RepoDiscoveryError(f"gh repo record missing required string field: {field}")
    return value


def _optional_string(record: dict[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepoDiscoveryError(f"gh repo record field {field} must be a string or null")
    normalized = value.strip()
    return normalized or None


def _bool_field(record: dict[str, object], field: str) -> bool:
    value = record.get(field)
    if not isinstance(value, bool):
        raise RepoDiscoveryError(f"gh repo record field {field} must be a boolean")
    return value


def _owner_login(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RepoDiscoveryError("gh repo record field owner must be an object or null")
    login = value.get("login")
    if login is None:
        return None
    if not isinstance(login, str):
        raise RepoDiscoveryError("gh repo record field owner.login must be a string")
    normalized = login.strip()
    return normalized or None


def _default_branch_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RepoDiscoveryError("gh repo record field defaultBranchRef must be an object or null")
    name = value.get("name")
    if name is None:
        return None
    if not isinstance(name, str):
        raise RepoDiscoveryError("gh repo record field defaultBranchRef.name must be a string")
    normalized = name.strip()
    return normalized or None
