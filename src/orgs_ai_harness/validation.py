"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from orgs_ai_harness.config import block_has_field, read_block_scalar, split_top_level_blocks


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

    redaction_block = blocks.get("redaction")
    if redaction_block is None:
        errors.append("harness.yml missing required field: redaction (add top-level 'redaction:' mapping)")
    if redaction_block is None or not block_has_field(redaction_block, "globs"):
        errors.append("harness.yml missing required field: redaction.globs (add '  globs: []')")
    if redaction_block is None or not block_has_field(redaction_block, "regexes"):
        errors.append("harness.yml missing required field: redaction.regexes (add '  regexes: []')")

    return errors


def _is_valid_org_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None
