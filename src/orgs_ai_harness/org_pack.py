"""Org skill pack filesystem management."""

from __future__ import annotations

from pathlib import Path

from orgs_ai_harness.config import render_default_harness_config


class OrgPackError(Exception):
    """Raised when org pack operations cannot be completed."""


DEFAULT_PACK_DIR = "org-agent-skills"
DEFAULT_SKILLS_VERSION = 1
ATTACHMENT_FILE = ".orgs-ai-harness-attachment"
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
    attachment = cwd / ATTACHMENT_FILE
    if attachment.is_file():
        target = attachment.read_text(encoding="utf-8").strip()
        if _looks_like_remote_url(target):
            raise OrgPackError(
                "attached org pack is a remote URL and is not available locally. "
                "Clone it first, then run 'harness org init --repo <local-path>'."
            )
        return Path(target).expanduser().resolve()

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


def attach_org_pack(cwd: Path, repo: str) -> Path | None:
    """Attach the current working directory to an existing org pack."""

    cwd = cwd.resolve()
    target = repo.strip()
    if not target:
        raise OrgPackError("repo path or URL cannot be empty")

    if _looks_like_remote_url(target):
        _write_attachment(cwd, target)
        return None

    root = Path(target).expanduser()
    if not root.is_absolute():
        root = (cwd / root).resolve()
    else:
        root = root.resolve()

    if not root.exists():
        raise OrgPackError(f"org pack path does not exist: {root}")
    if not root.is_dir():
        raise OrgPackError(f"org pack path is not a directory: {root}")

    from orgs_ai_harness.validation import validate_org_pack

    result = validate_org_pack(root)
    if not result.ok:
        formatted = "; ".join(result.errors)
        raise OrgPackError(f"invalid org pack at {root}: {formatted}")

    _write_attachment(cwd, str(root))
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


def _write_attachment(cwd: Path, target: str) -> None:
    (cwd / ATTACHMENT_FILE).write_text(f"{target}\n", encoding="utf-8")


def _looks_like_remote_url(value: str) -> bool:
    return (
        value.startswith("git@")
        or value.startswith("ssh://")
        or value.startswith("https://")
        or value.startswith("http://")
    )


def render_harness_config(org_name: str) -> str:
    """Render the minimum supported harness configuration."""

    normalized_name = org_name.strip()
    if not normalized_name:
        raise OrgPackError("org name cannot be empty")

    return render_default_harness_config(normalized_name)
