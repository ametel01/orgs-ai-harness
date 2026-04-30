"""Artifact-only dependency campaign command contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class DependencyCampaignError(Exception):
    """Raised when dependency campaign input cannot be resolved."""


@dataclass(frozen=True)
class DependencyCampaignRepo:
    repo_id: str
    repo_name: str
    repo_path: Path
    coverage_status: str


@dataclass(frozen=True)
class SkippedDependencyCampaignRepo:
    repo_id: str
    reason: str


@dataclass(frozen=True)
class DependencyCampaignInput:
    name: str
    package_filters: tuple[str, ...]
    repos: tuple[DependencyCampaignRepo, ...]
    skipped_repos: tuple[SkippedDependencyCampaignRepo, ...]
    status: str = "artifact-only"


def collect_dependency_campaign_input(
    root: Path,
    *,
    name: str,
    package_filters: tuple[str, ...] = (),
) -> DependencyCampaignInput:
    """Resolve read-only dependency campaign inputs across eligible local repos."""

    root = root.resolve()
    campaign_name = _normalize_name(name)
    normalized_filters = _normalize_package_filters(package_filters)
    entries = load_repo_entries(root / "harness.yml")
    if not entries:
        raise DependencyCampaignError("dependency campaign requires at least one registered repository")

    repos: list[DependencyCampaignRepo] = []
    skipped: list[SkippedDependencyCampaignRepo] = []
    for entry in sorted(entries, key=lambda item: item.id):
        reason = _skip_reason(root, entry)
        if reason is not None:
            skipped.append(SkippedDependencyCampaignRepo(entry.id, reason))
            continue
        if entry.local_path is None:
            skipped.append(SkippedDependencyCampaignRepo(entry.id, "repo has no local path"))
            continue
        repos.append(
            DependencyCampaignRepo(
                repo_id=entry.id,
                repo_name=entry.name,
                repo_path=(root / entry.local_path).resolve(),
                coverage_status=entry.coverage_status,
            )
        )

    if not repos:
        raise DependencyCampaignError("dependency campaign has no eligible active local repositories")

    return DependencyCampaignInput(
        name=campaign_name,
        package_filters=normalized_filters,
        repos=tuple(repos),
        skipped_repos=tuple(skipped),
    )


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise DependencyCampaignError("dependency campaign name cannot be empty")
    return normalized


def _normalize_package_filters(package_filters: tuple[str, ...]) -> tuple[str, ...]:
    if any(not item.strip() for item in package_filters):
        raise DependencyCampaignError("dependency campaign package filters cannot be empty")
    normalized = sorted({item.strip() for item in package_filters})
    return tuple(normalized)


def _skip_reason(root: Path, entry: RepoEntry) -> str | None:
    if entry.external or entry.coverage_status == "external":
        return "repo is an external dependency reference"
    if not entry.active:
        return "repo is not active selected coverage"
    if entry.local_path is None:
        return "repo has no local path"
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.exists():
        return "repo path does not exist"
    if not repo_path.is_dir():
        return "repo path is not a directory"
    return None
