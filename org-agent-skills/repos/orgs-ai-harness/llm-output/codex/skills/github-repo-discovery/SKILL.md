---
name: github-repo-discovery
description: Use this skill when modifying GitHub repository discovery, `gh repo list` integration, explicit repo selection, fork/archive filters, clone behavior, or interactive checkbox selection.
---

# GitHub Repo Discovery

## Purpose

Keep discovery explicit, deterministic, and safe. The harness may discover visible GitHub repositories, but it only registers repositories selected by the user.

## When To Use

Use this when changing `repo discover`, GitHub owner inference, `gh` command handling, checkbox selection, clone behavior, or fork/archive filtering.

## Inspect First

- `src/orgs_ai_harness/repo_discovery.py` for provider calls, filtering, selection, terminal UI, and cloning.
- `src/orgs_ai_harness/cli.py` for `repo discover` and `setup` dispatch.
- `src/orgs_ai_harness/repo_registry.py` for how discovered repos become `RepoEntry` values.
- `tests/test_org_pack_foundation.py`, especially `RepoRegistryTests` around fake `gh` and fake `git`.

## Repository Map

- GitHub discovery depends on the local `gh` CLI.
- Tests replace `gh` and `git` with fake executables in temporary `PATH` directories.
- Interactive selection has two paths: checkbox UI for TTY streams and line input for non-TTY streams.

## Standard Workflow

1. Parse GitHub profile URLs with `infer_github_owner`; reject non-owner URLs.
2. Fetch repos through `gh repo list <owner>` and parse the JSON fields the tests model.
3. Filter archived repos and forks unless `--include-archived` or `--include-forks` is set.
4. Require selection by `--select` or interactive prompt.
5. Register only selected repos, preserving owner, URL, default branch, visibility metadata where available.
6. If cloning, skip existing destinations with a warning and record the existing path.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoRegistryTests
```

Use focused tests for the path touched, for example:

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoRegistryTests.test_cli_repo_discover_hides_archived_and_forks_by_default
```

## Invariants

- Discovery never auto-registers every visible repo.
- Missing, unauthenticated, or failing `gh` must report concise setup guidance without partial registry mutation.
- Selection can use repo id or repo name.
- Selecting a filtered-out repo must explain the include flag needed.
- `--github-org` and `--github-user` are mutually exclusive.

## Common Pitfalls

- Letting provider failures leave a partially initialized registry.
- Treating archived repos or forks as selectable without explicit flags.
- Breaking non-TTY selection while adjusting the checkbox UI.
- Assuming clone destination absence; existing directories are a supported path.

## Escalation

If adding a non-GitHub provider, keep provider-specific behavior out of registry mutation and preserve explicit selection before writing `harness.yml`.
