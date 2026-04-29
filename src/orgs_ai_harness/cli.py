"""Command line interface for the Org Skill Harness."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from orgs_ai_harness.approval import ApprovalError, approve_repo, reject_repo, render_approval_review
from orgs_ai_harness.cache_manager import CacheManagerError, export_cached_pack, refresh_cache
from orgs_ai_harness.eval_replay import EvalReplayError, run_eval
from orgs_ai_harness.explain import ExplainError, render_explain
from orgs_ai_harness.llm_runner import run_llm_command_with_progress
from orgs_ai_harness.org_pack import (
    OrgPackError,
    attach_org_pack,
    init_org_pack,
    resolve_default_root,
)
from orgs_ai_harness.proposals import (
    ProposalError,
    apply_proposal,
    improve_repo,
    list_proposals,
    refresh_repo,
    reject_proposal,
    render_proposal_show,
)
from orgs_ai_harness.repo_discovery import (
    DiscoveredRepo,
    RepoDiscoveryError,
    clone_discovered_repos,
    discover_github_org,
    discover_github_user,
    filter_discovered_repos,
    infer_github_owner,
    register_discovered_repos,
    select_discovered_repos,
    select_discovered_repos_interactively,
)
from orgs_ai_harness.repo_onboarding import RepoOnboardingError, onboard_repo, scan_repo_only
from orgs_ai_harness.repo_registry import (
    RepoEntry,
    RepoRegistryError,
    add_repo,
    deactivate_repo,
    load_repo_entries,
    remove_repo,
    set_repo_path,
)
from orgs_ai_harness.validation import validate_org_pack, validate_repo_onboarding

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORG_LEVEL_SKILL_PROMPT_PATH = PROJECT_ROOT / "local-docs" / "ORG_LEVEL_SKILL_BUILD.md"
DEFAULT_ORG_LEVEL_SKILL_PROMPT = """Create organization-level agent skills from the harness evidence.

