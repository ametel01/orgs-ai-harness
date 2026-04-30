"""Deterministic dependency campaign risk classification and rollout planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orgs_ai_harness.dependency_context import DependencyInventory, DependencyRepoInventory
from orgs_ai_harness.pr_risk import (
    EvalSuggestion,
    PrRiskWarning,
    RiskLevel,
    ValidationSuggestion,
    _command_evidence,
    _filter_validation_suggestions,
    _load_eval_suggestions,
)


@dataclass(frozen=True)
class DependencyRiskItem:
    level: RiskLevel
    category: str
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class DependencyRiskWarning:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class DependencyRepoRisk:
    repo_id: str
    overall_risk: RiskLevel
    items: tuple[DependencyRiskItem, ...]
    validation_suggestions: tuple[ValidationSuggestion, ...]
    eval_suggestions: tuple[EvalSuggestion, ...]
    warnings: tuple[DependencyRiskWarning, ...]


@dataclass(frozen=True)
class DependencyRolloutStep:
    position: int
    repo_id: str
    risk: RiskLevel
    suggested_commands: tuple[str, ...]
    suggested_evals: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DependencyRiskReport:
    campaign_name: str
    overall_risk: RiskLevel
    repos: tuple[DependencyRepoRisk, ...]
    rollout_plan: tuple[DependencyRolloutStep, ...]
    warnings: tuple[DependencyRiskWarning, ...]


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}
_MIGRATION_PARTS = {"migration", "migrations", "schema"}
_DEPLOYMENT_PARTS = {"deploy", "deployment", "docker", "infra", "infrastructure", "k8s", "terraform"}
_DEPLOYMENT_NAMES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "vercel.json", "fly.toml"}


def build_dependency_risk_report(root: Path, inventory: DependencyInventory) -> DependencyRiskReport:
    """Classify dependency campaign risk without executing commands."""

    root = root.resolve()
    repo_reports = tuple(
        sorted((_repo_risk(root, repo_inventory) for repo_inventory in inventory.repos), key=lambda item: item.repo_id)
    )
    rollout_plan = _rollout_plan(repo_reports)
    warnings = tuple(
        sorted(
            {warning for report in repo_reports for warning in report.warnings},
            key=lambda item: (item.source, item.code, item.message),
        )
    )
    return DependencyRiskReport(
        campaign_name=inventory.campaign_name,
        overall_risk=_overall_risk(tuple(item.overall_risk for item in repo_reports)),
        repos=repo_reports,
        rollout_plan=rollout_plan,
        warnings=warnings,
    )


def _repo_risk(root: Path, inventory: DependencyRepoInventory) -> DependencyRepoRisk:
    artifact_root = root / "repos" / inventory.repo_id
    items: list[DependencyRiskItem] = []
    warnings: list[DependencyRiskWarning] = []

    dependency_paths = tuple(item.path for item in inventory.dependency_files)
    items.extend(_dependency_file_items(inventory))
    items.extend(_missing_evidence_items(inventory))
    items.extend(_coupling_items(inventory.repo_path))
    items.extend(_generated_pack_items(inventory))

    pr_warnings: list[PrRiskWarning] = []
    validation_suggestions = _filter_validation_suggestions(
        _command_evidence(root, artifact_root, inventory.repo_id, inventory.repo_path, pr_warnings),
        pr_warnings,
    )
    warnings.extend(_risk_warnings(pr_warnings))
    if not _has_repo_local_validation(validation_suggestions):
        warnings.append(
            DependencyRiskWarning(
                code="no-command-evidence",
                source=f"{inventory.repo_id}:command-evidence",
                message=(
                    "no repo-local validation command evidence found; only built-in harness validation is suggested"
                ),
            )
        )
        items.append(
            DependencyRiskItem(
                level=RiskLevel.MEDIUM,
                category="validation-evidence",
                reasons=("no repo-local validation command evidence found",),
                evidence=("harness validate only",),
            )
        )

    eval_warnings: list[PrRiskWarning] = []
    eval_suggestions = _load_eval_suggestions(artifact_root, inventory.repo_id, dependency_paths, eval_warnings)
    warnings.extend(_risk_warnings(eval_warnings))
    if not eval_suggestions:
        items.append(
            DependencyRiskItem(
                level=RiskLevel.MEDIUM,
                category="eval-evidence",
                reasons=("no onboarding eval evidence overlaps dependency manifests",),
                evidence=dependency_paths or ("dependency_files=empty",),
            )
        )

    if not items:
        items.append(
            DependencyRiskItem(
                level=RiskLevel.LOW,
                category="dependency-context",
                reasons=("dependency inventory contains no elevated deterministic risk signals",),
                evidence=("inventory",),
            )
        )

    sorted_items = tuple(sorted(items, key=lambda item: (-_RISK_ORDER[item.level], item.category, item.evidence)))
    return DependencyRepoRisk(
        repo_id=inventory.repo_id,
        overall_risk=_overall_risk(tuple(item.level for item in sorted_items)),
        items=sorted_items,
        validation_suggestions=validation_suggestions,
        eval_suggestions=eval_suggestions,
        warnings=tuple(sorted(set(warnings), key=lambda item: (item.source, item.code, item.message))),
    )


def _dependency_file_items(inventory: DependencyRepoInventory) -> tuple[DependencyRiskItem, ...]:
    items: list[DependencyRiskItem] = []
    for dependency_file in inventory.dependency_files:
        if dependency_file.status == "malformed":
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.HIGH,
                    category="dependency-manifest",
                    reasons=("dependency manifest is malformed and cannot be planned safely",),
                    evidence=(dependency_file.path,),
                )
            )
        else:
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.LOW,
                    category="dependency-manifest",
                    reasons=("campaign includes dependency manifest evidence",),
                    evidence=(dependency_file.path,),
                )
            )
    return tuple(items)


def _missing_evidence_items(inventory: DependencyRepoInventory) -> tuple[DependencyRiskItem, ...]:
    items: list[DependencyRiskItem] = []
    for missing in inventory.missing_evidence:
        if missing.kind == "lockfile":
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.MEDIUM,
                    category="lockfile-evidence",
                    reasons=("dependency manifest has no recognized lockfile evidence",),
                    evidence=(missing.path,),
                )
            )
        elif missing.kind == "artifact" and "approval" in missing.path:
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.HIGH,
                    category="approval-metadata",
                    reasons=("approval metadata is absent or malformed",),
                    evidence=(missing.path,),
                )
            )
        elif missing.kind == "artifact" and "evals/onboarding.yml" in missing.path:
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.MEDIUM,
                    category="eval-evidence",
                    reasons=("onboarding eval evidence is absent or malformed",),
                    evidence=(missing.path,),
                )
            )
        elif missing.kind == "generated-pack":
            items.append(
                DependencyRiskItem(
                    level=RiskLevel.HIGH,
                    category="pack-verification",
                    reasons=("repo pack is not verified by eval replay",),
                    evidence=(f"{missing.path}: {missing.reason}",),
                )
            )
    return tuple(items)


def _coupling_items(repo_path: Path) -> tuple[DependencyRiskItem, ...]:
    items = []
    if _has_path(repo_path, _MIGRATION_PARTS, (".sql",)):
        items.append(
            DependencyRiskItem(
                level=RiskLevel.HIGH,
                category="migration-coupling",
                reasons=(
                    "repo contains migration or schema paths that can couple dependency upgrades to data changes",
                ),
                evidence=("migration/schema paths",),
            )
        )
    if _has_deployment_evidence(repo_path):
        items.append(
            DependencyRiskItem(
                level=RiskLevel.MEDIUM,
                category="deployment-coupling",
                reasons=("repo contains deployment or infrastructure paths relevant to dependency rollout",),
                evidence=("deployment/infrastructure paths",),
            )
        )
    if (repo_path / ".github" / "workflows").is_dir():
        items.append(
            DependencyRiskItem(
                level=RiskLevel.MEDIUM,
                category="ci-workflow",
                reasons=("repo contains CI workflows that should be checked before dependency rollout",),
                evidence=(".github/workflows",),
            )
        )
    return tuple(items)


def _generated_pack_items(inventory: DependencyRepoInventory) -> tuple[DependencyRiskItem, ...]:
    items = []
    pack = inventory.generated_pack
    if pack.approval_status is not None and pack.approval_status != inventory.lifecycle_status:
        items.append(
            DependencyRiskItem(
                level=RiskLevel.HIGH,
                category="approval-metadata",
                reasons=("approval status does not match registry lifecycle status",),
                evidence=(f"approval={pack.approval_status}, registry={inventory.lifecycle_status}",),
            )
        )
    if pack.coverage_status == "verified" and pack.approval_verified is not True:
        items.append(
            DependencyRiskItem(
                level=RiskLevel.HIGH,
                category="approval-metadata",
                reasons=("verified registry entry lacks verified approval metadata",),
                evidence=("approval.verified=false",),
            )
        )
    return tuple(items)


def _has_repo_local_validation(suggestions: tuple[ValidationSuggestion, ...]) -> bool:
    return any(not suggestion.command.startswith("harness validate ") for suggestion in suggestions)


def _risk_warnings(warnings: list[PrRiskWarning]) -> tuple[DependencyRiskWarning, ...]:
    return tuple(DependencyRiskWarning(warning.code, warning.source, warning.message) for warning in warnings)


def _rollout_plan(repo_reports: tuple[DependencyRepoRisk, ...]) -> tuple[DependencyRolloutStep, ...]:
    ordered = sorted(repo_reports, key=lambda item: (_RISK_ORDER[item.overall_risk], item.repo_id))
    return tuple(
        DependencyRolloutStep(
            position=index,
            repo_id=report.repo_id,
            risk=report.overall_risk,
            suggested_commands=tuple(suggestion.command for suggestion in report.validation_suggestions),
            suggested_evals=tuple(suggestion.eval_id for suggestion in report.eval_suggestions),
            reasons=_rollout_reasons(report),
        )
        for index, report in enumerate(ordered, start=1)
    )


def _rollout_reasons(report: DependencyRepoRisk) -> tuple[str, ...]:
    categories = tuple(dict.fromkeys(item.category for item in report.items if item.level == report.overall_risk))
    return categories or ("dependency-context",)


def _overall_risk(levels: tuple[RiskLevel, ...]) -> RiskLevel:
    if not levels:
        return RiskLevel.LOW
    return max(levels, key=lambda level: _RISK_ORDER[level])


def _has_path(repo_path: Path, path_parts: set[str], suffixes: tuple[str, ...]) -> bool:
    for path in repo_path.rglob("*"):
        if not path.exists():
            continue
        normalized_parts = {part.lower() for part in path.relative_to(repo_path).parts}
        if normalized_parts & path_parts:
            return True
        if path.is_file() and path.name.lower().endswith(suffixes):
            return True
    return False


def _has_deployment_evidence(repo_path: Path) -> bool:
    for path in repo_path.rglob("*"):
        relative = path.relative_to(repo_path)
        parts = {part.lower() for part in relative.parts}
        name = path.name.lower()
        if name in _DEPLOYMENT_NAMES or parts & _DEPLOYMENT_PARTS:
            return True
    return False
