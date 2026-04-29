"""Deterministic pull request changed-file risk reporting."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from orgs_ai_harness.pr_review import ReviewChangedFiles
from orgs_ai_harness.runtime_permissions import PermissionLevel, classify_command


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class FileRisk:
    path: str
    level: RiskLevel
    category: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ValidationSuggestion:
    command: str
    permission: PermissionLevel
    sources: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvalSuggestion:
    eval_id: str
    matched_files: tuple[str, ...]
    expected_files: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class PrRiskWarning:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class PrRiskReport:
    repo_id: str
    repo_path: Path
    source: str
    changed_files: tuple[str, ...]
    overall_risk: RiskLevel
    file_risks: tuple[FileRisk, ...]
    validation_suggestions: tuple[ValidationSuggestion, ...]
    eval_suggestions: tuple[EvalSuggestion, ...]
    warnings: tuple[PrRiskWarning, ...]


@dataclass(frozen=True)
class _CommandEvidence:
    command: str
    source: str
    reason: str


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}

_DOC_NAMES = {
    "readme",
    "changelog",
    "contributing",
    "code_of_conduct",
    "license",
    "notice",
    "authors",
}
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".adoc"}
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".swift",
    ".ts",
    ".tsx",
}
_SOURCE_DIRS = {"app", "cmd", "lib", "pkg", "server", "src"}
_TEST_DIRS = {"__tests__", "spec", "test", "tests"}
_CI_PATHS = {
    ".buildkite/pipeline.yml",
    ".circleci/config.yml",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "jenkinsfile",
}
_DEPENDENCY_FILES = {
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pipfile",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pom.xml",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
_DEPENDENCY_SUFFIXES = (".csproj", ".fsproj", ".gemspec", ".gradle", ".sln", ".vcxproj")
_GENERATED_DIRS = {
    ".agent-harness",
    "__generated__",
    "build",
    "coverage",
    "dist",
    "generated",
    "gen",
    "node_modules",
    "org-agent-skills",
    "target",
}
_SENSITIVE_SUFFIXES = (".key", ".p12", ".pem", ".pfx")
_SENSITIVE_NAME_PARTS = ("credential", "credentials", "secret", "secrets", "token", "tokens")
_MAKE_VALIDATION_TARGETS = ("lint", "test", "verify")


def build_pr_risk_report(root: Path, review: ReviewChangedFiles) -> PrRiskReport:
    """Build a read-only risk report for a PR changed-file set."""

    root = root.resolve()
    changed_files = tuple(sorted(review.changed_files))
    file_risks = tuple(_classify_file(path) for path in changed_files)
    overall_risk = _overall_risk(file_risks)
    artifact_root = root / "repos" / review.repo_id

    warnings: list[PrRiskWarning] = []
    eval_suggestions = _load_eval_suggestions(artifact_root, review.repo_id, changed_files, warnings)
    evidence = _command_evidence(root, artifact_root, review.repo_id, review.repo_path, warnings)
    validation_suggestions = _filter_validation_suggestions(evidence, warnings)

    return PrRiskReport(
        repo_id=review.repo_id,
        repo_path=review.repo_path,
        source=review.source,
        changed_files=changed_files,
        overall_risk=overall_risk,
        file_risks=file_risks,
        validation_suggestions=validation_suggestions,
        eval_suggestions=eval_suggestions,
        warnings=tuple(sorted(set(warnings), key=lambda warning: (warning.source, warning.code, warning.message))),
    )


def _classify_file(path: str) -> FileRisk:
    normalized = Path(path).as_posix().strip("/")
    lower = normalized.lower()
    parts = tuple(part.lower() for part in Path(lower).parts)
    name = parts[-1] if parts else lower
    stem = Path(name).stem
    suffix = Path(name).suffix
    reasons: list[str] = []

    if _is_sensitive_path(lower):
        reasons.append("matches sensitive filename policy")
        return FileRisk(normalized, RiskLevel.HIGH, "sensitive", tuple(reasons))
    if _is_ci_path(lower):
        reasons.append("changes CI or workflow configuration")
        return FileRisk(normalized, RiskLevel.HIGH, "ci", tuple(reasons))
    if _is_dependency_path(lower):
        reasons.append("changes dependency or package manager metadata")
        return FileRisk(normalized, RiskLevel.HIGH, "dependency", tuple(reasons))
    if _is_generated_path(parts, name):
        reasons.append("changes generated or build artifact paths")
        return FileRisk(normalized, RiskLevel.HIGH, "generated", tuple(reasons))
    if _is_test_path(parts, name, suffix):
        reasons.append("changes test code")
        return FileRisk(normalized, RiskLevel.MEDIUM, "test", tuple(reasons))
    if _is_source_path(parts, suffix):
        reasons.append("changes source code")
        return FileRisk(normalized, RiskLevel.MEDIUM, "source", tuple(reasons))
    if _is_docs_path(parts, stem, suffix):
        reasons.append("changes documentation")
        return FileRisk(normalized, RiskLevel.LOW, "docs", tuple(reasons))
    return FileRisk(normalized, RiskLevel.MEDIUM, "unknown", ("path type is not recognized",))


def _is_sensitive_path(path: str) -> bool:
    name = Path(path).name.lower()
    stem = Path(name).stem.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(_SENSITIVE_SUFFIXES):
        return True
    if name.endswith(".local") or ".local." in name:
        return True
    if any(part in name for part in _SENSITIVE_NAME_PARTS):
        return True
    return stem in {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}


def _is_ci_path(path: str) -> bool:
    return (
        path.startswith(".github/workflows/")
        or path.startswith(".github/actions/")
        or path in _CI_PATHS
        or path.startswith(".buildkite/")
    )


def _is_dependency_path(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name in _DEPENDENCY_FILES
        or (name.startswith("requirements-") and name.endswith(".txt"))
        or name.endswith(_DEPENDENCY_SUFFIXES)
    )


def _is_generated_path(parts: tuple[str, ...], name: str) -> bool:
    if any(part in _GENERATED_DIRS for part in parts):
        return True
    return ".generated." in name or name.endswith((".pb.go", ".pb.ts", ".generated.py"))


def _is_test_path(parts: tuple[str, ...], name: str, suffix: str) -> bool:
    if any(part in _TEST_DIRS for part in parts):
        return True
    return suffix in _SOURCE_SUFFIXES and (
        name.startswith("test_") or name.endswith("_test.py") or name.endswith(".test.ts") or name.endswith(".spec.ts")
    )


def _is_source_path(parts: tuple[str, ...], suffix: str) -> bool:
    return suffix in _SOURCE_SUFFIXES or any(part in _SOURCE_DIRS for part in parts)


def _is_docs_path(parts: tuple[str, ...], stem: str, suffix: str) -> bool:
    return (bool(parts) and parts[0] == "docs") or stem in _DOC_NAMES or suffix in _DOC_SUFFIXES


def _overall_risk(file_risks: tuple[FileRisk, ...]) -> RiskLevel:
    if not file_risks:
        return RiskLevel.LOW
    return max((risk.level for risk in file_risks), key=lambda level: _RISK_ORDER[level])


def _command_evidence(
    root: Path,
    artifact_root: Path,
    repo_id: str,
    repo_path: Path,
    warnings: list[PrRiskWarning],
) -> tuple[_CommandEvidence, ...]:
    evidence: list[_CommandEvidence] = [
        _CommandEvidence(
            command=f"harness validate {repo_id}",
            source="built-in:harness-validate",
            reason="Validate generated repo onboarding and approval metadata locally.",
        )
    ]
    manifest = _load_json_artifact(artifact_root / "scripts" / "manifest.yml", root, "script-manifest", warnings)
    if isinstance(manifest, dict):
        evidence.extend(_commands_from_script_manifest(manifest, repo_id))

    evals = _load_json_artifact(artifact_root / "evals" / "onboarding.yml", root, "evals", warnings)
    if isinstance(evals, dict):
        evidence.extend(_commands_from_evals(evals))

    hypothesis_map = _load_json_artifact(
        artifact_root / "scan" / "hypothesis-map.yml",
        root,
        "hypothesis-map",
        warnings,
    )
    if isinstance(hypothesis_map, dict):
        evidence.extend(_commands_from_hypothesis_map(hypothesis_map))

    evidence.extend(_commands_from_repo_manifests(repo_path))
    return tuple(sorted(evidence, key=lambda item: (item.command, item.source, item.reason)))


def _load_eval_suggestions(
    artifact_root: Path,
    repo_id: str,
    changed_files: tuple[str, ...],
    warnings: list[PrRiskWarning],
) -> tuple[EvalSuggestion, ...]:
    evals_path = artifact_root / "evals" / "onboarding.yml"
    artifact = _load_json_artifact(evals_path, artifact_root.parents[1], "evals", warnings)
    if not isinstance(artifact, dict):
        return ()
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list):
        warnings.append(
            PrRiskWarning(
                code="malformed-artifact",
                source=_display_path(evals_path, artifact_root.parents[1]),
                message="evals artifact field tasks must be a list",
            )
        )
        return ()

    suggestions: list[EvalSuggestion] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        eval_id = task.get("id")
        expected_files = _string_tuple(task.get("expected_files"))
        if not isinstance(eval_id, str) or not eval_id.strip() or not expected_files:
            continue
        matched = tuple(
            sorted(
                changed
                for changed in changed_files
                if set(_artifact_relative_variants(changed, repo_id)) & set(expected_files)
            )
        )
        if not matched:
            continue
        suggestions.append(
            EvalSuggestion(
                eval_id=eval_id,
                matched_files=matched,
                expected_files=expected_files,
                source=f"repos/{repo_id}/evals/onboarding.yml",
            )
        )
    return tuple(sorted(suggestions, key=lambda item: item.eval_id))


def _commands_from_script_manifest(artifact: dict[str, object], repo_id: str) -> tuple[_CommandEvidence, ...]:
    permissions = artifact.get("command_permissions")
    if not isinstance(permissions, list):
        return ()

    evidence: list[_CommandEvidence] = []
    for permission in permissions:
        if not isinstance(permission, dict):
            continue
        command = permission.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        reason = permission.get("reason")
        evidence.append(
            _CommandEvidence(
                command=command.strip(),
                source=f"repos/{repo_id}/scripts/manifest.yml:command_permissions",
                reason=reason.strip() if isinstance(reason, str) and reason.strip() else "Command permission record.",
            )
        )
    return tuple(evidence)


def _commands_from_evals(artifact: dict[str, object]) -> tuple[_CommandEvidence, ...]:
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list):
        return ()

    evidence: list[_CommandEvidence] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        eval_id = task.get("id")
        source = f"evals/onboarding.yml:{eval_id}" if isinstance(eval_id, str) and eval_id else "evals/onboarding.yml"
        for command in _string_tuple(task.get("expected_commands")):
            evidence.append(
                _CommandEvidence(command=command, source=source, reason="Expected command from onboarding eval.")
            )
    return tuple(evidence)


def _commands_from_hypothesis_map(artifact: dict[str, object]) -> tuple[_CommandEvidence, ...]:
    hypotheses = artifact.get("hypotheses")
    if not isinstance(hypotheses, list):
        return ()

    evidence: list[_CommandEvidence] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict) or hypothesis.get("name") != "test_command_candidates":
            continue
        for command in _string_tuple(hypothesis.get("value")):
            evidence.append(
                _CommandEvidence(
                    command=command,
                    source="scan/hypothesis-map.yml:test_command_candidates",
                    reason="Test command candidate inferred during repo scan.",
                )
            )
    return tuple(evidence)


def _commands_from_repo_manifests(repo_path: Path) -> tuple[_CommandEvidence, ...]:
    evidence: list[_CommandEvidence] = []
    makefile = repo_path / "Makefile"
    if makefile.is_file():
        targets = _makefile_targets(makefile)
        for target in _MAKE_VALIDATION_TARGETS:
            if target in targets:
                evidence.append(
                    _CommandEvidence(
                        command=f"make {target}",
                        source=f"repo-manifest:{makefile.name}",
                        reason=f"Deterministic Makefile target: {target}.",
                    )
                )

    package_json = repo_path / "package.json"
    if package_json.is_file():
        command = _package_json_test_command(repo_path, package_json)
        if command is not None:
            evidence.append(
                _CommandEvidence(
                    command=command,
                    source="repo-manifest:package.json",
                    reason="Package manifest declares a test script.",
                )
            )

    if (repo_path / "pyproject.toml").is_file() or (repo_path / "pytest.ini").is_file():
        command = "uv run pytest" if (repo_path / "uv.lock").is_file() else "python -m pytest"
        evidence.append(
            _CommandEvidence(
                command=command,
                source="repo-manifest:python-tests",
                reason="Python manifest or pytest config indicates local pytest validation.",
            )
        )
    return tuple(evidence)


def _filter_validation_suggestions(
    evidence: tuple[_CommandEvidence, ...],
    warnings: list[PrRiskWarning],
) -> tuple[ValidationSuggestion, ...]:
    grouped: dict[str, list[_CommandEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.command, []).append(item)

    suggestions: list[ValidationSuggestion] = []
    for command in sorted(grouped):
        try:
            permission = _classify_validation_command(command)
        except ValueError as exc:
            warnings.append(
                PrRiskWarning(
                    code="invalid-command",
                    source="command-evidence",
                    message=f"skipped command {command!r}: {exc}",
                )
            )
            continue
        if permission == PermissionLevel.HIGH_RISK:
            warnings.append(
                PrRiskWarning(
                    code="unsupported-command",
                    source="command-evidence",
                    message=f"skipped command {command!r}: runtime permission classifier marked it high-risk",
                )
            )
            continue

        sources = tuple(sorted({item.source for item in grouped[command]}))
        reasons = tuple(sorted({item.reason for item in grouped[command]}))
        suggestions.append(
            ValidationSuggestion(command=command, permission=permission, sources=sources, reasons=reasons)
        )
    return tuple(suggestions)


def _classify_validation_command(command: str) -> PermissionLevel:
    argv = shlex.split(command)
    if len(argv) == 3 and argv[0] == "harness" and argv[1] == "validate":
        argv = ["python", "-m", "orgs_ai_harness", "validate", argv[2]]
    return classify_command(argv)


def _load_json_artifact(
    path: Path,
    root: Path,
    label: str,
    warnings: list[PrRiskWarning],
) -> object | None:
    if not path.is_file():
        warnings.append(
            PrRiskWarning(
                code="missing-artifact",
                source=_display_path(path, root),
                message=f"missing {label} artifact",
            )
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(
            PrRiskWarning(
                code="malformed-artifact",
                source=_display_path(path, root),
                message=f"{label} artifact is malformed: {exc.msg}",
            )
        )
        return None


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({item.strip() for item in value if isinstance(item, str) and item.strip()}))


def _artifact_relative_variants(path: str, repo_id: str) -> tuple[str, ...]:
    normalized = Path(path).as_posix().strip("/")
    variants = {normalized}
    for prefix in (f"repos/{repo_id}/", f"org-agent-skills/repos/{repo_id}/"):
        if normalized.startswith(prefix):
            variants.add(normalized.removeprefix(prefix))
    return tuple(sorted(variants))


def _makefile_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?:\s|$)")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            targets.add(match.group(1))
    return targets


def _package_json_test_command(repo_path: Path, path: Path) -> str | None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(manifest, dict):
        return None
    scripts = manifest.get("scripts")
    if not isinstance(scripts, dict) or not isinstance(scripts.get("test"), str):
        return None
    if (repo_path / "bun.lock").is_file() or (repo_path / "bun.lockb").is_file():
        return "bun test"
    if (repo_path / "pnpm-lock.yaml").is_file():
        return "pnpm test"
    if (repo_path / "yarn.lock").is_file():
        return "yarn test"
    return "npm test"
