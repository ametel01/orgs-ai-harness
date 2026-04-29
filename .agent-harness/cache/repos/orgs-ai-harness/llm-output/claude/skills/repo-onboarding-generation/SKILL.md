---
name: repo-onboarding-generation
description: Use this skill when changing repo onboarding scans, sensitive file skipping, generated Agent Skills prompts, LLM staging targets, resolvers, evals, draft pack reports, or `onboard` behavior.
---

# Repo Onboarding Generation

## Purpose

Protect the read-only scan and draft skill generation workflow that turns one selected repository into staged Agent Skills, resolvers, evals, scripts, unknowns, and a reviewable pack report.

## When To Use

Use this for `scan_repo_only`, `onboard_repo`, safe evidence collection, LLM command invocation, staged skill outputs, generated artifact shape, or repo-specific `validate <repo-id>` failures.

## Inspect First

- `src/orgs_ai_harness/repo_onboarding.py` for scan policy, prompts, LLM command execution, staging roots, and generated artifacts.
- `src/orgs_ai_harness/validation.py` for generated skill, resolver, eval, script, and pack report checks.
- `local-docs/SINGLE_REPO_SKILL_BUILD.md` for generator instructions.
- `local-docs/development-phases/sprint-04-read-only-repo-scan-and-onboarding-summary.md` and `sprint-05-draft-skill-pack-generation-and-validation.md`.
- `RepoOnboardingTests` in `tests/test_org_pack_foundation.py`.

## Repository Map

- Scan-only writes `onboarding-summary.md`, `unknowns.yml`, `scan/scan-manifest.yml`, and `scan/hypothesis-map.yml`.
- LLM generation writes first under `repos/<repo-id>/llm-output/<target>/skills`.
- Snapshot/review artifacts live under `repos/<repo-id>/skills`, `resolvers.yml`, `evals/onboarding.yml`, `scripts/`, and `pack-report.md`.
- Runtime install targets are `.agents/skills` and `.claude/skills` inside the covered repo, but generation prompts may restrict writes to staging.

## Standard Workflow

1. Resolve the registered repo and reject external, inactive, remote-only, or missing-path entries.
2. Run a safe scan; record sensitive skipped paths but never read their contents.
3. Preserve the blocking unknown about the narrowest reliable test command unless evidence closes it.
4. Generate skills only into staging roots first.
5. Validate that every staging target contains the same valid skill set.
6. Snapshot generated skills to the artifact pack, repair referenced `references/repo-evidence.md` when needed, and write resolvers/evals/scripts/report.
7. Mark one-repo candidate org skills as candidates, not org-wide standards.

## Sensitive Path Rules

Skip `.env`, `.env.*`, private key suffixes, credential/secret/token filenames, local override files, and SSH private key stems. Tests assert skipped contents such as secret fixture strings do not leak into artifacts.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoOnboardingTests
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness validate orgs-ai-harness
```

For generated skills, also run `skills-ref validate <skill-dir>` if `skills-ref` is available.

## Invariants

- Approved artifacts cannot be overwritten by direct onboarding regeneration; use proposals for changes.
- Generated evals must have 8-12 tasks with objective evidence lists.
- Script manifest entries must be deterministic, local-only, review-required, and include command permissions.
- Resolver skills must reference generated repo skills or existing org skills.

## Common Pitfalls

- Writing generated repo-level skills directly to runtime install targets when a prompt restricts output to staging.
- Reading sensitive files to "confirm" they are sensitive.
- Treating JSON-written `.yml` artifacts as a bug; this repo currently uses JSON-compatible YAML files.
- Forgetting to update `pack-report.md` status language.

## Escalation

If scan evidence is too weak to choose commands or architecture, keep the unknown open and phrase generated guidance as conditional.
