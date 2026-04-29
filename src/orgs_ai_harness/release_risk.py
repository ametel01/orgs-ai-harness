"""Deterministic release readiness risk classification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.pr_risk import (
    EvalSuggestion,
    PrRiskWarning,
    RiskLevel,
    ValidationSuggestion,
    _classify_file,
    _command_evidence,
    _filter_validation_suggestions,
    _load_eval_suggestions,
)
from orgs_ai_harness.release_readiness import ReleaseReadinessInput
from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


@dataclass(frozen=True)
class ReleaseRiskItem:
    level: RiskLevel
    category: str
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseRiskWarning:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class ReleaseRiskReport:
    repo_id: str
    repo_path: Path
    version: str | None
    base: str | None
    head: str | None
    changed_files: tuple[str, ...]
    overall_risk: RiskLevel
    items: tuple[ReleaseRiskItem, ...]
    validation_suggestions: tuple[ValidationSuggestion, ...]
    eval_suggestions: tuple[EvalSuggestion, ...]
    warnings: tuple[ReleaseRiskWarning, ...]


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}

_CHANGELOG_NAMES = {
    "changelog",
    "changes",
    "history",
    "release-notes",
    "releases",
    "version",
}
_VERSION_FILES = {
    ".bumpversion.cfg",
    ".release-please-manifest.json",
    "cargo.toml",
    "package.json",
    "pyproject.toml",
    "version",
    "version.txt",
}
_MIGRATION_PARTS = {"migration", "migrations", "migrate", "schema"}
_DEPLOYMENT_PARTS = {
    ".helm",
    ".k8s",
    "chart",
    "charts",
    "deploy",
    "deployment",
    "deployments",
    "docker",
    "helm",
    "infra",
    "infrastructure",
    "k8s",
    "kubernetes",
    "terraform",
}
_DEPLOYMENT_NAMES = {
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "fly.toml",
    "netlify.toml",
    "procfile",
    "render.yaml",
    "terraform.tf",
    "vercel.json",
}


def build_release_risk_report(
    root: Path,
    readiness: ReleaseReadinessInput,
    *,
    changed_files: tuple[str, ...] = (),
) -> ReleaseRiskReport:
    """Build a read-only release readiness risk report from local artifacts."""

    root = root.resolve()
    artifact_root = root / "repos" / readiness.repo_id
    normalized_changed_files = tuple(sorted({Path(path).as_posix().strip("/") for path in changed_files if path}))
    warnings: list[ReleaseRiskWarning] = []

    entry = _repo_entry(root, readiness.repo_id)
    items: list[ReleaseRiskItem] = []
    items.extend(_changed_file_items(normalized_changed_files))
    items.extend(_release_evidence_items(readiness, normalized_changed_files))
    items.extend(_pack_verification_items(root, artifact_root, entry, warnings))
    items.extend(_blocking_unknown_items(root, artifact_root, warnings))
    items.extend(_eval_evidence_items(root, artifact_root, warnings))

    pr_warnings: list[PrRiskWarning] = []
    evidence = _command_evidence(root, artifact_root, readiness.repo_id, readiness.repo_path, pr_warnings)
    validation_suggestions = _filter_validation_suggestions(evidence, pr_warnings)
    warnings.extend(_release_warnings(pr_warnings))
    if not _has_repo_local_validation(validation_suggestions):
        warnings.append(
            ReleaseRiskWarning(
                code="no-command-evidence",
                source="command-evidence",
                message=(
                    "no repo-local validation command evidence found; only built-in harness validation is suggested"
                ),
            )
        )

    eval_warnings: list[PrRiskWarning] = []
    eval_suggestions = _load_eval_suggestions(
        artifact_root,
        readiness.repo_id,
        normalized_changed_files,
        eval_warnings,
    )
    warnings.extend(_release_warnings(eval_warnings))

    if not items:
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.LOW,
                category="release-context",
                reasons=("release context contains no elevated deterministic risk signals",),
                evidence=normalized_changed_files or ("no changed files provided",),
            )
        )

    sorted_items = tuple(sorted(items, key=lambda item: (-_RISK_ORDER[item.level], item.category, item.evidence)))
    return ReleaseRiskReport(
        repo_id=readiness.repo_id,
        repo_path=readiness.repo_path,
        version=readiness.version,
        base=readiness.base,
        head=readiness.head,
        changed_files=normalized_changed_files,
        overall_risk=_overall_risk(sorted_items),
        items=sorted_items,
        validation_suggestions=validation_suggestions,
        eval_suggestions=eval_suggestions,
        warnings=tuple(sorted(set(warnings), key=lambda warning: (warning.source, warning.code, warning.message))),
    )


def _repo_entry(root: Path, repo_id: str) -> RepoEntry | None:
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == repo_id:
            return entry
    return None


def _changed_file_items(changed_files: tuple[str, ...]) -> tuple[ReleaseRiskItem, ...]:
    items: list[ReleaseRiskItem] = []
    for path in changed_files:
        release_item = _release_specific_file_item(path)
        if release_item is not None:
            items.append(release_item)
            continue
        file_risk = _classify_file(path)
        if file_risk.level == RiskLevel.LOW:
            continue
        items.append(
            ReleaseRiskItem(
                level=file_risk.level,
                category=file_risk.category,
                reasons=file_risk.reasons,
                evidence=(file_risk.path,),
            )
        )
    return tuple(items)


def _release_specific_file_item(path: str) -> ReleaseRiskItem | None:
    lower = Path(path).as_posix().strip("/").lower()
    parts = tuple(Path(lower).parts)
    name = parts[-1] if parts else lower
    if _is_migration_path(parts, name):
        return ReleaseRiskItem(
            level=RiskLevel.HIGH,
            category="migration",
            reasons=("changes database migration or schema files",),
            evidence=(path,),
        )
    if _is_deployment_path(parts, name):
        return ReleaseRiskItem(
            level=RiskLevel.HIGH,
            category="deployment",
            reasons=("changes deployment or infrastructure configuration",),
            evidence=(path,),
        )
    return None


def _is_migration_path(parts: tuple[str, ...], name: str) -> bool:
    if any(part in _MIGRATION_PARTS for part in parts):
        return True
    return name.endswith(".sql") and any(part in {"db", "database", "sql"} for part in parts)


def _is_deployment_path(parts: tuple[str, ...], name: str) -> bool:
    if name in _DEPLOYMENT_NAMES or name.endswith((".tf", ".tfvars")):
        return True
    return any(part in _DEPLOYMENT_PARTS for part in parts)


def _release_evidence_items(
    readiness: ReleaseReadinessInput,
    changed_files: tuple[str, ...],
) -> tuple[ReleaseRiskItem, ...]:
    items: list[ReleaseRiskItem] = []
    if readiness.version is None:
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.MEDIUM,
                category="version-evidence",
                reasons=("release version was not provided",),
                evidence=("version=-",),
            )
        )
    if changed_files and not any(_is_changelog_or_version_path(path) for path in changed_files):
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.MEDIUM,
                category="changelog-evidence",
                reasons=("changed files do not include changelog or version evidence",),
                evidence=changed_files,
            )
        )
    if not changed_files:
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.MEDIUM,
                category="change-evidence",
                reasons=("no release changed-file evidence was provided",),
                evidence=("changed_files=empty",),
            )
        )
    return tuple(items)


def _is_changelog_or_version_path(path: str) -> bool:
    normalized = Path(path).as_posix().strip("/").lower()
    parts = tuple(Path(normalized).parts)
    name = parts[-1] if parts else normalized
    stem = Path(name).stem
    return (
        stem in _CHANGELOG_NAMES or name in _VERSION_FILES or "changelog" in normalized or "release-note" in normalized
    )


def _pack_verification_items(
    root: Path,
    artifact_root: Path,
    entry: RepoEntry | None,
    warnings: list[ReleaseRiskWarning],
) -> tuple[ReleaseRiskItem, ...]:
    items: list[ReleaseRiskItem] = []
    status = entry.coverage_status if entry is not None else "missing"
    if status != "verified":
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="pack-verification",
                reasons=("repo pack is not verified by eval replay",),
                evidence=(f"coverage_status={status}",),
            )
        )

    approval_path = artifact_root / "approval.yml"
    approval = _load_json_artifact(approval_path, root, "approval metadata", warnings)
    if not isinstance(approval, dict):
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="approval-metadata",
                reasons=("approval metadata is absent or malformed",),
                evidence=(_display_path(approval_path, root),),
            )
        )
        return tuple(items)

    approval_reasons: list[str] = []
    if approval.get("decision") != "approved":
        approval_reasons.append("approval decision is not approved")
    if approval.get("status") != status:
        approval_reasons.append("approval status does not match registry status")
    if status == "verified" and approval.get("verified") is not True:
        approval_reasons.append("verified registry entry lacks verified approval metadata")
    if entry is not None and approval.get("pack_ref") != entry.pack_ref:
        approval_reasons.append("approval pack_ref does not match registry pack_ref")
    if status == "verified" and not isinstance(approval.get("verification"), dict):
        approval_reasons.append("verified approval metadata lacks verification details")
    stale_artifacts = _stale_protected_artifacts(root, approval)
    if stale_artifacts:
        approval_reasons.append("protected approval artifact hashes are stale or missing")

    if approval_reasons:
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="approval-metadata",
                reasons=tuple(sorted(approval_reasons)),
                evidence=tuple(sorted(stale_artifacts)) or (_display_path(approval_path, root),),
            )
        )
    return tuple(items)


def _blocking_unknown_items(
    root: Path,
    artifact_root: Path,
    warnings: list[ReleaseRiskWarning],
) -> tuple[ReleaseRiskItem, ...]:
    unknowns_path = artifact_root / "unknowns.yml"
    artifact = _load_json_artifact(unknowns_path, root, "unknowns", warnings)
    if not isinstance(artifact, dict):
        return (
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="blocking-unknowns",
                reasons=("unknown coverage metadata is absent or malformed",),
                evidence=(_display_path(unknowns_path, root),),
            ),
        )
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        return (
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="blocking-unknowns",
                reasons=("unknown coverage metadata does not contain an unknowns list",),
                evidence=(_display_path(unknowns_path, root),),
            ),
        )
    blocking = tuple(
        sorted(
            str(unknown.get("id") or "unknown")
            for unknown in unknowns
            if isinstance(unknown, dict) and unknown.get("severity") == "blocking" and unknown.get("status") == "open"
        )
    )
    if not blocking:
        return ()
    return (
        ReleaseRiskItem(
            level=RiskLevel.HIGH,
            category="blocking-unknowns",
            reasons=("blocking unknowns remain open",),
            evidence=blocking,
        ),
    )


def _eval_evidence_items(
    root: Path,
    artifact_root: Path,
    warnings: list[ReleaseRiskWarning],
) -> tuple[ReleaseRiskItem, ...]:
    items: list[ReleaseRiskItem] = []
    evals_path = artifact_root / "evals" / "onboarding.yml"
    evals = _load_json_artifact(evals_path, root, "onboarding evals", warnings)
    if not isinstance(evals, dict) or not isinstance(evals.get("tasks"), list) or not evals.get("tasks"):
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="eval-evidence",
                reasons=("onboarding eval evidence is absent, malformed, or empty",),
                evidence=(_display_path(evals_path, root),),
            )
        )

    report_path = artifact_root / "eval-report.yml"
    report = _load_json_artifact(report_path, root, "eval report", warnings)
    if not isinstance(report, dict) or report.get("status") != "verified":
        items.append(
            ReleaseRiskItem(
                level=RiskLevel.HIGH,
                category="eval-evidence",
                reasons=("latest eval report is absent, malformed, or not verified",),
                evidence=(_display_path(report_path, root),),
            )
        )
    return tuple(items)


def _stale_protected_artifacts(root: Path, approval: dict[str, object]) -> tuple[str, ...]:
    protected = approval.get("protected_artifacts")
    if not isinstance(protected, list):
        return ("approval.yml:protected_artifacts",)
    stale: list[str] = []
    for item in protected:
        if not isinstance(item, dict):
            stale.append("approval.yml:protected_artifacts")
            continue
        path = item.get("path")
        expected_hash = item.get("sha256")
        if not isinstance(path, str) or not path.strip() or not isinstance(expected_hash, str):
            stale.append("approval.yml:protected_artifacts")
            continue
        artifact_path = root / path
        if not artifact_path.is_file() or _sha256(artifact_path) != expected_hash:
            stale.append(path)
    return tuple(sorted(set(stale)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_repo_local_validation(suggestions: tuple[ValidationSuggestion, ...]) -> bool:
    return any(not suggestion.command.startswith("harness validate ") for suggestion in suggestions)


def _overall_risk(items: tuple[ReleaseRiskItem, ...]) -> RiskLevel:
    if not items:
        return RiskLevel.LOW
    return max((item.level for item in items), key=lambda level: _RISK_ORDER[level])


def _release_warnings(warnings: list[PrRiskWarning]) -> tuple[ReleaseRiskWarning, ...]:
    converted: list[ReleaseRiskWarning] = []
    for warning in warnings:
        converted.append(ReleaseRiskWarning(code=warning.code, source=warning.source, message=warning.message))
    return tuple(converted)


def _load_json_artifact(
    path: Path,
    root: Path,
    label: str,
    warnings: list[ReleaseRiskWarning],
) -> object | None:
    if not path.is_file():
        warnings.append(
            ReleaseRiskWarning(
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
            ReleaseRiskWarning(
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
