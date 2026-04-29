"""Repository discovery providers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from orgs_ai_harness.repo_registry import (
    RepoEntry,
    RepoRegistryError,
    add_repo_entries,
    derive_repo_id_from_url,
)


class RepoDiscoveryError(Exception):
    """Raised when repository discovery cannot be completed."""


@dataclass(frozen=True)
class DiscoveredRepo:
    id: str
    name: str
    owner: str | None
    url: str
    default_branch: str | None
    visibility: str | None
    archived: bool
    fork: bool
    description: str | None


def discover_github_org(org: str) -> tuple[DiscoveredRepo, ...]:
    """Discover repositories visible to `gh` for a GitHub organization."""

    target = org.strip()
    if not target:
        raise RepoDiscoveryError("GitHub org cannot be empty")
    return _run_gh_repo_list(target)


def discover_github_user(user: str) -> tuple[DiscoveredRepo, ...]:
    """Discover repositories visible to `gh` for a GitHub user profile."""

    target = user.strip()
    if not target:
        raise RepoDiscoveryError("GitHub user cannot be empty")
    return _run_gh_repo_list(target)


def infer_github_owner(source: str) -> str:
    """Infer a GitHub owner/org login from a pasted profile URL or bare owner."""

    raw_source = source.strip()
    if not raw_source:
        raise RepoDiscoveryError("GitHub profile source cannot be empty")

    if "://" not in raw_source and "/" not in raw_source and raw_source != "github.com":
        return raw_source.lstrip("@")

    candidate = raw_source if "://" in raw_source else f"https://{raw_source}"
    parsed = urlparse(candidate)
    host = parsed.netloc.lower()
    if host != "github.com" and not host.endswith(".github.com"):
        raise RepoDiscoveryError(f"GitHub profile source must be a github.com URL or owner: {source}")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        raise RepoDiscoveryError(
            "GitHub profile source must point to an owner or org profile, for example https://github.com/acme"
        )
    return parts[0].lstrip("@")


def filter_discovered_repos(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    include_archived: bool = False,
    include_forks: bool = False,
) -> tuple[DiscoveredRepo, ...]:
    """Apply default discovery filters before selection."""

    filtered: list[DiscoveredRepo] = []
    for repo in discovered:
        if repo.archived and not include_archived:
            continue
        if repo.fork and not include_forks:
            continue
        filtered.append(repo)
    return tuple(filtered)


def select_discovered_repos(
    discovered: tuple[DiscoveredRepo, ...],
    selection_value: str,
    *,
    filtered_out: tuple[DiscoveredRepo, ...] = (),
) -> tuple[DiscoveredRepo, ...]:
    """Select discovered repos by comma-separated id or name."""

    requested = tuple(part.strip() for part in selection_value.split(",") if part.strip())
    if not requested:
        raise RepoDiscoveryError("--select must include at least one repo id or name")

    by_key: dict[str, DiscoveredRepo] = {}
    for repo in discovered:
        by_key[repo.id] = repo
        by_key[repo.name] = repo

    selected: list[DiscoveredRepo] = []
    missing: list[str] = []
    seen: set[str] = set()
    for key in requested:
        repo = by_key.get(key)
        if repo is None:
            missing.append(key)
            continue
        if repo.id not in seen:
            selected.append(repo)
            seen.add(repo.id)

    if missing:
        filtered_keys = {repo.id for repo in filtered_out} | {repo.name for repo in filtered_out}
        filtered_missing = [key for key in missing if key in filtered_keys]
        if filtered_missing:
            missing_list = ", ".join(filtered_missing)
            raise RepoDiscoveryError(
                "selected repo(s) were filtered out by default: "
                f"{missing_list}. Use --include-archived or --include-forks when appropriate."
            )
        missing_list = ", ".join(missing)
        raise RepoDiscoveryError(f"selected repo(s) not found in discovery results: {missing_list}")

    return tuple(selected)


def select_discovered_repos_interactively(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> tuple[DiscoveredRepo, ...]:
    """Prompt a terminal user to select discovered repositories."""

    if not discovered:
        raise RepoDiscoveryError("no repositories are available to select after discovery filters")

    if input_stream.isatty() and output_stream.isatty():
        return _select_discovered_repos_with_checkboxes(
            discovered,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    return _select_discovered_repos_by_line(
        discovered,
        input_stream=input_stream,
        output_stream=output_stream,
    )


def _select_discovered_repos_by_line(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> tuple[DiscoveredRepo, ...]:
    print("Discovered repositories:", file=output_stream)
    for index, repo in enumerate(discovered, start=1):
        suffix = _repo_details_suffix(repo)
        print(f"  {index}. {repo.name}{suffix}", file=output_stream)
    print(
        "Select repositories by number or name, comma-separated. Use 'all' for every listed repo.", file=output_stream
    )
    print("Selection: ", end="", file=output_stream)
    output_stream.flush()

    selection = input_stream.readline().strip()
    if not selection:
        raise RepoDiscoveryError("no repositories selected")
    if selection.lower() == "all":
        return discovered

    selected: list[DiscoveredRepo] = []
    seen: set[str] = set()
    missing: list[str] = []
    by_name = {repo.name: repo for repo in discovered} | {repo.id: repo for repo in discovered}
    for raw_part in selection.split(","):
        part = raw_part.strip()
        if not part:
            continue
        repo: DiscoveredRepo | None = None
        if part.isdigit():
            index = int(part)
            if 1 <= index <= len(discovered):
                repo = discovered[index - 1]
        else:
            repo = by_name.get(part)
        if repo is None:
            missing.append(part)
            continue
        if repo.id not in seen:
            selected.append(repo)
            seen.add(repo.id)
    if missing:
        raise RepoDiscoveryError(f"selected repo(s) not found in discovery results: {', '.join(missing)}")
    if not selected:
        raise RepoDiscoveryError("no repositories selected")
    return tuple(selected)


def _select_discovered_repos_with_checkboxes(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> tuple[DiscoveredRepo, ...]:
    try:
        import termios
        import tty
    except ImportError:
        return _select_discovered_repos_by_line(
            discovered,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    try:
        input_fd = input_stream.fileno()
        original_terminal = termios.tcgetattr(input_fd)
    except (AttributeError, OSError, termios.error):
        return _select_discovered_repos_by_line(
            discovered,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    try:
        tty.setcbreak(input_fd)
        return _run_checkbox_selector(
            discovered,
            read_key=lambda: _read_terminal_selection_key(input_stream),
            output_stream=output_stream,
        )
    finally:
        termios.tcsetattr(input_fd, termios.TCSADRAIN, original_terminal)
        output_stream.write("\x1b[?25h\x1b[2J\x1b[H")
        output_stream.flush()


def _run_checkbox_selector(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    read_key: Callable[[], str],
    output_stream: TextIO,
    terminal_lines: int | None = None,
) -> tuple[DiscoveredRepo, ...]:
    cursor = 0
    selected_indexes: set[int] = set()
    message = ""

    while True:
        _render_checkbox_selector(
            discovered,
            cursor=cursor,
            selected_indexes=selected_indexes,
            output_stream=output_stream,
            message=message,
            terminal_lines=terminal_lines,
        )
        message = ""
        key = read_key()
        if key in {"up", "k"}:
            cursor = max(0, cursor - 1)
        elif key in {"down", "j"}:
            cursor = min(len(discovered) - 1, cursor + 1)
        elif key == "toggle":
            if cursor in selected_indexes:
                selected_indexes.remove(cursor)
            else:
                selected_indexes.add(cursor)
        elif key == "all":
            if len(selected_indexes) == len(discovered):
                selected_indexes.clear()
            else:
                selected_indexes = set(range(len(discovered)))
        elif key == "enter":
            if selected_indexes:
                return tuple(discovered[index] for index in sorted(selected_indexes))
            message = "Select at least one repository before confirming."
        elif key == "quit":
            raise RepoDiscoveryError("repository selection cancelled")


def _render_checkbox_selector(
    discovered: tuple[DiscoveredRepo, ...],
    *,
    cursor: int,
    selected_indexes: set[int],
    output_stream: TextIO,
    message: str,
    terminal_lines: int | None,
) -> None:
    if terminal_lines is None:
        terminal_lines = shutil.get_terminal_size((80, 24)).lines
    visible_count = max(5, terminal_lines - 8)
    viewport_start = min(
        max(0, cursor - visible_count + 1),
        max(0, len(discovered) - visible_count),
    )
    viewport_end = min(len(discovered), viewport_start + visible_count)

    output_stream.write("\x1b[?25l\x1b[2J\x1b[H")
    output_stream.write("Select repositories\n")
    output_stream.write("Use Up/Down or j/k to move, Space to toggle, a for all, Enter to confirm, q to cancel.\n")
    output_stream.write(f"Selected: {len(selected_indexes)} of {len(discovered)}\n")
    if message:
        output_stream.write(f"{message}\n")
    else:
        output_stream.write("\n")

    if viewport_start > 0:
        output_stream.write(f"  ... {viewport_start} more above\n")
    for index in range(viewport_start, viewport_end):
        repo = discovered[index]
        pointer = ">" if index == cursor else " "
        checked = "x" if index in selected_indexes else " "
        suffix = _repo_details_suffix(repo)
        output_stream.write(f"{pointer} [{checked}] {index + 1}. {repo.name}{suffix}\n")
    remaining = len(discovered) - viewport_end
    if remaining > 0:
        output_stream.write(f"  ... {remaining} more below\n")
    output_stream.flush()


def _read_terminal_selection_key(input_stream: TextIO) -> str:
    char = input_stream.read(1)
    if char == "\x1b":
        sequence = input_stream.read(2)
        if sequence == "[A":
            return "up"
        if sequence == "[B":
            return "down"
        return "unknown"
    if char in {"\r", "\n"}:
        return "enter"
    if char == " ":
        return "toggle"
    if char == "\x03" or char == "q":
        return "quit"
    if char in {"a", "j", "k"}:
        return char
    return "unknown"


def _repo_details_suffix(repo: DiscoveredRepo) -> str:
    details = []
    if repo.visibility:
        details.append(repo.visibility.lower())
    if repo.default_branch:
        details.append(f"default={repo.default_branch}")
    return f" ({', '.join(details)})" if details else ""


def clone_discovered_repos(
    root: Path,
    cwd: Path,
    selected: tuple[DiscoveredRepo, ...],
    clone_dir: str | None,
) -> dict[str, str]:
    """Clone selected repos and return registry-ready local paths keyed by repo id."""

    if shutil.which("git") is None:
        raise RepoDiscoveryError("git is required for --clone but was not found on PATH")

    clone_root = _resolve_clone_root(cwd, clone_dir)
    local_paths: dict[str, str] = {}
    for repo in selected:
        destination = (clone_root / repo.id).resolve()
        if destination.exists():
            print(
                f"warning: clone destination already exists for {repo.id}; "
                f"skipping clone and using existing directory: {destination}",
            )
            if destination.is_dir():
                local_paths[repo.id] = _relative_path(destination, root.resolve())
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Bandit: fixed git argv with shell=False.
        result = subprocess.run(  # nosec B603 B607
            ["git", "clone", repo.url, str(destination)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown git failure"
            raise RepoDiscoveryError(f"git clone failed for {repo.id}: {message}")
        if not destination.is_dir():
            raise RepoDiscoveryError(f"git clone did not create expected directory for {repo.id}: {destination}")
        local_paths[repo.id] = _relative_path(destination, root.resolve())
    return local_paths


def register_discovered_repos(
    root: Path,
    selected: tuple[DiscoveredRepo, ...],
    *,
    local_paths: dict[str, str] | None = None,
) -> tuple[RepoEntry, ...]:
    """Write selected discovered repos to the existing repo registry."""

    paths = local_paths or {}
    entries = tuple(_repo_entry_from_discovered(repo, local_path=paths.get(repo.id)) for repo in selected)
    try:
        return add_repo_entries(root, entries)
    except RepoRegistryError as exc:
        raise RepoDiscoveryError(str(exc)) from exc


def _run_gh_repo_list(target: str) -> tuple[DiscoveredRepo, ...]:
    if shutil.which("gh") is None:
        raise RepoDiscoveryError("GitHub CLI 'gh' is required. Install it and run 'gh auth login'.")

    command = [
        "gh",
        "repo",
        "list",
        target,
        "--limit",
        "1000",
        "--json",
        "name,owner,url,defaultBranchRef,visibility,isArchived,isFork,description",
    ]
    try:
        # Bandit: fixed gh argv shape with shell=False.
        result = subprocess.run(  # nosec B603
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RepoDiscoveryError(f"failed to run gh: {exc}") from exc

    if result.returncode != 0:
        raise RepoDiscoveryError(_format_gh_failure(result.stdout, result.stderr))

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RepoDiscoveryError("gh repo discovery returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RepoDiscoveryError("gh repo discovery returned unexpected JSON")

    return tuple(_discovered_repo_from_gh(record) for record in payload)


def _discovered_repo_from_gh(record: object) -> DiscoveredRepo:
    if not isinstance(record, dict):
        raise RepoDiscoveryError("gh repo discovery returned a non-object repo record")

    name = _required_string(record, "name")
    url = _required_string(record, "url")
    return DiscoveredRepo(
        id=derive_repo_id_from_url(url),
        name=name,
        owner=_owner_login(record.get("owner")),
        url=url,
        default_branch=_default_branch_name(record.get("defaultBranchRef")),
        visibility=_optional_string(record, "visibility"),
        archived=_bool_field(record, "isArchived"),
        fork=_bool_field(record, "isFork"),
        description=_optional_string(record, "description"),
    )


def _format_gh_failure(stdout: str, stderr: str) -> str:
    output = (stderr.strip() or stdout.strip() or "unknown gh failure").strip()
    lowered = output.lower()
    if "gh auth login" in lowered or "not logged" in lowered or "authentication" in lowered:
        return "GitHub CLI 'gh' is not authenticated. Run 'gh auth login' before discovery."

    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "unknown gh failure")
    return f"gh repo discovery failed: {first_line}"


def _repo_entry_from_discovered(repo: DiscoveredRepo, *, local_path: str | None = None) -> RepoEntry:
    return RepoEntry(
        id=repo.id,
        name=repo.name,
        owner=repo.owner,
        purpose=None,
        url=repo.url,
        default_branch=repo.default_branch,
        local_path=local_path,
        coverage_status="selected",
        active=True,
        deactivation_reason=None,
        pack_ref=None,
        external=False,
    )


def _resolve_clone_root(cwd: Path, clone_dir: str | None) -> Path:
    raw_value = clone_dir.strip() if clone_dir is not None else "covered-repos"
    if not raw_value:
        raise RepoDiscoveryError("--clone-dir cannot be empty")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = cwd.resolve() / path
    return path.resolve()


def _relative_path(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def _required_string(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RepoDiscoveryError(f"gh repo record missing required string field: {field}")
    return value


def _optional_string(record: dict[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepoDiscoveryError(f"gh repo record field {field} must be a string or null")
    normalized = value.strip()
    return normalized or None


def _bool_field(record: dict[str, object], field: str) -> bool:
    value = record.get(field)
    if not isinstance(value, bool):
        raise RepoDiscoveryError(f"gh repo record field {field} must be a boolean")
    return value


def _owner_login(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RepoDiscoveryError("gh repo record field owner must be an object or null")
    login = value.get("login")
    if login is None:
        return None
    if not isinstance(login, str):
        raise RepoDiscoveryError("gh repo record field owner.login must be a string")
    normalized = login.strip()
    return normalized or None


def _default_branch_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RepoDiscoveryError("gh repo record field defaultBranchRef must be an object or null")
    name = value.get("name")
    if name is None:
        return None
    if not isinstance(name, str):
        raise RepoDiscoveryError("gh repo record field defaultBranchRef.name must be a string")
    normalized = name.strip()
    return normalized or None
