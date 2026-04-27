"""Repo registry mutation and rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import json
import os
import re

from orgs_ai_harness.config import ConfigBlock, HarnessConfig, load_harness_config, save_harness_config


class RepoRegistryError(Exception):
    """Raised when repo registry operations cannot be completed."""


@dataclass(frozen=True)
class RepoEntry:
    id: str
    name: str
    owner: str | None
    purpose: str | None
    url: str | None
    default_branch: str | None
    local_path: str | None
    coverage_status: str
    active: bool
    deactivation_reason: str | None
    pack_ref: str | None
    external: bool


def add_repo(
    root: Path,
    cwd: Path,
    path_or_url: str,
    *,
    purpose: str | None = None,
    owner: str | None = None,
    default_branch: str | None = "main",
    external: bool = False,
) -> RepoEntry:
    """Register a local or remote repository in the org pack."""

    raw_value = path_or_url.strip()
    if not raw_value:
        raise RepoRegistryError("repo path or URL cannot be empty")

    root = root.resolve()
    entries = load_repo_entries(root / "harness.yml")

    if looks_like_remote_url(raw_value):
        repo_name = derive_repo_name_from_url(raw_value)
        repo_id = derive_repo_id_from_url(raw_value)
        _ensure_unique_repo_id(entries, repo_id)
        entry = RepoEntry(
            id=repo_id,
            name=repo_name,
            owner=_normalize_optional(owner),
            purpose=_normalize_optional(purpose),
            url=raw_value,
            default_branch=_normalize_optional(default_branch),
            local_path=None,
            coverage_status=_initial_coverage_status(external),
            active=not external,
            deactivation_reason=None,
            pack_ref=None,
            external=external,
        )
        save_repo_entries(root / "harness.yml", (*entries, entry))
        return entry

    repo_path = _resolve_user_path(cwd, raw_value)
    if not repo_path.exists():
        raise RepoRegistryError(f"repo path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise RepoRegistryError(f"repo path is not a directory: {repo_path}")

    repo_id = derive_repo_id_from_path(repo_path)
    _ensure_unique_repo_id(entries, repo_id)

    entry = RepoEntry(
        id=repo_id,
        name=repo_path.name,
        owner=_normalize_optional(owner),
        purpose=_normalize_optional(purpose),
        url=None,
        default_branch=_normalize_optional(default_branch),
        local_path=_relative_path(repo_path, root),
        coverage_status=_initial_coverage_status(external),
        active=not external,
        deactivation_reason=None,
        pack_ref=None,
        external=external,
    )
    save_repo_entries(root / "harness.yml", (*entries, entry))
    return entry


def load_repo_entries(config_path: Path) -> tuple[RepoEntry, ...]:
    config = load_harness_config(config_path)
    repos_block = next((block for block in config.blocks if block.key == "repos"), None)
    if repos_block is None:
        return ()
    return parse_repo_block(repos_block)


def set_repo_path(root: Path, cwd: Path, repo_id: str, path_value: str) -> RepoEntry:
    """Update the local path for one registered repo."""

    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise RepoRegistryError("repo id cannot be empty")

    raw_path = path_value.strip()
    if not raw_path:
        raise RepoRegistryError("repo path cannot be empty")

    root = root.resolve()
    repo_path = _resolve_user_path(cwd, raw_path)
    if not repo_path.exists():
        raise RepoRegistryError(f"repo path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise RepoRegistryError(f"repo path is not a directory: {repo_path}")

    entries = load_repo_entries(root / "harness.yml")
    updated_entries: list[RepoEntry] = []
    updated_entry: RepoEntry | None = None
    for entry in entries:
        if entry.id == normalized_repo_id:
            updated_entry = replace(entry, local_path=_relative_path(repo_path, root))
            updated_entries.append(updated_entry)
        else:
            updated_entries.append(entry)

    if updated_entry is None:
        raise RepoRegistryError(f"repo id is not registered: {normalized_repo_id}")

    save_repo_entries(root / "harness.yml", tuple(updated_entries))
    return updated_entry


def deactivate_repo(root: Path, repo_id: str, reason: str) -> RepoEntry:
    """Mark a registered repo inactive while preserving its registry record."""

    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise RepoRegistryError("repo id cannot be empty")

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise RepoRegistryError("deactivation reason cannot be empty")

    root = root.resolve()
    entries = load_repo_entries(root / "harness.yml")
    updated_entries: list[RepoEntry] = []
    updated_entry: RepoEntry | None = None
    for entry in entries:
        if entry.id == normalized_repo_id:
            updated_entry = replace(
                entry,
                coverage_status="deactivated",
                active=False,
                deactivation_reason=normalized_reason,
            )
            updated_entries.append(updated_entry)
        else:
            updated_entries.append(entry)

    if updated_entry is None:
        raise RepoRegistryError(f"repo id is not registered: {normalized_repo_id}")

    save_repo_entries(root / "harness.yml", tuple(updated_entries))
    return updated_entry


def remove_repo(root: Path, repo_id: str, reason: str, *, force: bool = False) -> RepoEntry:
    """Remove a registry entry without touching repository contents."""

    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise RepoRegistryError("repo id cannot be empty")

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise RepoRegistryError("removal reason cannot be empty")

    root = root.resolve()
    entries = load_repo_entries(root / "harness.yml")
    remaining_entries: list[RepoEntry] = []
    removed_entry: RepoEntry | None = None
    for entry in entries:
        if entry.id == normalized_repo_id:
            removed_entry = entry
            continue
        remaining_entries.append(entry)

    if removed_entry is None:
        raise RepoRegistryError(f"repo id is not registered: {normalized_repo_id}")
    if removed_entry.pack_ref is not None and not force:
        raise RepoRegistryError(
            f"repo {normalized_repo_id} has onboarding metadata and requires --force to remove"
        )

    save_repo_entries(root / "harness.yml", tuple(remaining_entries))
    return removed_entry


def save_repo_entries(config_path: Path, entries: tuple[RepoEntry, ...]) -> None:
    config = load_harness_config(config_path)
    save_harness_config(config_path, replace_config_block(config, render_repo_block(entries)))


def replace_config_block(config: HarnessConfig, replacement: ConfigBlock) -> HarnessConfig:
    blocks: list[ConfigBlock] = []
    replaced = False
    for block in config.blocks:
        if block.key == replacement.key:
            blocks.append(replacement)
            replaced = True
        else:
            blocks.append(block)
    if not replaced:
        blocks.append(replacement)
    return HarnessConfig(config.org_name, config.skills_version, tuple(blocks))


def parse_repo_block(block: ConfigBlock) -> tuple[RepoEntry, ...]:
    if len(block.lines) == 1 and block.lines[0].strip() == "repos: []":
        return ()
    if block.lines[0].strip() != "repos:":
        raise RepoRegistryError("harness.yml field repos must be a list")

    records: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for line in block.lines[1:]:
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is not None:
                records.append(current)
            current = {}
            remainder = line.removeprefix("  - ").strip()
            if remainder:
                key, value = _parse_field_line(remainder, line)
                current[key] = value
            continue
        if line.startswith("    "):
            if current is None:
                raise RepoRegistryError("harness.yml field repos contains a field before any list item")
            key, value = _parse_field_line(line.strip(), line)
            current[key] = value
            continue
        raise RepoRegistryError(f"harness.yml field repos has invalid indentation: {line!r}")

    if current is not None:
        records.append(current)

    return tuple(_entry_from_record(record) for record in records)


def render_repo_block(entries: tuple[RepoEntry, ...]) -> ConfigBlock:
    if not entries:
        return ConfigBlock("repos", ("repos: []",))

    lines = ["repos:"]
    fields = (
        "id",
        "name",
        "owner",
        "purpose",
        "url",
        "default_branch",
        "local_path",
        "coverage_status",
        "active",
        "deactivation_reason",
        "pack_ref",
        "external",
    )
    for entry in entries:
        values = entry.__dict__
        first_field = fields[0]
        lines.append(f"  - {first_field}: {_render_scalar(values[first_field])}")
        for field in fields[1:]:
            lines.append(f"    {field}: {_render_scalar(values[field])}")
    return ConfigBlock("repos", tuple(lines))


def derive_repo_id_from_path(path: Path) -> str:
    name = path.name.removesuffix(".git")
    return _normalize_repo_id(name)


def derive_repo_id_from_url(url: str) -> str:
    return _normalize_repo_id(derive_repo_name_from_url(url))


def derive_repo_name_from_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise RepoRegistryError("repo URL cannot be empty")

    if cleaned.startswith("git@") and ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[1]
    else:
        cleaned = cleaned.rsplit("/", 1)[-1]

    name = cleaned.rsplit("/", 1)[-1].removesuffix(".git")
    if not name:
        raise RepoRegistryError(f"cannot derive repo name from URL: {url}")
    return name


def looks_like_remote_url(value: str) -> bool:
    return (
        value.startswith("git@")
        or value.startswith("ssh://")
        or value.startswith("https://")
        or value.startswith("http://")
    )


def _entry_from_record(record: dict[str, object]) -> RepoEntry:
    required = ("id", "name", "coverage_status", "active", "external")
    missing = [field for field in required if field not in record]
    if missing:
        missing_list = ", ".join(missing)
        raise RepoRegistryError(f"harness.yml repo entry missing required field(s): {missing_list}")

    active = record["active"]
    if not isinstance(active, bool):
        raise RepoRegistryError("harness.yml repo entry field active must be true or false")
    external = record["external"]
    if not isinstance(external, bool):
        raise RepoRegistryError("harness.yml repo entry field external must be true or false")

    return RepoEntry(
        id=_required_string(record, "id"),
        name=_required_string(record, "name"),
        owner=_optional_string(record, "owner"),
        purpose=_optional_string(record, "purpose"),
        url=_optional_string(record, "url"),
        default_branch=_optional_string(record, "default_branch"),
        local_path=_optional_string(record, "local_path"),
        coverage_status=_required_string(record, "coverage_status"),
        active=active,
        deactivation_reason=_optional_string(record, "deactivation_reason"),
        pack_ref=_optional_string(record, "pack_ref"),
        external=external,
    )


def _required_string(record: dict[str, object], field: str) -> str:
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        raise RepoRegistryError(f"harness.yml repo entry field {field} must be a non-empty string")
    return value


def _optional_string(record: dict[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepoRegistryError(f"harness.yml repo entry field {field} must be a string or null")
    return value


def _parse_field_line(content: str, original_line: str) -> tuple[str, object]:
    if ":" not in content:
        raise RepoRegistryError(f"harness.yml repo field is missing ':': {original_line!r}")
    key, raw_value = content.split(":", 1)
    key = key.strip()
    if not key:
        raise RepoRegistryError(f"harness.yml repo field has an empty key: {original_line!r}")
    return key, _parse_scalar(raw_value.strip())


def _parse_scalar(raw_value: str) -> object:
    if raw_value in ("null", "~", ""):
        return None
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if raw_value.startswith('"') and raw_value.endswith('"'):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise RepoRegistryError(f"harness.yml repo string is invalid JSON-style YAML: {raw_value}") from exc
    if raw_value.startswith("'") and raw_value.endswith("'"):
        return raw_value[1:-1]
    return raw_value


def _render_scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if not isinstance(value, str):
        raise TypeError(f"unsupported repo scalar value: {value!r}")
    return json.dumps(value)


def _normalize_repo_id(name: str) -> str:
    repo_id = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip().lower()).strip("-._")
    if not repo_id:
        raise RepoRegistryError(f"cannot derive repo id from name: {name!r}")
    return repo_id


def _ensure_unique_repo_id(entries: tuple[RepoEntry, ...], repo_id: str) -> None:
    for entry in entries:
        if entry.id == repo_id:
            location = entry.local_path or entry.url or entry.name
            raise RepoRegistryError(f"repo id already registered: {repo_id} is owned by {location}")


def _resolve_user_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd.resolve() / path
    return path.resolve()


def _relative_path(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _initial_coverage_status(external: bool) -> str:
    if external:
        return "external"
    return "selected"
