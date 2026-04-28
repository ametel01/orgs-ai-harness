"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from orgs_ai_harness.config import block_has_field, read_block_scalar, split_top_level_blocks
from orgs_ai_harness.repo_registry import RepoRegistryError, parse_repo_block


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_org_pack(root: Path) -> ValidationResult:
    """Validate the minimum Sprint 01 org pack contract."""

    root = root.resolve()
    errors: list[str] = []

    if not root.exists():
        return ValidationResult((f"org pack root does not exist: {root}",))
    if not root.is_dir():
        return ValidationResult((f"org pack root is not a directory: {root}",))

    required_dirs = [
        "org",
        "org/skills",
        "repos",
        "proposals",
        "trace-summaries",
    ]
    required_files = [
        "harness.yml",
        "org/resolvers.yml",
    ]

    for relative in required_dirs:
        path = root / relative
        if not path.is_dir():
            errors.append(
                f"missing required directory: {relative} "
                f"(create {relative}/ or rerun 'harness org init --name <name>' in a clean directory)"
            )

    for relative in required_files:
        path = root / relative
        if not path.is_file():
            errors.append(
                f"missing required file: {relative} "
                f"(restore {relative} or rerun 'harness org init --name <name>' in a clean directory)"
            )

    config_path = root / "harness.yml"
    if config_path.is_file():
        errors.extend(_validate_minimum_config(config_path.read_text(encoding="utf-8")))

    return ValidationResult(tuple(errors))


def validate_repo_onboarding(root: Path, repo_id: str) -> ValidationResult:
    """Validate the minimum scan-only artifact contract for one repo."""

    root = root.resolve()
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        return ValidationResult(("repo id cannot be empty",))

    errors: list[str] = list(validate_org_pack(root).errors)
    artifact_root = root / "repos" / normalized_repo_id
    summary_path = artifact_root / "onboarding-summary.md"
    unknowns_path = artifact_root / "unknowns.yml"
    manifest_path = artifact_root / "scan" / "scan-manifest.yml"
    hypothesis_map_path = artifact_root / "scan" / "hypothesis-map.yml"

    if not summary_path.is_file():
        errors.append(f"missing onboarding summary: {summary_path.relative_to(root)}")
    elif not summary_path.read_text(encoding="utf-8").strip():
        errors.append(f"onboarding summary is empty: {summary_path.relative_to(root)}")

    unknowns = _load_json_artifact(unknowns_path, "unknowns", errors, root)
    if isinstance(unknowns, dict):
        _validate_unknowns_artifact(unknowns, unknowns_path, root, errors)

    manifest = _load_json_artifact(manifest_path, "scan manifest", errors, root)
    if isinstance(manifest, dict):
        _validate_scan_manifest_artifact(manifest, manifest_path, root, errors)

    hypothesis_map = _load_json_artifact(hypothesis_map_path, "hypothesis map", errors, root)
    if isinstance(hypothesis_map, dict):
        _validate_hypothesis_map_artifact(hypothesis_map, hypothesis_map_path, root, errors)

    return ValidationResult(tuple(errors))


