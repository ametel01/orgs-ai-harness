---
name: harness-validation-tests
description: Use this skill when adding or fixing orgs-ai-harness tests, validation rules, generated artifact schema checks, or local quality commands for the Python unittest suite.
---

# Harness Validation Tests

## Purpose

Keep validation behavior and regression tests aligned with the harness artifact contracts.

## When To Use

Use this for changes to `validation.py`, artifact schemas, status checks, CLI smoke tests, deterministic fixture tests, or the expected shape of generated skills, evals, scripts, approval metadata, cache metadata, and proposals.

## Inspect First

- `src/orgs_ai_harness/validation.py` for validation rules and error messages.
- `tests/test_org_pack_foundation.py` for all current tests; the suite is intentionally concentrated in one file.
- The module that creates the artifact being validated.
- `local-docs/development-phases/*` for acceptance criteria when changing sprint-level behavior.

## Repository Map

- Test framework: Python `unittest`.
- Test command: run from repo root with `PYTHONPATH="$PWD/src"`.
- Fixtures are created in temporary directories; avoid relying on repository-local mutable state.
- Several tests fake provider tools by prepending temporary executables to `PATH`.

## Standard Workflow

1. Read both the validator and the artifact producer before editing either.
2. Add a failing regression test for each new validation rule or lifecycle edge case.
3. Prefer CLI smoke tests when behavior crosses parser, module, filesystem, and validation boundaries.
4. Keep error messages actionable; tests usually assert key substrings, not full text.
5. Use deterministic temp fixtures and JSON-compatible artifact writes.
6. Run focused tests first, then the full suite.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation
```

Focused examples:

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.OrgPackFoundationTests
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoRegistryTests
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoOnboardingTests
```

## Invariants

- `validate_org_pack` checks the minimum org pack skeleton and `harness.yml`.
- `validate_repo_onboarding` accepts scan-only artifacts and adds generated-pack checks only when generated markers exist.
- Generated skill names must be lowercase kebab-case and match frontmatter `name`.
- Broken `references/...` links in `SKILL.md` must fail validation.
- Approved/verified repos require coherent `approval.yml` metadata.

## Common Pitfalls

- Running tests without `PYTHONPATH="$PWD/src"`.
- Retrying a failing command before reading stderr.
- Adding validator requirements without updating producers and fixture tests.
- Using broad filesystem state instead of temporary directories.
- Assuming a YAML parser is present; current validation uses simple parsing and JSON loading for JSON-compatible YAML.

## Escalation

If a validation change affects accepted artifact formats, update the corresponding docs and lifecycle tests in the same change.
