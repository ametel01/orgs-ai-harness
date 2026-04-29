---
name: horizon-frontend-starknet
description: Use this skill when changing the Horizon Next.js dApp in packages/frontend, including Starknet wallet hooks, typed contract calls, Router/RouterStatic integrations, FSD feature modules, WAD math, React Query cache keys, shadcn UI, or contract ABI codegen.
---

# Horizon Frontend Starknet

## Purpose

Guide frontend work in the Bun/Next.js dApp while preserving Feature-Sliced Design, typed Starknet calls, WAD math, cache invalidation, and contract-generated types.

## When to use

Use for `packages/frontend` edits: app routes, page compositions, widgets, features, entities, shared Starknet helpers, wallet flows, transaction forms, analytics pages, API routes, generated ABI types, or UI that reads indexer data.

## Inspect first

- `packages/frontend/CLAUDE.md`
- `packages/frontend/package.json`
- `packages/frontend/biome.json`
- `packages/frontend/tsconfig.json`
- Target feature folder under `src/features/`, `src/entities/`, `src/widgets/`, or `src/shared/`
- `src/shared/starknet/contracts.ts`, `src/shared/config/addresses.ts`, and `src/shared/query/query-keys.ts` for contract/data-flow changes

## Architecture rules

- FSD layers are intentional: `app` composes pages; `widgets` are page-level compositions; `features` own user interactions; `entities` model domain concepts; `shared` contains primitives and business-logic-free utilities.
- Prefer aliases: `@shared/*`, `@entities/*`, `@features/*`, `@widgets/*`. Avoid resurrecting legacy `@/components`, `@/hooks`, `@/contexts`, or broad `@/lib` imports.
- New feature code follows `features/<name>/{api,model,ui,index.ts}` and exports only the public API through `index.ts`.
- Use shadcn/Radix/lucide-backed shared UI components from `src/shared/ui` before introducing new primitives.
- Keep server-only code under `src/shared/server` or API routes; do not import it into client components.

## Starknet workflow

1. For write actions, use wallet/account hooks from `@features/wallet` and typed contracts from `@shared/starknet/contracts`.
2. Build multicalls with `populate(...)`, `uint256.bnToUint256(...)`, approvals first, then Router action.
3. Use Router for user-facing protocol actions and RouterStatic for previews when deployed; handle `null` RouterStatic on networks with `0x0` address.
4. Use `getAddresses(network)` and `getMarketParams(network)` instead of hard-coded contract addresses.
5. Use `@shared/math` WAD helpers (`parseWad`, `formatWad`, `wadMul`, `fromWad`, `toWad`) for token/rate math.
6. Use React Query keys from `src/shared/query/query-keys.ts` for new API-backed or chain-backed queries. Invalidate related market, token balance, allowance, LP, and indexer keys after mutations.

## Codegen workflow

If Cairo ABIs changed:

```bash
make build
cd packages/frontend && bun run codegen
```

Generated types live in `packages/frontend/src/types/generated`. Do not hand-edit generated ABI files except to diagnose codegen.

## Validation commands

Use Bun, not npm/yarn:

```bash
cd packages/frontend && bun run typecheck
cd packages/frontend && bun run check
cd packages/frontend && bun test
cd packages/frontend && bun run build
cd packages/frontend && bun run test:e2e --project=chromium
```

For a narrow unit test:

```bash
cd packages/frontend && bun test src/shared/math/amm.test.ts
```

## Common pitfalls

- `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, and strict boolean rules are enabled; build objects conditionally instead of assigning `undefined` to optional fields.
- Biome forbids `any`, unused imports/vars, direct `next/script`, `dangerouslySetInnerHTML`, and console usage outside configured test/API exceptions.
- The shadcn `components.json` aliases still mention legacy paths, but this repo's actual convention is `src/shared/ui`.
- Some query keys in older code are raw arrays; prefer the centralized factories for new work.
- Frontend env: `NEXT_PUBLIC_NETWORK` is public; `RPC_URL`, `DATABASE_URL`, and Sentry values are server-side only.

## Escalation rules

Ask before changing address JSON shape, network defaults, CSP/Sentry plumbing, wallet provider architecture, or generated-code conventions because those changes affect deployment and CI.
