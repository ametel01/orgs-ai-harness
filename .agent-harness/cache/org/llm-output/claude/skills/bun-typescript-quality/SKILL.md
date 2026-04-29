---
name: bun-typescript-quality
description: Use this skill in ametel01 TypeScript repos and packages that use Bun, Biome, Prettier, strict tsconfig, Vitest or Bun test, Next.js, Hono, Apibara, or workspace scripts. It guides installs, lint/format/typecheck/build commands, TypeScript invariants, and known formatting variants.
---

# Bun TypeScript Quality

## Purpose

Keep TypeScript changes aligned with the account's Bun-first repos while detecting package-local variants.

## When to use

Use when editing `.ts`, `.tsx`, `package.json`, `tsconfig.json`, Biome/Prettier config, Next.js frontend code, Hono server code, or Apibara indexer code in sampled repos.

## Evidence from repositories

Strength: Strong.

- `vitals-db`: Bun workspace root with `bun.lock`, `biome.json`, project references, `bun run verify`.
- `agent-vitals`: Bun package with `bun.lock`, Biome linting, Prettier formatting, strict `tsconfig.json`.
- `horizon-starknet/packages/frontend`: Bun, Next.js, Biome, strict TS, Playwright e2e.
- `horizon-starknet/packages/indexer`: Bun, Apibara, Biome, Vitest, strict TS, Docker helpers.

## Standard workflow

1. Read the nearest `package.json`, `bun.lock`, `biome.json`, `.prettierrc*`, and `tsconfig.json`.
2. Install with `bun install --frozen-lockfile` only in the package or workspace root that owns the lockfile.
3. Use existing scripts rather than raw tool invocations.
4. Preserve strict TypeScript settings and fix types directly instead of weakening configs.
5. Validate with the package's check, typecheck, test, and build scripts as appropriate for the change.

## Commands

- `vitals-db`: `bun run check:ci`, `bun run typecheck`, `bun run test`, `bun run build`, or `bun run verify`.
- `agent-vitals`: `bun run check`, `bun run build`.
- `horizon-starknet/packages/frontend`: `bun run check`, `bun run test`, `bun run build`; use `bun run test:e2e --project=chromium` for e2e-impacting UI changes.
- `horizon-starknet/packages/indexer`: `bun run check`, `bun run test`, `bun run build`.

## Required checks

- Keep `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, and unused-code checks intact where configured.
- Use `import type`/`export type` where Biome requires it.
- Keep formatter settings from the local config; quote style and line width differ across packages.
- Keep `noExplicitAny`, unused imports, and non-null assertion rules respected unless the local override already permits the case.

## Common pitfalls

- `agent-vitals` disables Biome formatting and uses Prettier for formatting; do not use `biome format` there unless scripts change.
- `vitals-db` uses a root workspace and TypeScript project references; package-local changes can still require root `bun run typecheck`.
- `horizon-starknet` frontend and indexer use independent lockfiles and package scripts.
- Biome versions and rule severities vary; inspect the local `biome.json` before applying a rule globally.

## Exceptions

If a TypeScript repo lacks `bun.lock`, this skill is only a hint. Re-run `org-toolchain-detection` and follow the repo's own package manager.
