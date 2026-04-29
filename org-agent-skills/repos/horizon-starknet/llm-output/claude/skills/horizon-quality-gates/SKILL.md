---
name: horizon-quality-gates
description: Use this skill when choosing validation commands or CI-equivalent checks for horizon-starknet changes across Cairo contracts, Next.js frontend, Apibara indexer, Docker devnet, generated ABIs, formatting, tests, builds, or GitHub Actions workflows.
---

# Horizon Quality Gates

## Purpose

Choose the smallest reliable validation command for a change in this monorepo and align local checks with GitHub Actions.

## When to use

Use before finalizing any code, config, generated ABI, workflow, Docker, deploy, frontend, indexer, or contract change, and when a command fails and you need the next scoped validation step.

## Inspect first

- `Makefile`
- `.github/workflows/*.yml`
- Relevant package `package.json`
- Relevant `CLAUDE.md` in `contracts/`, `packages/frontend/`, `packages/indexer/`, or `deploy/`
- The file changed and at least one related caller, test, type definition, or workflow

## Change-to-command map

Contracts:

```bash
cd contracts && scarb fmt --check
cd contracts && scarb check
cd contracts && scarb build
cd contracts && snforge test
```

Frontend:

```bash
cd packages/frontend && bun run typecheck
cd packages/frontend && bun run lint
cd packages/frontend && bun run format:check
cd packages/frontend && bun test
cd packages/frontend && bun run build
cd packages/frontend && bun run test:e2e --project=chromium
```

Indexer:

```bash
cd packages/indexer && bun run typecheck
cd packages/indexer && bun run lint
cd packages/indexer && bun run format:check
cd packages/indexer && bun run test
cd packages/indexer && bun run build
```

Cross-boundary ABI changes:

```bash
make build
cd packages/frontend && bun run codegen
cd packages/indexer && bun run codegen
```

Docker/devnet:

```bash
make dev-up
make dev-logs
make dev-down
cd packages/indexer && bun run docker:up
cd packages/indexer && bun run docker:down
```

## CI parity

- Cairo workflows run `scarb build`, `scarb fmt --check`, `scarb check`, and `snforge test`.
- Frontend CI runs Bun install, `typecheck`, `lint`, `format:check`, `bun test`, `bun run build`, and Chromium Playwright.
- Indexer CI runs Bun install, `typecheck`, `lint`, `format:check`, `bun run test`, and `bun run build`.
- Release workflow only creates GitHub releases on tags matching `v*`.

## Standard workflow

1. Confirm `pwd` before running commands.
2. Prefer narrow tests first, then package-level checks, then CI-equivalent checks.
3. If a command fails, read the full error and change approach before retrying.
4. Do not use npm/yarn/pnpm in the Bun packages unless a script explicitly invokes another tool.
5. Do not update lockfiles casually. If dependency changes are required, use the package's existing manager and report it.
6. After codegen, inspect diffs in generated ABI/event files before finalizing.

## Common pitfalls

- README badges mention older workflow names; trust `.github/workflows/*.yml`.
- `.tool-versions` currently differs from older README text; trust `.tool-versions` and package manifests for current local tooling.
- `bun run check` is package-local and differs by frontend/indexer. It combines typecheck plus Biome checks, not tests.
- Playwright requires browser installation in CI; locally use `bunx playwright install --with-deps chromium` if browsers are missing.
- `make dev-down` removes Docker volumes; warn the user if local state matters.

## Done criteria

Report the exact commands run, whether they passed, and any skipped checks with a concrete reason such as missing Docker, missing Playwright browsers, or unavailable live RPC.