Use the registered repositories, generated org artifacts, and resolver metadata
to create small, targeted skills that help agents choose the right repository
or workflow. Each skill must include valid frontmatter with a concrete name and
trigger-focused description.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Run the interactive first-run setup wizard")
    setup_parser.add_argument("source", nargs="?", help="GitHub org/profile URL, GitHub owner, or 'local'")
    setup_parser.add_argument("--include-archived", action="store_true", help="Include archived GitHub repositories")
    setup_parser.add_argument("--include-forks", action="store_true", help="Include fork GitHub repositories")
    setup_parser.add_argument("--llm", choices=("codex", "claude", "template"), help="Skill generator to use")
    setup_parser.add_argument(
        "--skill-target", choices=("codex", "claude", "both"), help="Where to install generated skills"
    )

    org_parser = subparsers.add_parser("org", help="Manage org skill packs")
    org_subparsers = org_parser.add_subparsers(dest="org_command", required=True)
    org_init = org_subparsers.add_parser("init", help="Initialize an org skill pack")
    init_source = org_init.add_mutually_exclusive_group(required=True)
    init_source.add_argument("--name", help="Organization name for the skill pack")
    init_source.add_argument("--repo", help="Existing org skill pack path or Git URL")
    init_source.add_argument("--github", help="GitHub org/profile URL or owner to infer the org pack name")

    repo_parser = subparsers.add_parser("repo", help="Manage covered repositories")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=True)
    repo_add = repo_subparsers.add_parser("add", help="Register a repository")
    repo_add.add_argument("path_or_url", help="Local repository path or remote Git URL")
    repo_add.add_argument("--purpose", help="Why this repository is covered")
    repo_add.add_argument("--owner", help="Owning team or person")
    repo_add.add_argument("--default-branch", default="main", help="Default branch name")
    repo_add.add_argument("--external", action="store_true", help="Mark as an external dependency reference")
    repo_discover = repo_subparsers.add_parser("discover", help="Discover repositories from a provider")
    repo_discover.add_argument("github_source", nargs="?", help="GitHub org/profile URL or owner")
    repo_discover.add_argument("--github-org", help="GitHub organization to discover with gh")
    repo_discover.add_argument("--github-user", help="GitHub user profile to discover with gh")
    repo_discover.add_argument("--select", help="Comma-separated discovered repo ids or names to register")
    repo_discover.add_argument("--include-archived", action="store_true", help="Include archived repositories")
    repo_discover.add_argument("--include-forks", action="store_true", help="Include fork repositories")
    repo_discover.add_argument("--clone", action="store_true", help="Clone selected repositories")
    repo_discover.add_argument("--clone-dir", help="Directory where selected repositories should be cloned")
    repo_discover.add_argument(
        "--llm", choices=("codex", "claude", "template"), help="Skill generator for interactive follow-up"
    )
    repo_discover.add_argument(
        "--skill-target", choices=("codex", "claude", "both"), help="Where to install generated skills"
    )
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

    onboard_parser = subparsers.add_parser("onboard", help="Run repository onboarding")
    onboard_parser.add_argument("repo_id", help="Registered repo id to onboard")
    onboard_parser.add_argument("--scan-only", action="store_true", help="Only scan and summarize the repository")
    onboard_parser.add_argument(
        "--llm",
        choices=("codex", "claude", "template"),
        default=os.environ.get("ORGS_AI_HARNESS_SKILL_GENERATOR", "codex"),
        help="Skill generator to use for project-specific skills",
    )
    onboard_parser.add_argument(
        "--skill-target",
        choices=("codex", "claude", "both"),
        default=os.environ.get("ORGS_AI_HARNESS_SKILL_TARGET", "codex"),
        help="Where to install generated repository skills",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate the org skill pack")
    validate_parser.add_argument("repo_id", nargs="?", help="Optional repo id for repo-specific artifacts")

    approve_parser = subparsers.add_parser("approve", help="Review or approve a generated draft pack")
    approve_parser.add_argument("repo_id", help="Registered repo id to approve")
    approve_parser.add_argument("--all", action="store_true", help="Approve every generated draft artifact")
    approve_parser.add_argument(
        "--exclude",
        action="append",
        help="Approve the draft pack while excluding one generated artifact or artifact directory",
    )
    approve_parser.add_argument("--rationale", help="Human rationale to record in the approval trace")

    reject_parser = subparsers.add_parser("reject", help="Reject a generated draft pack")
    reject_parser.add_argument("repo_id", help="Registered repo id to reject")
    reject_parser.add_argument("--reason", help="Human rejection reason to record in the approval trace")

    eval_parser = subparsers.add_parser("eval", help="Replay approved onboarding evals locally")
    eval_parser.add_argument("repo_id", help="Registered repo id to evaluate")
    eval_parser.add_argument("--adapter", default="fixture", help="Eval adapter to use: fixture or codex-local")
    eval_parser.add_argument(
        "--development",
        action="store_true",
        help="Allow draft/non-approved eval replay without producing verified status",
    )

    cache_parser = subparsers.add_parser("cache", help="Manage repo-local pinned caches")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_refresh = cache_subparsers.add_parser("refresh", help="Refresh a repo-local approved pack cache")
    cache_refresh.add_argument("repo_id", help="Registered repo id to refresh")

    export_parser = subparsers.add_parser("export", help="Export a cached pack for an agent runtime")
    export_parser.add_argument("target", help="Export target: generic or codex")
    export_parser.add_argument("repo_id", help="Registered repo id to export")
    export_parser.add_argument("--allow-draft", action="store_true", help="Allow exporting draft packs intentionally")
    export_parser.add_argument(
        "--development",
        action="store_true",
        help="Allow development-only exports for packs that need investigation",
    )

    explain_parser = subparsers.add_parser("explain", help="Explain harness state for one repository")
    explain_parser.add_argument("repo_id", help="Registered repo id to explain")

    improve_parser = subparsers.add_parser("improve", help="Create evidence-backed improvement proposals")
    improve_parser.add_argument("repo_id", help="Registered repo id to improve")

    refresh_parser = subparsers.add_parser("refresh", help="Propose updates after source changes")
    refresh_parser.add_argument("repo_id", help="Registered repo id to refresh")

    proposals_parser = subparsers.add_parser("proposals", help="Review generated proposals")
    proposals_subparsers = proposals_parser.add_subparsers(dest="proposals_command", required=True)
    proposals_subparsers.add_parser("list", help="List generated proposals")
    proposals_show = proposals_subparsers.add_parser("show", help="Show one generated proposal")
    proposals_show.add_argument("proposal_id", help="Proposal id to show")
    proposals_apply = proposals_subparsers.add_parser("apply", help="Apply one generated proposal")
    proposals_apply.add_argument("proposal_id", help="Proposal id to apply")
    proposals_apply.add_argument("--yes", action="store_true", help="Confirm proposal application")
    proposals_reject = proposals_subparsers.add_parser("reject", help="Reject one generated proposal")
    proposals_reject.add_argument("proposal_id", help="Proposal id to reject")
    proposals_reject.add_argument("--reason", required=True, help="Human rejection reason")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            return _run_setup_wizard(args, input_stream=sys.stdin, output_stream=sys.stdout)

        if args.command == "org" and args.org_command == "init":
            if args.name is not None:
                root = init_org_pack(Path.cwd(), args.name)
                print(f"Initialized org skill pack at {root}")
                return 0

            if args.github is not None:
                owner = infer_github_owner(args.github)
                root = init_org_pack(Path.cwd(), owner)
                print(f"Initialized org skill pack for GitHub owner {owner} at {root}")
                return 0

            root = attach_org_pack(Path.cwd(), args.repo)
            if root is None:
                print("Recorded remote org skill pack attachment. No clone, push, or hosted setup was performed.")
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
            if args.repo_id is None:
                result = validate_org_pack(root)
            else:
                result = validate_repo_onboarding(root, args.repo_id)
            if result.ok:
                if args.repo_id is None:
                    print(f"Validation passed for {root}")
                else:
                    print(f"Validation passed for {args.repo_id} at {root}")
                return 0
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 1

        if args.command == "onboard":
            root = resolve_default_root(Path.cwd())
            if args.scan_only:
                result = scan_repo_only(root, args.repo_id)
                print(f"Scanned repo {result.repo_id} into {result.artifact_root}")
                return 0
            result = onboard_repo(root, args.repo_id, skill_generator=args.llm, skill_target=args.skill_target)
            print(f"Generated draft pack for repo {result.repo_id} into {result.artifact_root}")
            return 0

        if args.command == "approve":
            root = resolve_default_root(Path.cwd())
            exclusions = tuple(args.exclude or ())
            if not args.all and not exclusions:
                print(render_approval_review(root, args.repo_id), end="")
                return 0
            result = approve_repo(root, args.repo_id, exclusions=exclusions, rationale=args.rationale)
            print(
                f"Approved {len(result.approved_artifacts)} artifact(s) for repo {result.repo_id}; "
                f"excluded={len(result.excluded_artifacts)}; status=approved-unverified"
            )
            return 0

        if args.command == "reject":
            root = resolve_default_root(Path.cwd())
            result = reject_repo(root, args.repo_id, rationale=args.reason)
            print(f"Rejected draft pack for repo {result.repo_id}; status=needs-investigation")
            return 0

        if args.command == "eval":
            root = resolve_default_root(Path.cwd())
            result = run_eval(root, args.repo_id, adapter_id=args.adapter, development=args.development)
            print(
                f"Evaluated repo {result.repo_id}; baseline_pass_rate={result.baseline_pass_rate:.2f}; "
                f"skill_pack_pass_rate={result.skill_pack_pass_rate:.2f}; "
                f"rediscovery_cost_delta={result.rediscovery_cost_delta:.2f}; status={result.status}; "
                f"report={result.report_path}"
            )
            return 0

        if args.command == "cache" and args.cache_command == "refresh":
            root = resolve_default_root(Path.cwd())
            result = refresh_cache(root, args.repo_id)
            print(f"Refreshed cache for {result.repo_id}; pack_ref={result.pack_ref}; cache={result.cache_root}")
            return 0

        if args.command == "export":
            root = resolve_default_root(Path.cwd())
            result = export_cached_pack(
                root,
                args.target,
                args.repo_id,
                allow_draft=args.allow_draft,
                development=args.development,
            )
            print(
                f"Exported {result.target} pack for {result.repo_id}; "
                f"status={result.status}; export={result.export_root}"
            )
            return 0

        if args.command == "explain":
            root = resolve_default_root(Path.cwd())
            print(render_explain(root, args.repo_id), end="")
            return 0

        if args.command == "improve":
            root = resolve_default_root(Path.cwd())
            result = improve_repo(root, args.repo_id)
            if result.proposal_id is None:
                print(f"No proposal for {result.repo_id}; {result.reason}.")
                return 0
            print(f"Created proposal {result.proposal_id} for {result.repo_id}: {result.proposal_root}")
            return 0

        if args.command == "refresh":
            root = resolve_default_root(Path.cwd())
            result = refresh_repo(root, args.repo_id)
            if result.proposal_id is None:
                print(f"No proposal for {result.repo_id}; {result.reason}.")
                return 0
            print(
                f"Created refresh proposal {result.proposal_id} for {result.repo_id}; "
                f"{result.previous_commit}..{result.current_commit}"
            )
            return 0

        if args.command == "proposals":
            root = resolve_default_root(Path.cwd())
            if args.proposals_command == "list":
                proposals = list_proposals(root)
                if not proposals:
                    print("No proposals.")
                    return 0
                for proposal in proposals:
                    print(
                        f"{proposal.proposal_id}\t{proposal.repo_id}\t"
                        f"status={proposal.status}\trisk={proposal.risk}\t{proposal.summary}"
                    )
                return 0
            if args.proposals_command == "show":
                print(render_proposal_show(root, args.proposal_id), end="")
                return 0
            if args.proposals_command == "apply":
                result = apply_proposal(root, args.proposal_id, approved=args.yes)
                print(
                    f"Applied proposal {result.proposal_id} for {result.repo_id}; "
                    f"changed={len(result.changed_artifacts)}"
                )
                return 0
            if args.proposals_command == "reject":
                result = reject_proposal(root, args.proposal_id, reason=args.reason)
                print(f"Rejected proposal {result.proposal_id} for {result.repo_id}; status={result.status}")
                return 0

        if args.command == "repo":
            if args.repo_command == "add":
                root = _resolve_existing_org_pack_root(Path.cwd())
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
                source_count = sum(item is not None for item in (args.github_source, args.github_org, args.github_user))
                if source_count > 1:
                    raise RepoDiscoveryError(
                        "repo discover accepts only one GitHub source: a URL, --github-org, or --github-user; "
                        "only one of --github-org or --github-user may be used"
                    )
                if source_count == 0:
                    raise RepoDiscoveryError(
                        "repo discover requires a GitHub profile URL, --github-org, or --github-user"
                    )
                if args.select is None and not sys.stdin.isatty():
                    raise RepoDiscoveryError("repo discover requires --select in non-interactive use")

                if args.github_source is not None:
                    target = infer_github_owner(args.github_source)
                    discovery_provider = discover_github_user
                elif args.github_org is not None:
                    target = args.github_org
                    discovery_provider = discover_github_org
                else:
                    target = args.github_user
                    discovery_provider = discover_github_user

                root, initialized = _resolve_or_init_org_pack_root(Path.cwd(), target)
                if initialized:
                    print(f"Initialized org skill pack for GitHub owner {target} at {root}")

                discovered = discovery_provider(target)

                filtered = filter_discovered_repos(
                    discovered,
                    include_archived=args.include_archived,
                    include_forks=args.include_forks,
                )
                filtered_out = tuple(repo for repo in discovered if repo not in filtered)
                if args.select is not None:
                    selected = select_discovered_repos(filtered, args.select, filtered_out=filtered_out)
                else:
                    print(f"Discovered GitHub repositories for {target}.")
                    selected = select_discovered_repos_interactively(
                        filtered,
                        input_stream=sys.stdin,
                        output_stream=sys.stdout,
                    )
                local_paths = None
                if args.clone:
                    local_paths = clone_discovered_repos(root, Path.cwd(), selected, args.clone_dir)
                elif args.select is None and _prompt_yes_no(
                    "Clone selected repositories now? Project-specific generation needs local paths.",
                    input_stream=sys.stdin,
                    output_stream=sys.stdout,
                    default=True,
                ):
                    clone_dir = _prompt_line(
                        "Clone directory",
                        input_stream=sys.stdin,
                        output_stream=sys.stdout,
                        default="./covered-repos",
                    )
                    local_paths = clone_discovered_repos(root, Path.cwd(), selected, clone_dir)
                entries, reused_entries = _register_or_reuse_discovered_repos(
                    root,
                    selected,
                    local_paths=local_paths,
                )
                for entry in reused_entries:
                    print(f"Repo {entry.id} is already registered at {_repo_location(entry)}")
                reused_ids = {entry.id for entry in reused_entries}
                for entry in entries:
                    if entry.id in reused_ids:
                        continue
                    print(f"Registered repo {entry.id} at {_repo_location(entry)}")
                if args.select is None:
                    _run_post_registration_wizard(
                        root,
                        entries,
                        input_stream=sys.stdin,
                        output_stream=sys.stdout,
                        skill_generator=args.llm,
                        skill_target=args.skill_target,
                    )
                return 0

            if args.repo_command == "set-path":
                root = _resolve_existing_org_pack_root(Path.cwd())
                entry = set_repo_path(root, Path.cwd(), args.repo_id, args.path)
                print(f"Updated repo {entry.id} path to {entry.local_path}")
                return 0

            if args.repo_command == "deactivate":
                root = _resolve_existing_org_pack_root(Path.cwd())
                entry = deactivate_repo(root, args.repo_id, args.reason)
                print(f"Deactivated repo {entry.id}: {entry.deactivation_reason}")
                return 0

            if args.repo_command == "remove":
                root = _resolve_existing_org_pack_root(Path.cwd())
                entry = remove_repo(root, args.repo_id, args.reason, force=args.force)
                print(f"Removed repo {entry.id} from registry: {args.reason.strip()}")
                return 0

            if args.repo_command == "list":
                root = _resolve_existing_org_pack_root(Path.cwd())
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

    except (
        OrgPackError,
        RepoRegistryError,
        RepoDiscoveryError,
        RepoOnboardingError,
        ApprovalError,
        EvalReplayError,
        CacheManagerError,
        ExplainError,
        ProposalError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error("unsupported command")
    return 2


def _repo_location(entry: RepoEntry) -> str:
    return entry.local_path or entry.url or "-"


def _resolve_existing_org_pack_root(cwd: Path) -> Path:
    root = resolve_default_root(cwd)
    if not (root / "harness.yml").is_file():
        raise OrgPackError(
            "no org skill pack found. Run 'harness org init --github https://github.com/<owner>' first, "
            "run from a directory containing org-agent-skills/harness.yml, or attach an existing pack with "
            "'harness org init --repo <path>'."
        )
    return root


def _resolve_or_init_org_pack_root(cwd: Path, org_name: str) -> tuple[Path, bool]:
    root = resolve_default_root(cwd)
    if (root / "harness.yml").is_file():
        return root, False
    return init_org_pack(cwd, org_name), True


def _run_setup_wizard(args: argparse.Namespace, *, input_stream, output_stream) -> int:
    if not input_stream.isatty() and args.source is None:
        raise OrgPackError("setup requires a GitHub source or 'local' when stdin is not interactive")

    print("Org Skill Harness setup", file=output_stream)
    source = args.source or _prompt_line(
        "GitHub org/profile URL, GitHub owner, or 'local'",
        input_stream=input_stream,
        output_stream=output_stream,
    )
    source = source.strip()
    if not source:
        raise OrgPackError("setup source cannot be empty")

    if source.lower() in {"local", "manual", "local-only"}:
        root = _setup_local_org_pack(input_stream=input_stream, output_stream=output_stream)
        entries = _setup_local_repositories(root, input_stream=input_stream, output_stream=output_stream)
    else:
        root, entries = _setup_github_repositories(
            source,
            include_archived=args.include_archived,
            include_forks=args.include_forks,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    _run_post_registration_wizard(
        root,
        entries,
        input_stream=input_stream,
        output_stream=output_stream,
        skill_generator=args.llm,
        skill_target=args.skill_target,
    )
    return 0


def _run_post_registration_wizard(
    root: Path,
    entries: tuple[RepoEntry, ...],
    *,
    input_stream,
    output_stream,
    skill_generator: str | None,
    skill_target: str | None,
) -> None:
    _print_validation_result(validate_org_pack(root), root, output_stream)
    if entries:
        print("Registered repositories:", file=output_stream)
        for entry in entries:
            print(f"  - {entry.id}: {_repo_location(entry)}", file=output_stream)
    else:
        print("No repositories registered yet.", file=output_stream)

    scope = _prompt_choice(
        "Generate skills now?",
        (
            ("project", "Project-specific repo skills"),
            ("global", "Global org skill"),
            ("both", "Both global and project-specific skills"),
            ("skip", "Skip skill generation"),
        ),
        input_stream=input_stream,
        output_stream=output_stream,
        default="project",
    )

    if scope in {"global", "both"}:
        generator = _select_skill_generator(
            skill_generator,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        target = (
            "codex"
            if generator == "template"
            else _select_skill_target(
                skill_target,
                input_stream=input_stream,
                output_stream=output_stream,
            )
        )
        skill_path = _generate_global_org_skill(root, generator=generator, skill_target=target)
        print(f"Generated global org skills at {skill_path}", file=output_stream)
        if generator != "template":
            for install_root in _global_skill_target_roots(target):
                installed_names = _skill_names_under(install_root)
                rendered_names = ", ".join(installed_names) if installed_names else "none"
                print(f"Installed global org skills at {install_root}: {rendered_names}", file=output_stream)
        _print_validation_result(validate_org_pack(root), root, output_stream)

    onboarded: list[RepoEntry] = []
    if scope in {"project", "both"}:
        generator = _select_skill_generator(
            skill_generator,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        target = (
            "codex"
            if generator == "template"
            else _select_skill_target(
                skill_target,
                input_stream=input_stream,
                output_stream=output_stream,
            )
        )
        onboarded = list(
            _setup_project_specific_skills(
                root,
                input_stream=input_stream,
                output_stream=output_stream,
                skill_generator=generator,
                skill_target=target,
            )
        )

    if onboarded and _prompt_yes_no(
        "Review and approve generated project skills now?",
        input_stream=input_stream,
        output_stream=output_stream,
        default=False,
    ):
        for entry in onboarded:
            print(render_approval_review(root, entry.id), end="", file=output_stream)
            if _prompt_yes_no(
                f"Approve all generated artifacts for {entry.id}?",
                input_stream=input_stream,
                output_stream=output_stream,
                default=False,
            ):
                rationale = _prompt_line(
                    "Approval rationale",
                    input_stream=input_stream,
                    output_stream=output_stream,
                    default="Reviewed through interactive setup",
                )
                result = approve_repo(root, entry.id, exclusions=(), rationale=rationale)
                print(
                    f"Approved {len(result.approved_artifacts)} artifact(s) for {entry.id}; status=approved-unverified",
                    file=output_stream,
                )

    if onboarded and _prompt_yes_no(
        "Run development eval replay for generated project skills?",
        input_stream=input_stream,
        output_stream=output_stream,
        default=False,
    ):
        for entry in onboarded:
            result = run_eval(root, entry.id, development=True)
            print(
                f"Evaluated {entry.id}; status={result.status}; report={result.report_path}",
                file=output_stream,
            )

    approved_entries = [
        entry
        for entry in load_repo_entries(root / "harness.yml")
        if entry.id in {onboarded_entry.id for onboarded_entry in onboarded}
        and entry.coverage_status in {"approved-unverified", "verified"}
    ]
    if approved_entries and _prompt_yes_no(
        "Refresh repo-local cache and export approved skills?",
        input_stream=input_stream,
        output_stream=output_stream,
        default=False,
    ):
        target = _prompt_choice(
            "Export target",
            (("codex", "Codex"), ("generic", "Generic"), ("skip", "Skip export")),
            input_stream=input_stream,
            output_stream=output_stream,
            default="codex",
        )
        for entry in approved_entries:
            refresh = refresh_cache(root, entry.id)
            print(f"Refreshed cache for {entry.id}: {refresh.cache_root}", file=output_stream)
            if target != "skip":
                exported = export_cached_pack(root, target, entry.id)
                print(f"Exported {target} pack for {entry.id}: {exported.export_root}", file=output_stream)

    if onboarded and _prompt_yes_no(
        "Show final state explanation for generated project skills?",
        input_stream=input_stream,
        output_stream=output_stream,
        default=False,
    ):
        for entry in onboarded:
            print(render_explain(root, entry.id), end="", file=output_stream)

    print("Setup complete.", file=output_stream)


def _setup_local_org_pack(*, input_stream, output_stream) -> Path:
    root = resolve_default_root(Path.cwd())
    if (root / "harness.yml").is_file():
        print(f"Using existing org skill pack at {root}", file=output_stream)
        return root
    org_name = _prompt_line(
        "Org name",
        input_stream=input_stream,
        output_stream=output_stream,
        default="local",
    )
    root = init_org_pack(Path.cwd(), org_name)
    print(f"Initialized org skill pack at {root}", file=output_stream)
    return root


def _setup_local_repositories(root: Path, *, input_stream, output_stream) -> tuple[RepoEntry, ...]:
    registered: list[RepoEntry] = []
    while True:
        path_value = _prompt_line(
            "Local repo path to register; leave blank when done",
            input_stream=input_stream,
            output_stream=output_stream,
            allow_empty=True,
        )
        if not path_value:
            break
        purpose = _prompt_line(
            f"Purpose for {path_value}",
            input_stream=input_stream,
            output_stream=output_stream,
            allow_empty=True,
        )
        owner = _prompt_line(
            f"Owner/team for {path_value}",
            input_stream=input_stream,
            output_stream=output_stream,
            allow_empty=True,
        )
        entry = add_repo(
            root,
            Path.cwd(),
            path_value,
            purpose=purpose or None,
            owner=owner or None,
        )
        registered.append(entry)
        print(f"Registered repo {entry.id} at {_repo_location(entry)}", file=output_stream)
    return tuple(registered)


def _setup_github_repositories(
    source: str,
    *,
    include_archived: bool,
    include_forks: bool,
    input_stream,
    output_stream,
) -> tuple[Path, tuple[RepoEntry, ...]]:
    target = infer_github_owner(source)
    root, initialized = _resolve_or_init_org_pack_root(Path.cwd(), target)
    if initialized:
        print(f"Initialized org skill pack for GitHub owner {target} at {root}", file=output_stream)
    else:
        print(f"Using existing org skill pack at {root}", file=output_stream)

    discovered = discover_github_user(target)
    filtered = filter_discovered_repos(discovered, include_archived=include_archived, include_forks=include_forks)
    print(f"Discovered GitHub repositories for {target}.", file=output_stream)
    selected = select_discovered_repos_interactively(
        filtered,
        input_stream=input_stream,
        output_stream=output_stream,
    )

    local_paths = None
    if _prompt_yes_no(
        "Clone selected repositories now? Project-specific generation needs local paths.",
        input_stream=input_stream,
        output_stream=output_stream,
        default=True,
    ):
        clone_dir = _prompt_line(
            "Clone directory",
            input_stream=input_stream,
            output_stream=output_stream,
            default="./covered-repos",
        )
        local_paths = clone_discovered_repos(root, Path.cwd(), selected, clone_dir)

    entries, reused_entries = _register_or_reuse_discovered_repos(root, selected, local_paths=local_paths)
    for entry in reused_entries:
        print(f"Repo {entry.id} is already registered at {_repo_location(entry)}", file=output_stream)
    reused_ids = {entry.id for entry in reused_entries}
    for entry in entries:
        if entry.id in reused_ids:
            continue
        print(f"Registered repo {entry.id} at {_repo_location(entry)}", file=output_stream)
    return root, entries


def _register_or_reuse_discovered_repos(
    root: Path,
    selected: tuple[DiscoveredRepo, ...],
    *,
    local_paths: dict[str, str] | None,
) -> tuple[tuple[RepoEntry, ...], tuple[RepoEntry, ...]]:
    existing_by_id = {entry.id: entry for entry in load_repo_entries(root / "harness.yml")}
    new_selected = tuple(repo for repo in selected if repo.id not in existing_by_id)
    registered = (
        register_discovered_repos(
            root,
            new_selected,
            local_paths={
                repo.id: local_paths[repo.id] for repo in new_selected if local_paths and repo.id in local_paths
            },
        )
        if new_selected
        else ()
    )
    registered_by_id = {entry.id: entry for entry in registered}
    ordered_entries: list[RepoEntry] = []
    reused_entries: list[RepoEntry] = []
    for repo in selected:
        existing = existing_by_id.get(repo.id)
        if existing is not None:
            ordered_entries.append(existing)
            reused_entries.append(existing)
            continue
        ordered_entries.append(registered_by_id[repo.id])
    return tuple(ordered_entries), tuple(reused_entries)


def _setup_project_specific_skills(
    root: Path,
    *,
    input_stream,
    output_stream,
    skill_generator: str,
    skill_target: str,
) -> tuple[RepoEntry, ...]:
    entries = tuple(
        entry
        for entry in load_repo_entries(root / "harness.yml")
        if entry.active and not entry.external and entry.local_path is not None
    )
    if not entries:
        print(
            "No local active repositories are available for project-specific generation. "
            "Clone repos or set paths first.",
            file=output_stream,
        )
        return ()

    selected = _select_registered_entries_interactively(
        entries,
        input_stream=input_stream,
        output_stream=output_stream,
    )
    onboarded: list[RepoEntry] = []
    for entry in selected:
        print(
            f"Generating project-specific skills for {entry.id} with {skill_generator}...",
            file=output_stream,
        )
        try:
            result = onboard_repo(root, entry.id, skill_generator=skill_generator, skill_target=skill_target)
        except RepoOnboardingError as exc:
            print(f"error: failed to generate project-specific skills for {entry.id}: {exc}", file=output_stream)
            continue
        print(f"Generated draft pack for repo {entry.id} into {result.artifact_root}", file=output_stream)
        skill_names = _skill_names_under(result.artifact_root / "skills")
        if skill_names:
            print(f"Generated repo skills for {entry.id}: {', '.join(skill_names)}", file=output_stream)
        if skill_generator != "template":
            install_roots = _repo_skill_install_roots_for_entry(root, entry, skill_target)
            for install_root in install_roots:
                installed_names = _skill_names_under(install_root)
                rendered_names = ", ".join(installed_names) if installed_names else "none"
                print(f"Installed {entry.id} skills at {install_root}: {rendered_names}", file=output_stream)
        _print_validation_result(validate_repo_onboarding(root, entry.id), root, output_stream, repo_id=entry.id)
        onboarded.append(entry)
    return tuple(onboarded)


def _select_registered_entries_interactively(
    entries: tuple[RepoEntry, ...],
    *,
    input_stream,
    output_stream,
) -> tuple[RepoEntry, ...]:
    discovered = tuple(
        DiscoveredRepo(
            id=entry.id,
            name=entry.name,
            owner=entry.owner,
            url=entry.url or entry.local_path or entry.id,
            default_branch=entry.default_branch,
            visibility="local" if entry.local_path else "remote",
            archived=False,
            fork=False,
            description=entry.purpose,
        )
        for entry in entries
    )
    selected = select_discovered_repos_interactively(
        discovered,
        input_stream=input_stream,
        output_stream=output_stream,
    )
    selected_ids = {repo.id for repo in selected}
    return tuple(entry for entry in entries if entry.id in selected_ids)


def _repo_skill_install_roots_for_entry(root: Path, entry: RepoEntry, skill_target: str) -> tuple[Path, ...]:
    if entry.local_path is None:
        return ()
    repo_path = (root / entry.local_path).resolve()
    normalized = skill_target.strip().lower()
    if normalized == "codex":
        return (repo_path / ".agents" / "skills",)
    if normalized == "claude":
        return (repo_path / ".claude" / "skills",)
    if normalized == "both":
        return (repo_path / ".agents" / "skills", repo_path / ".claude" / "skills")
    return ()


def _skill_names_under(skills_root: Path) -> list[str]:
    if not skills_root.is_dir():
        return []
    names = []
    for path in sorted(skills_root.iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            names.append(path.name)
    return names


def _select_skill_generator(value: str | None, *, input_stream, output_stream) -> str:
    configured = value or os.environ.get("ORGS_AI_HARNESS_SKILL_GENERATOR")
    if configured:
        return configured
    return _prompt_choice(
        "Skill generator",
        (
            ("codex", "Codex CLI"),
            ("claude", "Claude Code"),
        ),
        input_stream=input_stream,
        output_stream=output_stream,
        default="codex",
    )


def _select_skill_target(value: str | None, *, input_stream, output_stream) -> str:
    configured = value or os.environ.get("ORGS_AI_HARNESS_SKILL_TARGET")
    if configured:
        return configured
    return _prompt_choice(
        "Install generated skills for",
        (
            ("codex", "Codex only"),
            ("claude", "Claude Code only"),
            ("both", "Both Codex and Claude Code"),
        ),
        input_stream=input_stream,
        output_stream=output_stream,
        default="both",
    )


def _generate_global_org_skill(root: Path, *, generator: str, skill_target: str) -> Path:
    if generator == "template":
        return _generate_template_global_org_skill(root)

    target_roots = _global_skill_target_roots(skill_target)
    for target_root in target_roots:
        target_root.mkdir(parents=True, exist_ok=True)
    staging_roots = _global_skill_staging_roots(root, skill_target)
    for staging_root in staging_roots:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging_root.mkdir(parents=True, exist_ok=True)
    prompt_path = root / "org" / "org-level-skill-generation-prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(_render_org_level_skill_prompt(root, staging_roots, target_roots), encoding="utf-8")
    command = _llm_org_skill_generation_command(generator, root, staging_roots, prompt_path)
    log_path = root / "org" / "org-level-skill-generation.log"
    result = run_llm_command_with_progress(
        command,
        cwd=root,
        log_path=log_path,
        label=f"{generator} org-level skill generation",
    )
    if result.returncode != 0:
        message = result.tail or "LLM command failed without output"
        raise OrgPackError(
            f"{generator} org-level skill generation failed. See log: {log_path}. Last output: {message}"
        )
    _ensure_global_skill_outputs(staging_roots, generator, result.tail, log_path)
    _install_generated_skills(staging_roots[0], target_roots)
    return target_roots[0]


def _generate_template_global_org_skill(root: Path) -> Path:
    entries = load_repo_entries(root / "harness.yml")
    skill_root = root / "org" / "skills" / "org-repository-map"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True, exist_ok=True)
    skill_path = skill_root / "SKILL.md"
    reference_path = references_root / "repositories.md"
    skill_path.write_text(
        "# Org Repository Map\n\n"
        "Use this skill when a user asks which repositories exist in this org, "
        "where a capability likely lives, or which project-specific skill should be used.\n\n"
        "Start with `references/repositories.md`, then route repo-specific implementation "
        "questions to the matching repository skill pack when one exists.\n",
        encoding="utf-8",
    )
    lines = [
        "# Repositories",
        "",
        "This reference was generated by `harness setup` from the current repository registry.",
        "",
    ]
    if not entries:
        lines.append("No repositories are registered yet.")
    for entry in entries:
        lines.extend(
            [
                f"## {entry.id}",
                "",
                f"- Name: {entry.name}",
                f"- Owner: {entry.owner or 'unknown'}",
                f"- Location: {_repo_location(entry)}",
                f"- Default branch: {entry.default_branch or 'unknown'}",
                f"- Coverage status: {entry.coverage_status}",
                f"- Active: {str(entry.active).lower()}",
                f"- Purpose: {entry.purpose or 'not recorded'}",
                "",
            ]
        )
    reference_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return skill_path


def _render_org_level_skill_prompt(root: Path, staging_roots: tuple[Path, ...], target_roots: tuple[Path, ...]) -> str:
    base_prompt = _read_text_or_default(ORG_LEVEL_SKILL_PROMPT_PATH, DEFAULT_ORG_LEVEL_SKILL_PROMPT)
    staging_list = "\n".join(f"- {path}" for path in staging_roots)
    target_list = "\n".join(f"- {path}" for path in target_roots)
    entries = load_repo_entries(root / "harness.yml")
    repo_lines = []
    for entry in entries:
        repo_lines.append(
            f"- {entry.id}: local_path={entry.local_path or 'none'}, url={entry.url or 'none'}, "
            f"status={entry.coverage_status}, active={str(entry.active).lower()}"
        )
    repos = "\n".join(repo_lines) if repo_lines else "- No registered repositories"
    return f"""{base_prompt}

Additional harness constraints:

Generation staging targets:
{staging_list}

Final global runtime install targets:
{target_list}

Write organization-level skills only under every generation staging target listed above.
Do not write organization-level skills directly to the final global runtime install targets.
After validation, the harness will install the validated generated skills into the final global runtime install targets.
Do not write org-level skills under `.github/agent-skills/` unless that path is also explicitly listed above.

Registered repositories available through the harness:
{repos}

Org pack root:
{root}

Additional skill-shaping constraints:
- Prefer many small, specialized skills over a few broad general skills.
- Create targeted org-level skills with precise trigger metadata.
- Skill descriptions are critical routing metadata; make them concrete and narrow
  so agents avoid unnecessary context loading.
- Do not create one large "org practices" skill.
- If evidence is weak or repo conventions conflict, create a detector/decision
  skill rather than a universal policy skill.
"""


def _llm_org_skill_generation_command(
    generator: str,
    root: Path,
    target_roots: tuple[Path, ...],
    prompt_path: Path,
) -> list[str]:
    prompt = prompt_path.read_text(encoding="utf-8")
    if generator == "codex":
        executable = shutil.which("codex")
        if executable is None:
            raise OrgPackError("codex CLI is required for org skill generation but was not found on PATH")
        command = [
            executable,
            "exec",
            "--cd",
            str(root),
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        for writable_root in target_roots:
            command.extend(["--add-dir", str(writable_root)])
        for repo_path in _registered_local_repo_paths(root):
            command.extend(["--add-dir", str(repo_path)])
        command.append(prompt)
        return command
    if generator == "claude":
        executable = shutil.which("claude")
        if executable is None:
            raise OrgPackError("Claude Code CLI is required for org skill generation but was not found on PATH")
        command = [executable, "--print", "--permission-mode", "bypassPermissions"]
        for path in (root, *target_roots, *_registered_local_repo_paths(root)):
            command.extend(["--add-dir", str(path)])
        command.append(prompt)
        return command
    raise OrgPackError(f"unsupported skill generator: {generator}")


def _ensure_global_skill_outputs(
    target_roots: tuple[Path, ...],
    generator: str,
    output_tail: str,
    log_path: Path,
) -> None:
    skill_names_by_root = [_skill_names_under(root) for root in target_roots]
    missing_roots = [str(root) for root, names in zip(target_roots, skill_names_by_root, strict=True) if not names]
    first_names = set(skill_names_by_root[0]) if skill_names_by_root else set()
    inconsistent_roots = [
        str(root)
        for root, names in zip(target_roots[1:], skill_names_by_root[1:], strict=True)
        if set(names) != first_names
    ]
    if not missing_roots and not inconsistent_roots:
        return
    details = []
    if missing_roots:
        details.append("missing skills under " + ", ".join(missing_roots))
    if inconsistent_roots:
        details.append("generated skill set differs under " + ", ".join(inconsistent_roots))
    output = output_tail.strip()[:600]
    suffix = f"; last output: {output}" if output else ""
    raise OrgPackError(
        f"{generator} did not produce valid global skill files: {'; '.join(details)}. See log: {log_path}{suffix}"
    )


def _install_generated_skills(source_skills_root: Path, target_roots: tuple[Path, ...]) -> None:
    skill_roots = [
        path for path in sorted(source_skills_root.iterdir()) if path.is_dir() and (path / "SKILL.md").is_file()
    ]
    for target_root in target_roots:
        target_root.mkdir(parents=True, exist_ok=True)
        for skill_root in skill_roots:
            destination = target_root / skill_root.name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(skill_root, destination)


def _global_skill_target_roots(skill_target: str) -> tuple[Path, ...]:
    normalized = skill_target.strip().lower()
    home = Path.home()
    if normalized == "codex":
        return (home / ".agents" / "skills",)
    if normalized == "claude":
        return (home / ".claude" / "skills",)
    if normalized == "both":
        return (home / ".agents" / "skills", home / ".claude" / "skills")
    raise OrgPackError(f"unsupported skill target: {skill_target}")


def _global_skill_staging_roots(root: Path, skill_target: str) -> tuple[Path, ...]:
    normalized = skill_target.strip().lower()
    base = root / "org" / "llm-output"
    if normalized == "codex":
        return (base / "codex" / "skills",)
    if normalized == "claude":
        return (base / "claude" / "skills",)
    if normalized == "both":
        return (base / "codex" / "skills", base / "claude" / "skills")
    raise OrgPackError(f"unsupported skill target: {skill_target}")


def _registered_local_repo_paths(root: Path) -> tuple[Path, ...]:
    paths = []
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.local_path:
            paths.append((root / entry.local_path).resolve())
    return tuple(path for path in paths if path.is_dir())


def _read_text_or_default(path: Path, default: str) -> str:
    if not path.is_file():
        return default.strip()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise OrgPackError(f"skill generation prompt file is empty: {path}")
    return text


def _prompt_line(
    prompt: str,
    *,
    input_stream,
    output_stream,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        print(f"{prompt}{suffix}: ", end="", file=output_stream)
        output_stream.flush()
        value = input_stream.readline()
        if value == "":
            if default is not None:
                print("", file=output_stream)
                return default
            if allow_empty:
                print("", file=output_stream)
                return ""
            raise OrgPackError(f"missing response for prompt: {prompt}")
        value = value.strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""


def _prompt_yes_no(
    prompt: str,
    *,
    input_stream,
    output_stream,
    default: bool,
) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        value = _prompt_line(
            f"{prompt} [{default_label}]",
            input_stream=input_stream,
            output_stream=output_stream,
            allow_empty=True,
        ).lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.", file=output_stream)


def _prompt_choice(
    prompt: str,
    choices: tuple[tuple[str, str], ...],
    *,
    input_stream,
    output_stream,
    default: str,
) -> str:
    choice_by_key = {key: key for key, _ in choices}
    print(prompt, file=output_stream)
    for index, (key, label) in enumerate(choices, start=1):
        default_marker = " (default)" if key == default else ""
        print(f"  {index}. {label}{default_marker}", file=output_stream)
        choice_by_key[str(index)] = key
        choice_by_key[key] = key
    while True:
        value = _prompt_line(
            "Selection",
            input_stream=input_stream,
            output_stream=output_stream,
            default=default,
        )
        selected = choice_by_key.get(value.lower())
        if selected is not None:
            return selected
        print("Choose one of the listed options.", file=output_stream)


def _print_validation_result(result, root: Path, output_stream, repo_id: str | None = None) -> None:
    if result.ok:
        if repo_id is None:
            print(f"Validation passed for {root}", file=output_stream)
        else:
            print(f"Validation passed for {repo_id} at {root}", file=output_stream)
        return
    for error in result.errors:
        print(f"validation error: {error}", file=output_stream)
