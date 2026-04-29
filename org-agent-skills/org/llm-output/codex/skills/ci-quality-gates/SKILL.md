---
name: ci-quality-gates
description: Use this skill when changing GitHub Actions, Makefile/package verification scripts, CI-required checks, build pipelines, or code that must pass local quality gates across ametel01 repos. It maps repo variants for Bun, Go, Scarb, frontend, indexer, and release workflows.
---

# CI Quality Gates

## Purpose

Mirror each repo's CI locally and keep workflow, script, and documentation changes aligned.

## When to use

Use when editing `.github/workflows/**`, `Makefile`, `package.json` scripts, `Scarb.toml`, build/test tooling, or when a code change needs final verification.

## Evidence from repositories

Strength: Strong.

- `vitals-db`: `.github/workflows/ci.yml` runs Bun install, `check:ci`, typecheck, test, build.
- `agents-toolbelt`: `.github/workflows/ci.yml` runs `make verify` on Linux and macOS.
- `horizon-starknet`: separate path-scoped workflows for Cairo build/fmt/lint/test, frontend CI, indexer CI, and release.
- `agents-toolbelt/CONTRIBUTING.md`: documents `make verify` as canonical and says not to submit subsets only.

## Standard workflow

1. Read the workflow that would run for the changed path.
2. Read the command source it invokes, such as `package.json`, `Makefile`, or `Scarb.toml`.
3. Run the closest local equivalent before reporting completion.
4. If changing the gate itself, update workflow files and docs together.
5. If a full gate is too expensive or blocked, run the narrowest matching subset and report the gap.

## Commands

| Area | CI-shaped checks |
|---|---|
| `vitals-db` | `bun install --frozen-lockfile`; `bun run check:ci`; `bun run typecheck`; `bun run test`; `bun run build` |
| `agents-toolbelt` | `make verify` |
| `horizon-starknet/contracts` | `scarb fmt --check`; `scarb check`; `scarb build`; `snforge test` |
| `horizon-starknet/packages/frontend` | `bun run typecheck`; `bun run lint`; `bun run format:check`; `bun test`; `bun run build`; e2e for UI flows |
| `horizon-starknet/packages/indexer` | `bun run typecheck`; `bun run lint`; `bun run format:check`; `bun run test`; `bun run build` |

## Required checks

- Keep path filters aligned with the package or contract area they validate.
- Use frozen installs in CI-like runs.
- For CI edits, validate YAML syntax and command availability.
- For gate changes, update contributor or README guidance if it names the old command.

## Common pitfalls

- Do not collapse `horizon-starknet` into one root CI command; its workflows are path-scoped.
- Do not skip `make verify` in `agents-toolbelt`; it includes lint, race tests, build, and vulnerability checks.
- Do not replace `check:ci` with `check` in `vitals-db`; CI uses warning-as-error behavior.

## Escalation / uncertainty rules

If a workflow references a tool not installed locally, report the missing tool and run the remaining checks that do not depend on it. Do not mark the gate fully validated.
