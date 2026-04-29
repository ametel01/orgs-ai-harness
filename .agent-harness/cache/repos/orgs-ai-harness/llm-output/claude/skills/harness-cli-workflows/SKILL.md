---
name: harness-cli-workflows
description: Use this skill when changing orgs-ai-harness CLI commands, setup wizard flows, argparse subcommands, user-facing command output, or command error handling in `src/orgs_ai_harness/cli.py`.
---

# Harness CLI Workflows

## Purpose

Keep the CLI aligned with the harness lifecycle: `setup`, `org`, `repo`, `onboard`, `validate`, `approve`, `eval`, `cache`, `export`, `explain`, `improve`, `refresh`, and `proposals`.

## When To Use

Use this when adding or changing CLI flags, command dispatch, setup wizard behavior, progress output, or user-facing error messages.

## Inspect First

- `src/orgs_ai_harness/cli.py` for parser definitions and dispatch.
- The module that owns the behavior, such as `repo_registry.py`, `repo_onboarding.py`, `approval.py`, `eval_replay.py`, `cache_manager.py`, or `proposals.py`.
- `tests/test_org_pack_foundation.py` for CLI smoke tests and expected stdout/stderr.
- `local-docs/development-phases/full-application-test-guide.md` when changing documented user flows.

## Repository Map

- CLI entrypoint: `src/orgs_ai_harness/__main__.py` calls `cli.main`.
- Parser and setup wizard: `src/orgs_ai_harness/cli.py`.
- Tests use `PYTHONPATH=$PWD/src` and invoke `python -m orgs_ai_harness`.
- `harness` console script is declared in `pyproject.toml`, but tests prefer module execution.

## Standard Workflow

1. Add parser arguments and dispatch in the same command family.
2. Keep CLI logic thin; put state changes in the owning module.
3. Preserve concise progress output for long LLM generation and setup flows.
4. For command failures, raise module-specific errors and let `main` print the message to stderr.
5. Update docs only for user-visible command or workflow changes.
6. Add or update CLI tests that run through `subprocess.run` with `PYTHONPATH` set to `src`.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness validate
```

Run a narrower test while iterating when possible:

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoOnboardingTests.test_cli_approve_without_all_renders_review_without_mutating_draft
```

## Invariants

- Commands must not silently mutate unrelated repos or generated artifacts.
- `repo discover` must require explicit selection; it must not add every visible repo by default.
- `setup` and LLM generation must write logs under `org-agent-skills/` and print concise progress.
- Remote org pack attachment records the URL only; it does not clone or create hosted resources.

## Common Pitfalls

- Adding CLI-only behavior that bypasses module validation.
- Forgetting tests assert exact substrings in stdout and stderr.
- Treating `harness` as always installed; local docs and tests use `python3 -m orgs_ai_harness`.
- Expanding setup wizard behavior without preserving non-interactive `--select` flows.

## Escalation

If a command changes lifecycle status or artifact shape, inspect the validation and approval/eval/cache modules before editing. If docs and code disagree, update both or leave a clear note in the final response.