def _validate_minimum_config(config_text: str) -> list[str]:
    errors: list[str] = []
    blocks = {block.key: block for block in split_top_level_blocks(config_text)}
    org_block = blocks.get("org")

    if org_block is None:
        errors.append("harness.yml missing required field: org (add top-level 'org:' mapping)")

    name = read_block_scalar(org_block, "name") if org_block is not None else None
    if name is None or not name.strip():
        errors.append("harness.yml missing required field: org.name (set org.name to a non-empty name)")
    elif not _is_valid_org_name(name):
        errors.append(
            "harness.yml field org.name is invalid "
            "(use letters, numbers, dots, underscores, or hyphens; do not use slashes)"
        )

    skills_version = read_block_scalar(org_block, "skills_version") if org_block is not None else None
    if skills_version != "1":
        errors.append("harness.yml field org.skills_version must be 1 (set 'skills_version: 1')")

    for field_name in ("providers", "repos", "command_permissions"):
        if field_name not in blocks:
            errors.append(f"harness.yml missing required field: {field_name} (add '{field_name}: []')")

    repos_block = blocks.get("repos")
    if repos_block is not None:
        try:
            entries = parse_repo_block(repos_block)
        except RepoRegistryError as exc:
            errors.append(str(exc))
        else:
            seen_repo_ids: set[str] = set()
            for entry in entries:
                if entry.id in seen_repo_ids:
                    errors.append(f"harness.yml contains duplicate repo id: {entry.id}")
                seen_repo_ids.add(entry.id)
                errors.extend(
                    _validate_repo_entry(
                        entry.id,
                        entry.coverage_status,
                        entry.active,
                        entry.local_path,
                        entry.deactivation_reason,
                        entry.external,
                    )
                )

    redaction_block = blocks.get("redaction")
    if redaction_block is None:
        errors.append("harness.yml missing required field: redaction (add top-level 'redaction:' mapping)")
    if redaction_block is None or not block_has_field(redaction_block, "globs"):
        errors.append("harness.yml missing required field: redaction.globs (add '  globs: []')")
    if redaction_block is None or not block_has_field(redaction_block, "regexes"):
        errors.append("harness.yml missing required field: redaction.regexes (add '  regexes: []')")

    return errors


def _load_json_artifact(path: Path, label: str, errors: list[str], root: Path) -> object | None:
    if not path.is_file():
        errors.append(f"missing {label}: {path.relative_to(root)}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{label} is malformed: {path.relative_to(root)} ({exc.msg})")
        return None


def _validate_unknowns_artifact(artifact: dict[str, object], path: Path, root: Path, errors: list[str]) -> None:
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        errors.append(f"unknowns.yml field unknowns must be a list: {path.relative_to(root)}")
        return
    for index, unknown in enumerate(unknowns, start=1):
        if not isinstance(unknown, dict):
            errors.append(f"unknowns.yml item {index} must be an object: {path.relative_to(root)}")
            continue
        for field in ("id", "question", "why_it_matters", "severity", "status", "recommended_investigation"):
            if not isinstance(unknown.get(field), str) or not str(unknown.get(field)).strip():
                errors.append(f"unknowns.yml item {index} field {field} must be a non-empty string")
        if unknown.get("severity") not in {"blocking", "important", "minor"}:
            errors.append(f"unknowns.yml item {index} has invalid severity: {unknown.get('severity')}")
        if unknown.get("status") not in {"open", "closed"}:
            errors.append(f"unknowns.yml item {index} has invalid status: {unknown.get('status')}")
        evidence = unknown.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"unknowns.yml item {index} field evidence must be a list")


def _validate_scan_manifest_artifact(artifact: dict[str, object], path: Path, root: Path, errors: list[str]) -> None:
    if not isinstance(artifact.get("repo_id"), str) or not str(artifact.get("repo_id")).strip():
        errors.append(f"scan manifest field repo_id must be a non-empty string: {path.relative_to(root)}")
    scanned_paths = artifact.get("scanned_paths")
    skipped_paths = artifact.get("skipped_paths")
    if not isinstance(scanned_paths, list):
        errors.append(f"scan manifest field scanned_paths must be a list: {path.relative_to(root)}")
    else:
        for index, record in enumerate(scanned_paths, start=1):
            _validate_path_record(record, f"scan manifest scanned_paths item {index}", require_reason=False, errors=errors)
    if not isinstance(skipped_paths, list):
        errors.append(f"scan manifest field skipped_paths must be a list: {path.relative_to(root)}")
    else:
        for index, record in enumerate(skipped_paths, start=1):
            _validate_path_record(record, f"scan manifest skipped_paths item {index}", require_reason=True, errors=errors)


