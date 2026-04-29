---
name: harness-config-registry
description: Use this skill when editing `harness.yml` parsing, repo registry mutations, org pack initialization, repo coverage statuses, or config round-trip behavior in orgs-ai-harness.
---

# Harness Config Registry

## Purpose

Preserve the central org skill pack contract while changing config parsing, `harness.yml` rendering, repo registration, path repair, deactivation, removal, or external dependency records.

## When To Use

Use this for changes in `config.py`, `org_pack.py`, `repo_registry.py`, or validation of top-level `harness.yml` fields and repo entries.

## Inspect First

- `src/orgs_ai_harness/config.py` for block-based parsing and round-trip rendering.
- `src/orgs_ai_harness/org_pack.py` for pack skeletons, attachments, and protected init paths.
- `src/orgs_ai_harness/repo_registry.py` for repo entry fields and mutations.
- `src/orgs_ai_harness/validation.py` for accepted status and config invariants.
- Tests in `OrgPackFoundationTests` and `RepoRegistryTests`.

## Repository Map

- Source of truth directory: `org-agent-skills/`.
- Required pack shape: `harness.yml`, `org/resolvers.yml`, `org/skills/`, `repos/`, `proposals/`, `trace-summaries/`.
- Registry entries live under the top-level `repos:` block in `harness.yml`.

## Standard Workflow

1. Preserve unknown/future top-level config blocks when rewriting `harness.yml`.
2. Keep `org.name`, `org.skills_version`, `providers`, `repos`, `redaction`, and `command_permissions` present.
3. Use `RepoEntry` and `save_repo_entries`; do not hand-edit YAML strings in callers.
4. Store local repo paths relative to the org pack root.
5. Keep remote-only repos valid without `local_path`; onboarding must reject them with repair guidance.
6. Validate after each registry mutation.

## Status Rules

- Active selected coverage: `selected`, `onboarding`, `needs-investigation`, `draft`, `approved-unverified`, `verified`.
- Inactive coverage: `deactivated` with a non-empty `deactivation_reason`.
- External references: `coverage_status: external`, `external: true`, `active: false`.
- Approved or verified repos require matching approval metadata.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.OrgPackFoundationTests
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoRegistryTests
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness validate
```

## Invariants

- `init_org_pack` refuses to overwrite existing protected pack paths.
- `attach_org_pack` validates local packs before recording the attachment.
- Duplicate repo ids must fail without mutating the registry.
- Removing a repo never deletes repository contents.
- Removing entries with onboarding metadata requires explicit `--force`.

## Common Pitfalls

- Replacing `harness.yml` with a serializer that drops unknown future sections.
- Writing absolute `local_path` values.
- Allowing active external repos or inactive selected repos.
- Deriving repo ids without applying the existing normalization rules.

## Escalation

If a new status is needed, update `RepoEntry` use sites, validation, explain output, lifecycle docs, and tests together.
