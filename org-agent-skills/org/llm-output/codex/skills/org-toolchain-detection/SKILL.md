---
name: org-toolchain-detection
description: Use this skill when entering an ametel01 repo or package and choosing install, build, test, lint, format, release, or CI commands. Detect Bun, Biome, Prettier, Go Makefiles, Scarb/snforge, Python packaging, lockfiles, and GitHub Actions before running commands.
---

# Org Toolchain Detection

## Purpose

Choose commands from the target repo's own manifests and workflows. The account uses several toolchains side by side, so detection is an org-wide requirement.

## When to use

Use before running install, build, test, lint, format, typecheck, release, security, or deployment commands in any sampled repo.

## Evidence from repositories

Strength: Strong.

- `vitals-db`: `package.json`, `bun.lock`, `biome.json`, `tsconfig.json`, `.github/workflows/ci.yml`.
- `agent-vitals`: `package.json`, `bun.lock`, `biome.json`, `.prettierrc.json`, `tsconfig.json`.
- `horizon-starknet`: root `Makefile`, `contracts/Scarb.toml`, per-package `packages/frontend/package.json`, `packages/indexer/package.json`, per-package `bun.lock`, path-scoped workflows.
- `agents-toolbelt`: `go.mod`, `go.sum`, `Makefile`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`.
- `orgs-ai-harness`: `pyproject.toml`, `tests/test_org_pack_foundation.py`.

## Standard workflow

1. Inspect the target directory and nearest parent for manifests before choosing commands.
2. Prefer package scripts, `Makefile` targets, and workflow commands over reconstructed raw commands.
3. If a repo has multiple subprojects, run commands from the subproject working directory shown by the manifest or workflow.
4. Mirror CI order locally when practical.
5. If no workflow exists, run the smallest command that validates the touched area.

## Command detector

| Detected files | Default command shape |
|---|---|
| `package.json` plus `bun.lock` | `bun install --frozen-lockfile`, then `bun run <script>` |
| `biome.json` | Use Biome through scripts; do not substitute ESLint or Prettier unless the repo has those configs |
| `.prettierrc.json` plus scripts | Run the repo's Prettier script, usually `bun run format:check` |
| `Makefile` with `verify` | Prefer `make verify`; use individual targets only while iterating |
| `go.mod` | Use `make verify` when present, otherwise `go test ./...` and configured linters |
| `contracts/Scarb.toml` | From `contracts`: `scarb fmt --check`, `scarb check`, `scarb build`, `snforge test` |
| `pyproject.toml` without richer gates | Use Python's local test command or the harness CLI tests; do not assume `uv`, Ruff, or Pyright exist |

## Required checks

- Confirm the working directory before running commands.
- Use frozen installs when the repo has a lockfile and CI does the same.
- Scope to changed package when workflows are path-scoped.

## Common pitfalls

- Do not assume pnpm or npm; sampled TypeScript repos use Bun.
- Do not run root `bun install` in `horizon-starknet`; frontend and indexer have separate package directories and lockfiles.
- Do not treat `orgs-ai-harness` like a uv-managed Python repo unless a lockfile or config appears.
- Do not invent one global org command; the variation is intentional.

## Escalation / uncertainty rules

If manifests and workflows disagree, state the conflict and prefer the command used by GitHub Actions unless the user asks for a narrower local check.