def _validate_path_record(record: object, label: str, *, require_reason: bool, errors: list[str]) -> None:
    if not isinstance(record, dict):
        errors.append(f"{label} must be an object")
        return
    if not isinstance(record.get("path"), str) or not str(record.get("path")).strip():
        errors.append(f"{label} field path must be a non-empty string")
    if require_reason and (not isinstance(record.get("reason"), str) or not str(record.get("reason")).strip()):
        errors.append(f"{label} field reason must be a non-empty string")


def _validate_hypothesis_map_artifact(
    artifact: dict[str, object],
    path: Path,
    root: Path,
    errors: list[str],
) -> None:
    if not isinstance(artifact.get("repo_id"), str) or not str(artifact.get("repo_id")).strip():
        errors.append(f"hypothesis map field repo_id must be a non-empty string: {path.relative_to(root)}")
    seed_context = artifact.get("seed_context")
    if not isinstance(seed_context, dict):
        errors.append(f"hypothesis map field seed_context must be an object: {path.relative_to(root)}")
    evidence_categories = artifact.get("evidence_categories")
    if not isinstance(evidence_categories, dict):
        errors.append(f"hypothesis map field evidence_categories must be an object: {path.relative_to(root)}")
    hypotheses = artifact.get("hypotheses")
    if not isinstance(hypotheses, list):
        errors.append(f"hypothesis map field hypotheses must be a list: {path.relative_to(root)}")
        return
    for index, hypothesis in enumerate(hypotheses, start=1):
        if not isinstance(hypothesis, dict):
            errors.append(f"hypothesis map item {index} must be an object")
            continue
        if not isinstance(hypothesis.get("name"), str) or not str(hypothesis.get("name")).strip():
            errors.append(f"hypothesis map item {index} field name must be a non-empty string")
        if not isinstance(hypothesis.get("evidence_paths"), list):
            errors.append(f"hypothesis map item {index} field evidence_paths must be a list")
        if not isinstance(hypothesis.get("unknown"), bool):
            errors.append(f"hypothesis map item {index} field unknown must be true or false")


def _validate_repo_entry(
    repo_id: str,
    coverage_status: str,
    active: bool,
    local_path: str | None,
    deactivation_reason: str | None,
    external: bool,
) -> list[str]:
    errors: list[str] = []

    if not _is_valid_repo_id(repo_id):
        errors.append(
            f"harness.yml repo id is invalid: {repo_id} "
            "(use letters, numbers, dots, underscores, or hyphens)"
        )
    if coverage_status not in {"selected", "deactivated", "external"}:
        errors.append(
            f"harness.yml repo {repo_id} has invalid coverage_status: {coverage_status} "
            "(supported values: selected, deactivated, external)"
        )
    if coverage_status == "selected" and not active:
        errors.append(f"harness.yml repo {repo_id} with selected coverage must be active")
    if coverage_status == "selected" and external:
        errors.append(f"harness.yml repo {repo_id} cannot be both selected coverage and external")
    if coverage_status == "deactivated":
        if active:
            errors.append(f"harness.yml repo {repo_id} with deactivated coverage must be inactive")
        if deactivation_reason is None or not deactivation_reason.strip():
            errors.append(f"harness.yml repo {repo_id} with deactivated coverage must include deactivation_reason")
        if external:
            errors.append(f"harness.yml repo {repo_id} cannot be both deactivated coverage and external")
    if coverage_status == "external":
        if not external:
            errors.append(f"harness.yml repo {repo_id} with external coverage must set external: true")
        if active:
            errors.append(f"harness.yml repo {repo_id} with external coverage must be inactive")
    if local_path is not None and Path(local_path).is_absolute():
        errors.append(f"harness.yml repo {repo_id} local_path must be relative to the org pack root")

    return errors


def _is_valid_org_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None


def _is_valid_repo_id(repo_id: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo_id) is not None
