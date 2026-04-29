---
name: horizon-indexer-events
description: Use this skill when modifying the Horizon Apibara indexer in packages/indexer, including Starknet event filters, factory-pattern contract discovery, Drizzle schema or migrations, Zod event validation, u256/i256/ByteArray parsing, knownContracts, health metrics, and PostgreSQL views.
---

# Horizon Indexer Events

## Purpose

Guide changes to the Apibara DNA indexer that streams Horizon Starknet events into PostgreSQL with Drizzle, validation, idempotent inserts, health metrics, and analytics views.

## When to use

Use for `packages/indexer` changes: adding events, editing indexers, schema migrations, event ABI generation, known contract lists, runtime config, database views, parsing utilities, health/metrics, or tests around event transforms.

## Inspect first

- `packages/indexer/CLAUDE.md`
- `packages/indexer/package.json`
- `packages/indexer/apibara.config.ts`
- `packages/indexer/drizzle.config.ts`
- `packages/indexer/src/schema/index.ts`
- `packages/indexer/src/lib/validation.ts`
- `packages/indexer/src/lib/utils.ts`
- The relevant `packages/indexer/src/indexers/*.indexer.ts`
- `docs/EVENTS.md` and related contract event definitions when event shape changes

## Architecture rules

- Static indexers listen to fixed addresses from `src/lib/constants.ts`: factory, market-factory, router.
- Factory-pattern indexers (`sy`, `yt`, `market`) must listen for creation events and also include `knownSYContracts`, `knownYTContracts`, or `knownMarkets` so restarts work after checkpoints pass creation blocks.
- Every event table has `_id`, `block_number`, `block_timestamp`, `transaction_hash`, and `event_index`.
- Preserve unique event keys on `(block_number, transaction_hash, event_index)` and idempotent `onConflictDoNothing()` batch inserts.
- Use one table per event type; do not collapse unrelated events into a generic log table.

## Adding or changing an event

1. Confirm the Cairo event fields in `contracts/src/**` and `docs/EVENTS.md`.
2. Update or add a Zod schema in `src/lib/validation.ts`.
3. Add or update the Drizzle table in `src/schema/index.ts` with indexes and the event-key unique index.
4. Add a selector with `getSelector("EventName")`.
5. Add filter entries. For factory-pattern contracts, update both known-contract filters and dynamic `factory(...)` filters.
6. Parse using utilities from `src/lib/utils.ts`: `matchSelector`, `readU256`, `readI256`, `readFelt`, `readFeltAsNumber`, `decodeByteArrayWithOffset`.
7. Validate each event with `validateEvent(...)`; skip malformed data events, but rethrow programmer errors with `isProgrammerError`.
8. Insert rows in a transaction with `onConflictDoNothing()`.
9. Add focused Vitest coverage in `packages/indexer/tests`.
10. Run `bun run db:generate` when schema changes create a real migration.

## Codegen workflow

Indexer event ABIs depend on frontend generated ABIs:

```bash
cd packages/indexer && bun run codegen
```

This script builds contracts, generates frontend ABI types, extracts event-only ABIs into `src/lib/abi`, and formats output.

## Validation commands

```bash
cd packages/indexer && bun run typecheck
cd packages/indexer && bun run check
cd packages/indexer && bun run test
cd packages/indexer && bun run build
```

For schema changes:

```bash
cd packages/indexer && bun run db:generate
cd packages/indexer && bun run db:migrate
```

For local services:

```bash
cd packages/indexer && bun run docker:up
cd packages/indexer && bun run dev:devnet
```

## Common pitfalls

- DNA selector hex may be padded differently; always compare with `matchSelector`.
- Avoid deprecated `readU256Safe` for new parsing because it masks malformed events.
- ByteArray fields are variable length; use offset-returning decode helpers when fields follow a ByteArray.
- `block.header.timestamp` is stored as the event timestamp; keep the type consistent with Drizzle schema.
- `apibara.config.ts` starting blocks and `src/lib/constants.ts` starting blocks must stay aligned for network presets.
- The docs may lag the current schema count; trust `src/schema/index.ts` and tests for the current event table set.

## Escalation rules

Ask before reindexing production, resetting checkpoints, changing mainnet starting blocks, changing deployed addresses, or modifying Railway/DNA token deployment assumptions.
