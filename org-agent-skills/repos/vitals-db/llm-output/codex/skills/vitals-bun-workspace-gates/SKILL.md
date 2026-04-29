---
name: vitals-bun-workspace-gates
description: Use this skill when setting up, validating, linting, formatting, typechecking, testing, building, or changing CI in vitals-db. It covers Bun workspaces, Biome rules, TypeScript project references, package-manager constraints, and the exact quality gates used by GitHub Actions.
---

# Vitals Bun Workspace Gates

## Purpose

Keep repository-wide validation aligned with the actual `vitals-db` workspace. Use Bun, Biome, TypeScript build mode, and the same gate order as CI.

## When To Use

- Installing dependencies, running tests, or validating any change.
- Editing `package.json`, `bun.lock`, `biome.json`, `tsconfig*.json`, `lefthook.yml`, or `.github/workflows/ci.yml`.
- Adding workspace packages under `apps/*` or `packages/*`.
- Debugging lint, typecheck, build, or CI failures.

## Repository Map

- Root workspace: `package.json`, `bun.lock`.
- Packages: `packages/core`, `packages/db`, `packages/ingest`, `packages/queries`.
- Apps: `apps/server`, `apps/web`.
- Formatting/linting: `biome.json`.
- TypeScript references: root `tsconfig.json` plus per-package `tsconfig.json`.
- CI source of truth: `.github/workflows/ci.yml`.

## Standard Workflow

1. Verify the working directory is the repo root with `pwd`.
2. Read the relevant package manifest before changing scripts or dependencies.
3. Use Bun, not npm/pnpm/yarn. The root declares `packageManager: bun@1.3.13`.
4. For local setup, run:
   ```bash
   bun install
   ```
5. Prefer narrow validation while iterating:
   ```bash
   bun test packages/queries/src/__tests__/workouts.test.ts
   bun test apps/server/src/__tests__/server.test.ts
   bun run typecheck
   ```
6. Before finishing broad or cross-package changes, run the repo gate:
   ```bash
   bun run verify
   ```

## Validation Commands

- `bun run check:ci` - Biome check with warnings as errors, matching CI.
- `bun run typecheck` - TypeScript build mode over all project references.
- `bun run test` - full Bun test suite.
- `bun run build` - typecheck plus Next web build.
- `bun run verify` - local equivalent of the main non-build CI gate: `check:ci`, `typecheck`, `test`.

CI runs:

```bash
bun install --frozen-lockfile
bun run check:ci
bun run typecheck
bun run test
bun run build
```

## Invariants

- Keep workspace package versions and `workspace:*` dependencies consistent unless deliberately changing release metadata.
- Do not replace Bun workspace commands with npm/pnpm/yarn commands.
- Preserve strict TypeScript settings from `tsconfig.base.json`, especially `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, and `verbatimModuleSyntax`.
- Preserve Biome constraints: no unused variables/imports/params, no explicit `any`, no non-null assertions, no `console.log`, Node builtins via `node:` imports, and import types where required.
- Do not commit or rely on generated DuckDB files; Biome ignores `**/*.duckdb*`.

## Common Pitfalls

- `bun run check` may pass while CI fails on warnings; use `bun run check:ci` for final validation.
- `bun run build` already runs `typecheck`; use it when web or CI parity matters.
- `apps/web/README.md` is a default Next scaffold and is not the repo source of truth for commands; prefer root `README.md` and root `package.json`.
- If a command fails, read the full error and adjust the command or code. Do not retry the same failing command blindly.

## Escalation

If native DuckDB or Next build behavior fails due to local machine setup rather than code, report the exact command, error, platform context, and the narrower commands that did pass.
