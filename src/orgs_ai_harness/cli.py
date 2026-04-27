"""Command line interface for the Org Skill Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orgs_ai_harness.org_pack import (
    OrgPackError,
    attach_org_pack,
    init_org_pack,
    resolve_default_root,
)
from orgs_ai_harness.repo_registry import (
    RepoEntry,
    RepoRegistryError,
    add_repo,
    deactivate_repo,
    load_repo_entries,
    remove_repo,
    set_repo_path,
)
from orgs_ai_harness.repo_discovery import (
    RepoDiscoveryError,
    clone_discovered_repos,
    discover_github_org,
    discover_github_user,
    filter_discovered_repos,
    register_discovered_repos,
    select_discovered_repos,
)
from orgs_ai_harness.validation import validate_org_pack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    org_parser = subparsers.add_parser("org", help="Manage org skill packs")
    org_subparsers = org_parser.add_subparsers(dest="org_command", required=True)
    org_init = org_subparsers.add_parser("init", help="Initialize an org skill pack")
    init_source = org_init.add_mutually_exclusive_group(required=True)
    init_source.add_argument("--name", help="Organization name for the skill pack")
    init_source.add_argument("--repo", help="Existing org skill pack path or Git URL")

    repo_parser = subparsers.add_parser("repo", help="Manage covered repositories")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=True)
    repo_add = repo_subparsers.add_parser("add", help="Register a repository")
    repo_add.add_argument("path_or_url", help="Local repository path or remote Git URL")
    repo_add.add_argument("--purpose", help="Why this repository is covered")
    repo_add.add_argument("--owner", help="Owning team or person")
    repo_add.add_argument("--default-branch", default="main", help="Default branch name")
    repo_add.add_argument("--external", action="store_true", help="Mark as an external dependency reference")
    repo_discover = repo_subparsers.add_parser("discover", help="Discover repositories from a provider")
    repo_discover.add_argument("--github-org", help="GitHub organization to discover with gh")
    repo_discover.add_argument("--github-user", help="GitHub user profile to discover with gh")
    repo_discover.add_argument("--select", help="Comma-separated discovered repo ids or names to register")
    repo_discover.add_argument("--include-archived", action="store_true", help="Include archived repositories")
    repo_discover.add_argument("--include-forks", action="store_true", help="Include fork repositories")
    repo_discover.add_argument("--clone", action="store_true", help="Clone selected repositories")
    repo_discover.add_argument("--clone-dir", help="Directory where selected repositories should be cloned")
    repo_set_path = repo_subparsers.add_parser("set-path", help="Repair a registered local repository path")
    repo_set_path.add_argument("repo_id", help="Registered repo id")
    repo_set_path.add_argument("path", help="New local repository path")
    repo_deactivate = repo_subparsers.add_parser("deactivate", help="Deactivate a registered repository")
    repo_deactivate.add_argument("repo_id", help="Registered repo id")
    repo_deactivate.add_argument("--reason", required=True, help="Reason for deactivation")
    repo_remove = repo_subparsers.add_parser("remove", help="Remove a repository registry entry")
    repo_remove.add_argument("repo_id", help="Registered repo id")
    repo_remove.add_argument("--reason", required=True, help="Reason for removal")
    repo_remove.add_argument("--force", action="store_true", help="Remove even when onboarding metadata exists")
    repo_subparsers.add_parser("list", help="List registered repositories")

    subparsers.add_parser("validate", help="Validate the org skill pack")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "org" and args.org_command == "init":
            if args.name is not None:
                root = init_org_pack(Path.cwd(), args.name)
                print(f"Initialized org skill pack at {root}")
                return 0

            root = attach_org_pack(Path.cwd(), args.repo)
            if root is None:
                print(
                    "Recorded remote org skill pack attachment. "
                    "No clone, push, or hosted setup was performed."
                )
                return 0

            result = validate_org_pack(root)
            if not result.ok:
                for error in result.errors:
                    print(f"error: {error}", file=sys.stderr)
                return 1

            print(f"Attached org skill pack at {root}")
            return 0

        if args.command == "validate":
            root = resolve_default_root(Path.cwd())
            result = validate_org_pack(root)
            if result.ok:
                print(f"Validation passed for {root}")
                return 0
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 1

        if args.command == "repo":
            root = resolve_default_root(Path.cwd())
            if args.repo_command == "add":
                entry = add_repo(
                    root,
                    Path.cwd(),
                    args.path_or_url,
                    purpose=args.purpose,
                    owner=args.owner,
                    default_branch=args.default_branch,
                    external=args.external,
                )
                print(f"Registered repo {entry.id} at {_repo_location(entry)}")
                return 0

            if args.repo_command == "discover":
                if args.github_org is not None and args.github_user is not None:
                    raise RepoDiscoveryError("repo discover accepts only one of --github-org or --github-user")
                if args.github_org is None and args.github_user is None:
                    raise RepoDiscoveryError("repo discover requires --github-org or --github-user")
                if args.select is None:
                    raise RepoDiscoveryError("repo discover requires --select in non-interactive use")
                if args.github_org is not None:
                    discovered = discover_github_org(args.github_org)
                else:
                    discovered = discover_github_user(args.github_user)
                filtered = filter_discovered_repos(
                    discovered,
                    include_archived=args.include_archived,
                    include_forks=args.include_forks,
                )
                filtered_out = tuple(repo for repo in discovered if repo not in filtered)
                selected = select_discovered_repos(filtered, args.select, filtered_out=filtered_out)
                local_paths = None
                if args.clone:
                    local_paths = clone_discovered_repos(root, Path.cwd(), selected, args.clone_dir)
                entries = register_discovered_repos(root, selected, local_paths=local_paths)
                for entry in entries:
                    print(f"Registered repo {entry.id} at {_repo_location(entry)}")
                return 0

            if args.repo_command == "set-path":
                entry = set_repo_path(root, Path.cwd(), args.repo_id, args.path)
                print(f"Updated repo {entry.id} path to {entry.local_path}")
                return 0

            if args.repo_command == "deactivate":
                entry = deactivate_repo(root, args.repo_id, args.reason)
                print(f"Deactivated repo {entry.id}: {entry.deactivation_reason}")
                return 0

            if args.repo_command == "remove":
                entry = remove_repo(root, args.repo_id, args.reason, force=args.force)
                print(f"Removed repo {entry.id} from registry: {args.reason.strip()}")
                return 0

            if args.repo_command == "list":
                entries = load_repo_entries(root / "harness.yml")
                if not entries:
                    print("No repositories registered.")
                    return 0
                for entry in entries:
                    print(
                        f"{entry.id}\t{_repo_location(entry)}\t"
                        f"active={str(entry.active).lower()}\tstatus={entry.coverage_status}"
                    )
                return 0

    except (OrgPackError, RepoRegistryError, RepoDiscoveryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error("unsupported command")
    return 2


def _repo_location(entry: RepoEntry) -> str:
    return entry.local_path or entry.url or "-"
