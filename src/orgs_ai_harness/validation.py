"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


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
    lines = config_text.splitlines()

    if "org:" not in lines:
        errors.append("harness.yml missing required field: org (add top-level 'org:' mapping)")

    name = _read_scalar(lines, "  name:")
    if name is None or not name.strip():
        errors.append("harness.yml missing required field: org.name (set org.name to a non-empty name)")
    elif not _is_valid_org_name(name):
        errors.append(
            "harness.yml field org.name is invalid "
            "(use letters, numbers, dots, underscores, or hyphens; do not use slashes)"
        )

    skills_version = _read_scalar(lines, "  skills_version:")
    if skills_version != "1":
        errors.append("harness.yml field org.skills_version must be 1 (set 'skills_version: 1')")

    for field in ("providers: []", "repos: []", "command_permissions: []"):
        if field not in lines:
            field_name = field.removesuffix(": []")
            errors.append(f"harness.yml missing required field: {field_name} (add '{field}')")

    if "redaction:" not in lines:
        errors.append("harness.yml missing required field: redaction (add top-level 'redaction:' mapping)")
    if "  globs: []" not in lines:
        errors.append("harness.yml missing required field: redaction.globs (add '  globs: []')")
    if "  regexes: []" not in lines:
        errors.append("harness.yml missing required field: redaction.regexes (add '  regexes: []')")

    return errors


def _read_scalar(lines: list[str], prefix: str) -> str | None:
    for line in lines:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def _is_valid_org_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None
