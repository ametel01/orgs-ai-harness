"""Org skill pack filesystem management."""

from __future__ import annotations

from pathlib import Path


class OrgPackError(Exception):
    """Raised when org pack operations cannot be completed."""


DEFAULT_PACK_DIR = "org-agent-skills"
DEFAULT_SKILLS_VERSION = 1
PROTECTED_INIT_PATHS = (
    "harness.yml",
    "org",
    "repos",
    "proposals",
    "trace-summaries",
)


def resolve_default_root(cwd: Path) -> Path:
    """Resolve the org pack root for commands run from common locations."""

    cwd = cwd.resolve()
    if (cwd / "harness.yml").exists():
        return cwd

    child = cwd / DEFAULT_PACK_DIR
    if (child / "harness.yml").exists():
        return child

    return cwd


def default_init_root(cwd: Path) -> Path:
    """Choose the default init target for the current working directory."""

    cwd = cwd.resolve()
    if cwd.name == DEFAULT_PACK_DIR:
        return cwd
    return cwd / DEFAULT_PACK_DIR


def init_org_pack(cwd: Path, org_name: str) -> Path:
    """Create a minimal org skill pack skeleton."""

    root = default_init_root(cwd)
    _ensure_can_initialize(root)
    root.mkdir(parents=True, exist_ok=True)

    (root / "org" / "skills").mkdir(parents=True, exist_ok=True)
    (root / "repos").mkdir(parents=True, exist_ok=True)
    (root / "proposals").mkdir(parents=True, exist_ok=True)
    (root / "trace-summaries").mkdir(parents=True, exist_ok=True)
    (root / "org" / "resolvers.yml").write_text("rules: []\n", encoding="utf-8")
    (root / "harness.yml").write_text(render_harness_config(org_name), encoding="utf-8")

    return root


def _ensure_can_initialize(root: Path) -> None:
    existing = [relative for relative in PROTECTED_INIT_PATHS if (root / relative).exists()]
    if not existing:
        return

    existing_list = ", ".join(existing)
    raise OrgPackError(
        "refusing to initialize over existing org pack artifacts "
        f"at {root}: {existing_list}. "
        "Use 'harness org init --repo <path>' to attach an existing pack, "
        "repair the directory manually, or run init from a different directory."
    )


def render_harness_config(org_name: str) -> str:
    """Render the minimum supported harness configuration."""

    normalized_name = org_name.strip()
    if not normalized_name:
        raise OrgPackError("org name cannot be empty")

    return (
        "org:\n"
        f"  name: {normalized_name}\n"
        f"  skills_version: {DEFAULT_SKILLS_VERSION}\n"
        "\n"
        "providers: []\n"
        "repos: []\n"
        "redaction:\n"
        "  globs: []\n"
        "  regexes: []\n"
        "command_permissions: []\n"
    )
