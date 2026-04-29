---
name: proposal-refresh-workflow
description: Use this skill when modifying proposal-first learning, trace-backed improvement proposals, source refresh proposals, proposal apply/reject commands, redaction, or stale-cache handling.
---

# Proposal Refresh Workflow

## Purpose

Preserve the rule that accepted skills and artifacts change through evidence-backed proposals, not silent regeneration.

## When To Use

Use this for `proposals.py`, `improve`, `refresh`, `proposals list/show/apply/reject`, proposal metadata, redaction, patch application, approval hash updates, or cache stale checks after applied proposals.

## Inspect First

- `src/orgs_ai_harness/proposals.py` for proposal generation, metadata validation, redaction, patch parsing, and decisions.
- `src/orgs_ai_harness/cache_manager.py` for stale-cache checks against applied proposal ids.
- `src/orgs_ai_harness/eval_replay.py` and `approval.py` for trace event sources.
- `local-docs/development-phases/sprint-09-proposal-first-learning-and-refresh.md` and the proposal sections in the full application guide.
- Proposal-related tests in `RepoOnboardingTests`.

## Repository Map

- Proposals live under `org-agent-skills/proposals/prop_###/`.
- Required files are `summary.md`, `evidence.jsonl`, `patch.diff`, and `metadata.yml`.
- Improvement evidence comes from `trace-summaries/*.jsonl`.
- Refresh compares current source commit to `scan/scan-manifest.yml` `repo_source_commit`.

## Standard Workflow

1. Collect only trace-backed evidence for `improve`.
2. Redact secrets from proposal evidence using built-in patterns and `harness.yml` redaction config.
3. Create proposals as `open` with supported type, risk, target artifacts, affected evals, evidence refs, and source metadata.
4. `show` renders a compact human review; it does not mutate artifacts.
5. `apply` requires explicit `--yes`, validates metadata and patch target, appends the patch, updates approval hashes, and marks the proposal `applied`.
6. `reject` requires a non-empty reason and must not mutate target artifacts.
7. After apply, refresh the cache before exporting; export should fail stale caches.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoOnboardingTests
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness validate <repo-id>
```

CLI smoke flow:

```bash
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness improve <repo-id>
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness proposals list
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness proposals show prop_001
```

## Invariants

- Proposals are the path for changing approved/protected artifacts.
- Proposal evidence must not include secrets, tokens, private keys, or sensitive skipped file contents.
- Applying a proposal updates protected artifact hashes when approval metadata exists.
- Refresh creates proposals only; it does not overwrite accepted skills, summaries, resolvers, evals, or scripts.
- Open and rejected proposals do not make caches stale; applied proposals do.

## Common Pitfalls

- Mutating target artifacts during `improve`, `refresh`, `list`, or `show`.
- Applying a proposal without validating metadata first.
- Letting `patch.diff` target a file not listed in metadata.
- Forgetting to update approval hashes after changing protected artifacts.
- Exporting without checking whether applied proposals are included in cache metadata.

## Escalation

If a proposed change needs complex patching beyond the current append patch format, add validation and tests before expanding the patch parser.
