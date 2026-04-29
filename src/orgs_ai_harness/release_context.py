"""Read-only release readiness context gathered from repo artifacts."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ReleaseContextError(Exception):
    """Raised when release context cannot resolve the requested repository."""


@dataclass(frozen=True)
class ArtifactStatus:
    name: str
    path: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class MissingReleaseEvidence:
    kind: str
    path: str
    reason: str


@dataclass(frozen=True)
class LocalRepoStatus:
    configured_path: str | None
    resolved_path: Path | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class LifecycleStatus:
    registry_status: str
    active: bool
    external: bool
    pack_ref: str | None
    supported: bool
    reason: str | None = None
    approval_status: str | None = None
    approval_decision: str | None = None
    approval_verified: bool | None = None
    eval_status: str | None = None
    eval_pass_rate: float | None = None
    eval_task_count: int | None = None


@dataclass(frozen=True)
class PackReportMetadata:
    path: str
    title: str | None
    status: str | None
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class UnknownCoverage:
    id: str
    question: str | None
    severity: str | None
    status: str | None
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class ScanEvidenceCategory:
    category: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedSkill:
    name: str
    path: str
    description: str | None


@dataclass(frozen=True)
class GeneratedResolver:
    skill: str
    intent: str | None
    when: tuple[str, ...]


@dataclass(frozen=True)
class LocalReleaseEvidence:
    category: str
    path: str
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class ReleaseContext:
    repo_id: str
    repo_name: str
    registry_entry: RepoEntry
    artifact_root: Path
    local_repo: LocalRepoStatus
    lifecycle: LifecycleStatus
    artifacts: tuple[ArtifactStatus, ...]
    pack_report: PackReportMetadata | None
    unknowns: tuple[UnknownCoverage, ...]
    scan_evidence: tuple[ScanEvidenceCategory, ...]
    generated_skills: tuple[GeneratedSkill, ...]
    generated_resolvers: tuple[GeneratedResolver, ...]
    local_release_evidence: tuple[LocalReleaseEvidence, ...]
    missing_evidence: tuple[MissingReleaseEvidence, ...]


CHANGELOG_FILES = (
    "CHANGELOG.md",
    "CHANGELOG",
    "HISTORY.md",
    "NEWS.md",
    "RELEASES.md",
)
VERSION_FILES = (
    "VERSION",
    "VERSION.txt",
    "version.txt",
)
PACKAGE_MANIFESTS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
)
LOCKFILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "go.sum",
)
CI_FILES = (
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    "cloudbuild.yaml",
    "Jenkinsfile",
)
MIGRATION_DIRS = (
    "migrations",
    "db/migrations",
    "database/migrations",
    "prisma/migrations",
    "alembic/versions",
)
DEPLOYMENT_PATHS = (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Procfile",
    "vercel.json",
    "netlify.toml",
    "fly.toml",
    "render.yaml",
    "railway.json",
    "wrangler.toml",
    "k8s",
    "kubernetes",
    "helm",
    "charts",
)


def build_release_context(root: Path, repo_id: str) -> ReleaseContext:
    """Build deterministic, read-only context for release readiness review."""

    root = root.resolve()
    entry = _find_repo_entry(root, repo_id)
    artifact_root = root / "repos" / entry.id
    artifacts: list[ArtifactStatus] = []
    missing: list[MissingReleaseEvidence] = []

    local_repo = _resolve_local_repo(root, entry)
    approval = _load_json_artifact(root, artifact_root / "approval.yml", "approval", artifacts, missing)
    eval_report = _load_json_artifact(root, artifact_root / "eval-report.yml", "eval-report", artifacts, missing)
    evals = _load_json_artifact(root, artifact_root / "evals" / "onboarding.yml", "evals", artifacts, missing)
    pack_report = _load_pack_report(root, artifact_root / "pack-report.md", artifacts, missing)
    unknowns = _load_unknowns(root, artifact_root, artifacts, missing)
    scan_evidence = _load_scan_evidence(root, artifact_root, artifacts, missing)
    generated_skills = _load_generated_skills(root, artifact_root, artifacts, missing)
    generated_resolvers = _load_generated_resolvers(root, artifact_root, artifacts, missing)
    local_release_evidence = _collect_local_release_evidence(local_repo, missing)
    lifecycle = _build_lifecycle(entry, approval, eval_report, evals)

    return ReleaseContext(
        repo_id=entry.id,
        repo_name=entry.name,
        registry_entry=entry,
        artifact_root=artifact_root,
        local_repo=local_repo,
        lifecycle=lifecycle,
        artifacts=tuple(artifacts),
        pack_report=pack_report,
        unknowns=unknowns,
        scan_evidence=scan_evidence,
        generated_skills=generated_skills,
        generated_resolvers=generated_resolvers,
        local_release_evidence=tuple(local_release_evidence),
        missing_evidence=tuple(missing),
    )


def _find_repo_entry(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ReleaseContextError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == normalized_repo_id:
            return entry
    raise ReleaseContextError(f"repo id is not registered: {normalized_repo_id}")


def _resolve_local_repo(root: Path, entry: RepoEntry) -> LocalRepoStatus:
    if entry.local_path is None:
        return LocalRepoStatus(None, None, "missing", "repo has no local path")
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.exists():
        return LocalRepoStatus(entry.local_path, repo_path, "missing", "repo path does not exist")
    if not repo_path.is_dir():
        return LocalRepoStatus(entry.local_path, repo_path, "invalid", "repo path is not a directory")
    return LocalRepoStatus(entry.local_path, repo_path, "available")


def _build_lifecycle(
    entry: RepoEntry,
    approval: dict[str, object] | None,
    eval_report: dict[str, object] | None,
    evals: dict[str, object] | None,
) -> LifecycleStatus:
    supported = True
    reason = None
    if entry.external or entry.coverage_status == "external":
        supported = False
        reason = "repo is an external dependency reference"
    elif not entry.active:
        supported = False
        reason = "repo is not active selected coverage"
    elif entry.local_path is None:
        supported = False
        reason = "repo has no local path"

    approval_status = _str_field(approval, "status")
    approval_decision = _str_field(approval, "decision")
    approval_verified = approval.get("verified") if isinstance(approval, dict) else None
    eval_status = _str_field(eval_report, "status")
    eval_pass_rate = _float_field(eval_report, "skill_pack_pass_rate")
    eval_tasks = evals.get("tasks") if isinstance(evals, dict) else None
    return LifecycleStatus(
        registry_status=entry.coverage_status,
        active=entry.active,
        external=entry.external,
        pack_ref=entry.pack_ref,
        supported=supported,
        reason=reason,
        approval_status=approval_status,
        approval_decision=approval_decision,
        approval_verified=approval_verified if isinstance(approval_verified, bool) else None,
        eval_status=eval_status,
        eval_pass_rate=eval_pass_rate,
        eval_task_count=len(eval_tasks) if isinstance(eval_tasks, list) else None,
    )


def _load_pack_report(
    root: Path,
    path: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> PackReportMetadata | None:
    relative_path = _relative(root, path)
    if not path.is_file():
        artifacts.append(ArtifactStatus("pack-report", relative_path, "missing", "artifact is missing"))
        missing.append(MissingReleaseEvidence("artifact", relative_path, "artifact is missing"))
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        artifacts.append(ArtifactStatus("pack-report", relative_path, "malformed", str(exc)))
        missing.append(MissingReleaseEvidence("artifact", relative_path, f"artifact is unreadable: {exc}"))
        return None
    title = None
    fields: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if title is None and line.startswith("# "):
            title = line.removeprefix("# ").strip() or None
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line.removeprefix("- ").split(":", 1)
        fields.append((key.strip(), value.strip()))
    status = next((value for key, value in fields if key == "Status"), None)
    artifacts.append(ArtifactStatus("pack-report", relative_path, "loaded"))
    if status is None:
        missing.append(MissingReleaseEvidence("artifact", relative_path, "pack report does not state status"))
    return PackReportMetadata(relative_path, title, status, tuple(fields))


def _load_unknowns(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> tuple[UnknownCoverage, ...]:
    path = artifact_root / "unknowns.yml"
    artifact = _load_json_artifact(root, path, "unknowns", artifacts, missing)
    if not isinstance(artifact, dict):
        return ()
    raw_unknowns = artifact.get("unknowns")
    if not isinstance(raw_unknowns, list):
        missing.append(MissingReleaseEvidence("artifact", _relative(root, path), "unknowns field must be a list"))
        return ()
    unknowns: list[UnknownCoverage] = []
    for item in raw_unknowns:
        if not isinstance(item, dict):
            continue
        unknown_id = item.get("id")
        if not isinstance(unknown_id, str) or not unknown_id.strip():
            continue
        evidence_paths = []
        for evidence in _as_dict_list(item.get("evidence")):
            evidence_path = evidence.get("path")
            if isinstance(evidence_path, str) and evidence_path.strip():
                evidence_paths.append(_normalize_relative_path(evidence_path))
        unknowns.append(
            UnknownCoverage(
                id=unknown_id,
                question=_optional_str(item.get("question")),
                severity=_optional_str(item.get("severity")),
                status=_optional_str(item.get("status")),
                evidence_paths=tuple(sorted(set(evidence_paths))),
            )
        )
    return tuple(unknowns)


def _load_scan_evidence(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> tuple[ScanEvidenceCategory, ...]:
    evidence: dict[str, set[str]] = {}
    manifest_path = artifact_root / "scan" / "scan-manifest.yml"
    manifest = _load_json_artifact(root, manifest_path, "scan-manifest", artifacts, missing)
    if isinstance(manifest, dict):
        for item in _as_dict_list(manifest.get("scanned_paths")):
            category = item.get("category")
            path = item.get("path")
            if isinstance(category, str) and isinstance(path, str):
                evidence.setdefault(category, set()).add(_normalize_relative_path(path))

    hypothesis_path = artifact_root / "scan" / "hypothesis-map.yml"
    hypothesis = _load_json_artifact(root, hypothesis_path, "hypothesis-map", artifacts, missing)
    if isinstance(hypothesis, dict):
        categories = hypothesis.get("evidence_categories")
        if isinstance(categories, dict):
            for category, paths in categories.items():
                if not isinstance(category, str) or not isinstance(paths, list):
                    continue
                for path in paths:
                    if isinstance(path, str):
                        evidence.setdefault(category, set()).add(_normalize_relative_path(path))
    return tuple(ScanEvidenceCategory(category, tuple(sorted(paths))) for category, paths in sorted(evidence.items()))


def _load_generated_skills(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> tuple[GeneratedSkill, ...]:
    skills_root = artifact_root / "skills"
    relative_root = _relative(root, skills_root)
    if not skills_root.is_dir():
        artifacts.append(ArtifactStatus("skills", relative_root, "missing", "skills directory is missing"))
        missing.append(MissingReleaseEvidence("artifact", relative_root, "skills directory is missing"))
        return ()
    artifacts.append(ArtifactStatus("skills", relative_root, "loaded"))
    skills: list[GeneratedSkill] = []
    for skill_root in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_path = skill_root / "SKILL.md"
        relative_skill_path = _relative(root, skill_path)
        if not skill_path.is_file():
            missing.append(MissingReleaseEvidence("artifact", relative_skill_path, "SKILL.md is missing"))
            continue
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            missing.append(MissingReleaseEvidence("artifact", relative_skill_path, f"cannot read SKILL.md: {exc}"))
            continue
        frontmatter = _parse_frontmatter(text)
        if frontmatter is None:
            missing.append(MissingReleaseEvidence("artifact", relative_skill_path, "SKILL.md frontmatter is malformed"))
            continue
        skills.append(
            GeneratedSkill(
                name=frontmatter.get("name") or skill_root.name,
                path=relative_skill_path,
                description=frontmatter.get("description"),
            )
        )
    return tuple(skills)


def _load_generated_resolvers(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> tuple[GeneratedResolver, ...]:
    path = artifact_root / "resolvers.yml"
    artifact = _load_json_artifact(root, path, "resolvers", artifacts, missing)
    if not isinstance(artifact, dict):
        return ()
    raw_resolvers = artifact.get("resolvers")
    if not isinstance(raw_resolvers, list):
        missing.append(MissingReleaseEvidence("artifact", _relative(root, path), "resolvers field must be a list"))
        return ()
    resolvers: list[GeneratedResolver] = []
    for item in raw_resolvers:
        if not isinstance(item, dict):
            continue
        skill = item.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            continue
        when = item.get("when")
        resolvers.append(
            GeneratedResolver(
                skill=skill,
                intent=_optional_str(item.get("intent")),
                when=tuple(str(value) for value in when if isinstance(value, str)) if isinstance(when, list) else (),
            )
        )
    return tuple(resolvers)


def _load_json_artifact(
    root: Path,
    path: Path,
    name: str,
    artifacts: list[ArtifactStatus],
    missing: list[MissingReleaseEvidence],
) -> dict[str, object] | None:
    relative_path = _relative(root, path)
    if not path.is_file():
        artifacts.append(ArtifactStatus(name, relative_path, "missing", "artifact is missing"))
        missing.append(MissingReleaseEvidence("artifact", relative_path, "artifact is missing"))
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        artifacts.append(ArtifactStatus(name, relative_path, "malformed", str(exc)))
        missing.append(MissingReleaseEvidence("artifact", relative_path, f"artifact is malformed: {exc}"))
        return None
    if not isinstance(artifact, dict):
        artifacts.append(ArtifactStatus(name, relative_path, "malformed", "artifact must be an object"))
        missing.append(MissingReleaseEvidence("artifact", relative_path, "artifact must be an object"))
        return None
    artifacts.append(ArtifactStatus(name, relative_path, "loaded"))
    return artifact


def _collect_local_release_evidence(
    local_repo: LocalRepoStatus,
    missing: list[MissingReleaseEvidence],
) -> list[LocalReleaseEvidence]:
    repo_path = local_repo.resolved_path
    if repo_path is None or local_repo.status != "available":
        reason = local_repo.reason or "local repo is unavailable"
        for category in ("changelog", "version", "lockfile", "ci", "migration", "deployment"):
            missing.append(MissingReleaseEvidence(category, "-", reason))
        return []

    evidence: list[LocalReleaseEvidence] = []
    evidence.extend(_collect_existing_files(repo_path, "changelog", CHANGELOG_FILES))
    evidence.extend(_collect_version_evidence(repo_path, missing))
    evidence.extend(_collect_existing_files(repo_path, "lockfile", LOCKFILES))
    evidence.extend(_collect_ci_evidence(repo_path))
    evidence.extend(_collect_existing_dirs(repo_path, "migration", MIGRATION_DIRS))
    evidence.extend(_collect_deployment_evidence(repo_path))

    _record_missing_local_categories(evidence, missing)
    return sorted(evidence, key=lambda item: (item.category, item.path, item.status))


def _collect_existing_files(repo_path: Path, category: str, candidates: tuple[str, ...]) -> list[LocalReleaseEvidence]:
    evidence = []
    for relative in candidates:
        path = repo_path / relative
        if path.is_file():
            evidence.append(LocalReleaseEvidence(category, relative, "present"))
    return evidence


def _collect_existing_dirs(repo_path: Path, category: str, candidates: tuple[str, ...]) -> list[LocalReleaseEvidence]:
    evidence = []
    for relative in candidates:
        path = repo_path / relative
        if path.is_dir():
            file_count = sum(1 for child in path.rglob("*") if child.is_file())
            evidence.append(LocalReleaseEvidence(category, relative, "present", f"files={file_count}"))
    return evidence


def _collect_ci_evidence(repo_path: Path) -> list[LocalReleaseEvidence]:
    evidence = _collect_existing_files(repo_path, "ci", CI_FILES)
    workflows_root = repo_path / ".github" / "workflows"
    if workflows_root.is_dir():
        for path in sorted(workflows_root.iterdir()):
            if path.is_file() and path.suffix in {".yml", ".yaml"}:
                evidence.append(LocalReleaseEvidence("ci", path.relative_to(repo_path).as_posix(), "present"))
    return evidence


def _collect_deployment_evidence(repo_path: Path) -> list[LocalReleaseEvidence]:
    evidence = []
    for relative in DEPLOYMENT_PATHS:
        path = repo_path / relative
        if path.is_file():
            evidence.append(LocalReleaseEvidence("deployment", relative, "present"))
        elif path.is_dir():
            file_count = sum(1 for child in path.rglob("*") if child.is_file())
            evidence.append(LocalReleaseEvidence("deployment", relative, "present", f"files={file_count}"))
    return evidence


def _collect_version_evidence(
    repo_path: Path,
    missing: list[MissingReleaseEvidence],
) -> list[LocalReleaseEvidence]:
    evidence: list[LocalReleaseEvidence] = []
    explicit_version_found = False
    for relative in VERSION_FILES:
        path = repo_path / relative
        if not path.is_file():
            continue
        value = path.read_text(encoding="utf-8", errors="replace").splitlines()
        version = next((line.strip() for line in value if line.strip()), "")
        explicit_version_found = explicit_version_found or bool(version)
        detail = f"version={version}" if version else "empty version file"
        evidence.append(LocalReleaseEvidence("version", relative, "parsed" if version else "present", detail))

    for relative in PACKAGE_MANIFESTS:
        path = repo_path / relative
        if not path.is_file():
            continue
        parsed = _parse_manifest_version(path)
        if parsed["status"] == "parsed":
            explicit_version_found = True
        elif parsed["status"] == "malformed":
            missing.append(MissingReleaseEvidence("version", relative, str(parsed["detail"])))
        evidence.append(
            LocalReleaseEvidence("version", relative, str(parsed["status"]), _optional_str(parsed.get("detail")))
        )

    if not explicit_version_found:
        missing.append(
            MissingReleaseEvidence(
                "version",
                "VERSION/package manifests",
                "no explicit version found in version files or supported package manifests",
            )
        )
    return evidence


def _parse_manifest_version(path: Path) -> dict[str, object]:
    try:
        if path.name == "package.json":
            artifact = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(artifact, dict):
                return {"status": "malformed", "detail": "package.json must be an object"}
            version = artifact.get("version")
            name = artifact.get("name")
            if isinstance(version, str) and version.strip():
                label = f"version={version.strip()}"
                if isinstance(name, str) and name.strip():
                    label = f"name={name.strip()}, {label}"
                return {"status": "parsed", "detail": label}
            return {"status": "present", "detail": "version field missing"}

        artifact = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return {"status": "malformed", "detail": str(exc)}

    version = _toml_version(path.name, artifact)
    if version is None:
        return {"status": "present", "detail": "version field missing"}
    return {"status": "parsed", "detail": f"version={version}"}


def _toml_version(name: str, artifact: dict[str, Any]) -> str | None:
    if name == "pyproject.toml":
        project = artifact.get("project")
        if isinstance(project, dict) and isinstance(project.get("version"), str):
            return project["version"]
        tool = artifact.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict) and isinstance(poetry.get("version"), str):
            return poetry["version"]
        return None
    if name == "Cargo.toml":
        package = artifact.get("package")
        if isinstance(package, dict) and isinstance(package.get("version"), str):
            return package["version"]
    return None


def _record_missing_local_categories(
    evidence: list[LocalReleaseEvidence],
    missing: list[MissingReleaseEvidence],
) -> None:
    present_categories = {item.category for item in evidence if item.status != "malformed"}
    messages = {
        "changelog": ("CHANGELOG.md", "no changelog file found"),
        "lockfile": ("known lockfiles", "no lockfile found"),
        "ci": (".github/workflows", "no CI workflow/config found"),
        "migration": ("known migration dirs", "no migration directory found"),
        "deployment": ("known deployment config", "no deployment config found"),
    }
    for category, (path, reason) in messages.items():
        if category not in present_categories:
            missing.append(MissingReleaseEvidence(category, path, reason))


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    frontmatter: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return frontmatter
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return None


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _str_field(artifact: dict[str, object] | None, field: str) -> str | None:
    if not isinstance(artifact, dict):
        return None
    return _optional_str(artifact.get(field))


def _float_field(artifact: dict[str, object] | None, field: str) -> float | None:
    if not isinstance(artifact, dict):
        return None
    value = artifact.get(field)
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_relative_path(path: str) -> str:
    return Path(path.strip()).as_posix().strip("/")


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
