"""Read-only dependency campaign inventory context."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.dependency_campaign import DependencyCampaignInput, SkippedDependencyCampaignRepo
from orgs_ai_harness.repo_registry import load_repo_entries


@dataclass(frozen=True)
class DependencyEvidence:
    ecosystem: str
    manager: str
    source: str
    detail: str


@dataclass(frozen=True)
class DependencyFile:
    path: str
    ecosystem: str
    manager: str
    status: str
    package_name: str | None
    dependencies: tuple[str, ...]
    dev_dependencies: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True)
class ParsedManifest:
    package_name: str | None
    dependencies: tuple[str, ...]
    dev_dependencies: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True)
class DependencyLockfile:
    path: str
    ecosystem: str
    manager: str
    status: str


@dataclass(frozen=True)
class GeneratedPackStatus:
    coverage_status: str
    pack_ref: str | None
    approval_status: str | None
    approval_verified: bool | None
    eval_task_count: int | None
    skills_status: str
    resolvers_status: str
    scan_status: str


@dataclass(frozen=True)
class MissingDependencyEvidence:
    kind: str
    path: str
    reason: str


@dataclass(frozen=True)
class DependencyInventoryWarning:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class DependencyRepoInventory:
    repo_id: str
    repo_name: str
    repo_path: Path
    lifecycle_status: str
    dependency_files: tuple[DependencyFile, ...]
    lockfiles: tuple[DependencyLockfile, ...]
    package_manager_evidence: tuple[DependencyEvidence, ...]
    generated_pack: GeneratedPackStatus
    missing_evidence: tuple[MissingDependencyEvidence, ...]
    warnings: tuple[DependencyInventoryWarning, ...]


@dataclass(frozen=True)
class DependencyInventory:
    campaign_name: str
    package_filters: tuple[str, ...]
    repos: tuple[DependencyRepoInventory, ...]
    skipped_repos: tuple[SkippedDependencyCampaignRepo, ...]


_SKIP_DIRS = {
    ".agent-harness",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "org-agent-skills",
    "target",
    "vendor",
}

_MANIFESTS: dict[str, tuple[str, str, str]] = {
    "package.json": ("node", "npm-compatible", "package.json"),
    "pyproject.toml": ("python", "python", "pyproject.toml"),
    "requirements.txt": ("python", "pip", "requirements.txt"),
    "go.mod": ("go", "go", "go.mod"),
    "Cargo.toml": ("rust", "cargo", "Cargo.toml"),
}
_LOCKFILES: dict[str, tuple[str, str]] = {
    "bun.lock": ("node", "bun"),
    "bun.lockb": ("node", "bun"),
    "package-lock.json": ("node", "npm"),
    "pnpm-lock.yaml": ("node", "pnpm"),
    "yarn.lock": ("node", "yarn"),
    "uv.lock": ("python", "uv"),
    "poetry.lock": ("python", "poetry"),
    "Pipfile.lock": ("python", "pipenv"),
    "go.sum": ("go", "go"),
    "Cargo.lock": ("rust", "cargo"),
}
_REQUIREMENTS_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


def build_dependency_inventory(root: Path, campaign: DependencyCampaignInput) -> DependencyInventory:
    """Build deterministic dependency inventory from local files and harness artifacts."""

    root = root.resolve()
    entries = {entry.id: entry for entry in load_repo_entries(root / "harness.yml")}
    repos: list[DependencyRepoInventory] = []
    for repo in campaign.repos:
        entry = entries.get(repo.repo_id)
        artifact_root = root / "repos" / repo.repo_id
        missing: list[MissingDependencyEvidence] = []
        warnings: list[DependencyInventoryWarning] = []
        dependency_files = _collect_dependency_files(repo.repo_path, missing, warnings)
        lockfiles = _collect_lockfiles(repo.repo_path)
        _record_lockfile_gaps(dependency_files, lockfiles, missing)
        package_manager_evidence = _package_manager_evidence(dependency_files, lockfiles)
        generated_pack = _generated_pack_status(artifact_root, repo.coverage_status, entry.pack_ref if entry else None)
        _record_generated_pack_missing(generated_pack, artifact_root, root, missing)
        repos.append(
            DependencyRepoInventory(
                repo_id=repo.repo_id,
                repo_name=repo.repo_name,
                repo_path=repo.repo_path,
                lifecycle_status=repo.coverage_status,
                dependency_files=dependency_files,
                lockfiles=lockfiles,
                package_manager_evidence=package_manager_evidence,
                generated_pack=generated_pack,
                missing_evidence=tuple(sorted(missing, key=lambda item: (item.kind, item.path, item.reason))),
                warnings=tuple(sorted(warnings, key=lambda item: (item.source, item.code, item.message))),
            )
        )

    return DependencyInventory(
        campaign_name=campaign.name,
        package_filters=campaign.package_filters,
        repos=tuple(sorted(repos, key=lambda item: item.repo_id)),
        skipped_repos=campaign.skipped_repos,
    )


def _collect_dependency_files(
    repo_path: Path,
    missing: list[MissingDependencyEvidence],
    warnings: list[DependencyInventoryWarning],
) -> tuple[DependencyFile, ...]:
    files: list[DependencyFile] = []
    for path in _walk_known_files(repo_path, set(_MANIFESTS)):
        ecosystem, manager, manifest_type = _MANIFESTS[path.name]
        files.append(_parse_dependency_file(repo_path, path, ecosystem, manager, manifest_type, warnings))
    if not files:
        missing.append(
            MissingDependencyEvidence("dependency-manifest", "known manifests", "no dependency manifest found")
        )
    return tuple(sorted(files, key=lambda item: item.path))


def _collect_lockfiles(repo_path: Path) -> tuple[DependencyLockfile, ...]:
    lockfiles = []
    for path in _walk_known_files(repo_path, set(_LOCKFILES)):
        ecosystem, manager = _LOCKFILES[path.name]
        lockfiles.append(DependencyLockfile(path.relative_to(repo_path).as_posix(), ecosystem, manager, "present"))
    return tuple(sorted(lockfiles, key=lambda item: item.path))


def _walk_known_files(repo_path: Path, names: set[str]) -> tuple[Path, ...]:
    matches: list[Path] = []
    for path in repo_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo_path).parts[:-1]):
            continue
        if path.is_file() and path.name in names:
            matches.append(path)
    return tuple(sorted(matches, key=lambda item: item.relative_to(repo_path).as_posix()))


def _parse_dependency_file(
    repo_path: Path,
    path: Path,
    ecosystem: str,
    manager: str,
    manifest_type: str,
    warnings: list[DependencyInventoryWarning],
) -> DependencyFile:
    relative_path = path.relative_to(repo_path).as_posix()
    try:
        parsed = _parse_manifest(path, manifest_type)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        warnings.append(
            DependencyInventoryWarning(
                code="malformed-manifest",
                source=relative_path,
                message=f"dependency manifest is malformed: {exc}",
            )
        )
        return DependencyFile(relative_path, ecosystem, manager, "malformed", None, (), (), str(exc))

    return DependencyFile(
        path=relative_path,
        ecosystem=ecosystem,
        manager=manager,
        status="parsed",
        package_name=parsed.package_name,
        dependencies=parsed.dependencies,
        dev_dependencies=parsed.dev_dependencies,
        detail=parsed.detail,
    )


def _parse_manifest(path: Path, manifest_type: str) -> ParsedManifest:
    if manifest_type == "package.json":
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise json.JSONDecodeError("package.json must be an object", "", 0)
        dependencies = _mapping_keys(artifact.get("dependencies"))
        dev_dependencies = _mapping_keys(artifact.get("devDependencies"))
        return ParsedManifest(
            package_name=_optional_str(artifact.get("name")),
            dependencies=dependencies,
            dev_dependencies=dev_dependencies,
            detail=f"dependencies={len(dependencies)}, dev_dependencies={len(dev_dependencies)}",
        )

    if manifest_type == "pyproject.toml":
        artifact = tomllib.loads(path.read_text(encoding="utf-8"))
        project = artifact.get("project")
        dependencies: tuple[str, ...] = ()
        optional_dependencies: tuple[str, ...] = ()
        package_name = None
        if isinstance(project, dict):
            package_name = _optional_str(project.get("name"))
            dependencies = _dependency_names(project.get("dependencies"))
            raw_optional_dependencies = project.get("optional-dependencies")
            if isinstance(raw_optional_dependencies, dict):
                optional_dependencies = tuple(
                    sorted(
                        {
                            dependency
                            for values in raw_optional_dependencies.values()
                            if isinstance(values, list)
                            for dependency in _dependency_names(values)
                        }
                    )
                )
        return ParsedManifest(
            package_name=package_name,
            dependencies=dependencies,
            dev_dependencies=optional_dependencies,
            detail=f"dependencies={len(dependencies)}, optional_dependencies={len(optional_dependencies)}",
        )

    if manifest_type == "requirements.txt":
        dependencies = _requirements_dependencies(path)
        return ParsedManifest(
            package_name=None,
            dependencies=dependencies,
            dev_dependencies=(),
            detail=f"dependencies={len(dependencies)}",
        )

    if manifest_type == "go.mod":
        dependencies = _go_mod_dependencies(path)
        return ParsedManifest(package_name=None, dependencies=dependencies, dev_dependencies=(), detail=None)

    if manifest_type == "Cargo.toml":
        artifact = tomllib.loads(path.read_text(encoding="utf-8"))
        package = artifact.get("package")
        package_name = _optional_str(package.get("name")) if isinstance(package, dict) else None
        dependencies = _mapping_keys(artifact.get("dependencies"))
        dev_dependencies = _mapping_keys(artifact.get("dev-dependencies"))
        return ParsedManifest(
            package_name=package_name,
            dependencies=dependencies,
            dev_dependencies=dev_dependencies,
            detail=f"dependencies={len(dependencies)}, dev_dependencies={len(dev_dependencies)}",
        )

    return ParsedManifest(package_name=None, dependencies=(), dev_dependencies=(), detail=None)


def _requirements_dependencies(path: Path) -> tuple[str, ...]:
    names = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-", "--")):
            continue
        match = _REQUIREMENTS_RE.match(line)
        if match:
            names.append(match.group(1).lower())
    return tuple(sorted(set(names)))


def _go_mod_dependencies(path: Path) -> tuple[str, ...]:
    dependencies = []
    in_require_block = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("module "):
            continue
        if line == "require (":
            in_require_block = True
            continue
        if in_require_block and line == ")":
            in_require_block = False
            continue
        if line.startswith("require "):
            parts = line.split()
            if len(parts) >= 2:
                dependencies.append(parts[1])
        elif in_require_block and line:
            parts = line.split()
            if parts:
                dependencies.append(parts[0])
    return tuple(sorted(set(dependencies)))


def _record_lockfile_gaps(
    dependency_files: tuple[DependencyFile, ...],
    lockfiles: tuple[DependencyLockfile, ...],
    missing: list[MissingDependencyEvidence],
) -> None:
    lockfile_ecosystems = {item.ecosystem for item in lockfiles}
    for ecosystem in sorted({item.ecosystem for item in dependency_files}):
        if ecosystem not in lockfile_ecosystems:
            missing.append(MissingDependencyEvidence("lockfile", ecosystem, "no recognized lockfile found"))


def _package_manager_evidence(
    dependency_files: tuple[DependencyFile, ...],
    lockfiles: tuple[DependencyLockfile, ...],
) -> tuple[DependencyEvidence, ...]:
    evidence: list[DependencyEvidence] = []
    for dependency_file in dependency_files:
        evidence.append(
            DependencyEvidence(
                ecosystem=dependency_file.ecosystem,
                manager=dependency_file.manager,
                source=dependency_file.path,
                detail=dependency_file.status,
            )
        )
    for lockfile in lockfiles:
        evidence.append(
            DependencyEvidence(
                ecosystem=lockfile.ecosystem,
                manager=lockfile.manager,
                source=lockfile.path,
                detail=lockfile.status,
            )
        )
    return tuple(sorted(evidence, key=lambda item: (item.ecosystem, item.manager, item.source)))


def _generated_pack_status(
    artifact_root: Path,
    coverage_status: str,
    pack_ref: str | None,
) -> GeneratedPackStatus:
    approval = _load_json_artifact(artifact_root / "approval.yml")
    evals = _load_json_artifact(artifact_root / "evals" / "onboarding.yml")
    approval_verified = approval.get("verified") if isinstance(approval, dict) else None
    tasks = evals.get("tasks") if isinstance(evals, dict) else None
    return GeneratedPackStatus(
        coverage_status=coverage_status,
        pack_ref=pack_ref,
        approval_status=_optional_str(approval.get("status")) if isinstance(approval, dict) else None,
        approval_verified=approval_verified if isinstance(approval_verified, bool) else None,
        eval_task_count=len(tasks) if isinstance(tasks, list) else None,
        skills_status="present" if (artifact_root / "skills").is_dir() else "missing",
        resolvers_status=_artifact_status(artifact_root / "resolvers.yml"),
        scan_status="present" if (artifact_root / "scan").is_dir() else "missing",
    )


def _record_generated_pack_missing(
    generated_pack: GeneratedPackStatus,
    artifact_root: Path,
    root: Path,
    missing: list[MissingDependencyEvidence],
) -> None:
    if generated_pack.coverage_status != "verified":
        missing.append(
            MissingDependencyEvidence("generated-pack", "coverage_status", "repo pack is not verified by eval replay")
        )
    for status, path, reason in (
        (generated_pack.skills_status, artifact_root / "skills", "skills directory is missing"),
        (generated_pack.resolvers_status, artifact_root / "resolvers.yml", "resolvers artifact is missing"),
        (generated_pack.scan_status, artifact_root / "scan", "scan directory is missing"),
    ):
        if status == "missing":
            missing.append(MissingDependencyEvidence("artifact", _relative(root, path), reason))
    if generated_pack.approval_status is None:
        missing.append(
            MissingDependencyEvidence(
                "artifact", _relative(root, artifact_root / "approval.yml"), "approval metadata is missing"
            )
        )
    if generated_pack.eval_task_count is None:
        missing.append(
            MissingDependencyEvidence(
                "artifact", _relative(root, artifact_root / "evals" / "onboarding.yml"), "eval evidence is missing"
            )
        )


def _load_json_artifact(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return artifact if isinstance(artifact, dict) else None


def _artifact_status(path: Path) -> str:
    if not path.is_file():
        return "missing"
    if _load_json_artifact(path) is None:
        return "malformed"
    return "present"


def _mapping_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(str(key) for key in value if isinstance(key, str) and key.strip()))


def _dependency_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = re.split(r"[<>=!~;\[\] ]", item.strip(), maxsplit=1)[0]
        if name:
            names.append(name.lower())
    return tuple(sorted(set(names)))


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
