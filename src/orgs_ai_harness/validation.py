"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
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
