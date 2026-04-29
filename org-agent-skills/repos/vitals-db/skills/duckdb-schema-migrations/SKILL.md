---
name: duckdb-schema-migrations
description: Use this skill when changing vitals-db DuckDB schema, ordered SQL migrations, database connection code, analytics tables, indexes, _migrations behavior, or migration tests in packages/db.
---

# DuckDB Schema Migrations

## Purpose

Make schema changes through ordered, idempotent DuckDB migrations while keeping ingest, queries, API DTOs, and tests synchronized.

## When To Use

- Adding, renaming, or removing analytics tables or columns.
- Adding indexes or internal bookkeeping tables.
- Changing `packages/db/src/connect.ts` or `packages/db/src/migrate.ts`.
- Debugging `_migrations` state, migration ordering, or schema drift.

## Inspect First

- Migration runner: `packages/db/src/migrate.ts`.
- DuckDB wrapper: `packages/db/src/connect.ts`.
- Current migrations: `packages/db/src/migrations/001_init.sql`, `002_sleep_raw_state.sql`, `003_performance_workout_context.sql`.
- Schema contract test: `packages/db/src/__tests__/migrate.test.ts`.
- Ingest writer target SQL: `packages/ingest/src/writer.ts`.
- Query modules and DTOs that read the affected table: `packages/queries/src/*`, `packages/core/src/dto.ts`.

## Standard Workflow

1. Pick the next zero-padded migration filename, for example `004_new_metric.sql`.
2. Make SQL idempotent where DuckDB supports it (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).
3. Keep `_migrations` untouched except through the migration runner.
4. Update `packages/db/src/__tests__/migrate.test.ts` expected tables, columns, indexes, and applied migration ids.
5. Update downstream insert SQL in `packages/ingest/src/writer.ts` before any mapped row targets a new column or table.
6. Update query modules, DTO schemas, server routes, docs, and frontend fetchers only when the schema change affects public behavior.
7. Run migration tests before broader tests.

## Invariants

- Migrations are applied in sorted filename order.
- Each migration runs in a transaction and records its id only after SQL succeeds.
- Re-running `migrate(db)` must apply no new migrations after the first run.
- Migration state must persist across connections.
- Analytics tables currently include `workouts`, `heart_rate`, `resting_hr`, `hrv`, `walking_hr`, `steps`, `distance`, `energy`, `sleep`, `performance`, and workout context tables.
- Internal tables are `_migrations`, `_ingest_state`, and `_ingest_seen`.
- `heart_rate.ts` has `hr_ts_idx`; workout context tables have workout-id indexes.

## Validation Commands

```bash
bun test packages/db/src/__tests__/migrate.test.ts
bun test packages/db/src/__tests__/connect.test.ts
```

If ingest or query code changed:

```bash
bun test packages/ingest/src/__tests__/ingest.test.ts
bun test packages/queries/src/__tests__
```

Final gate:

```bash
bun run verify
```

## Common Pitfalls

- Do not edit an already-applied migration to represent a new production change. Add a new migration.
- Keep the schema test in lockstep with migrations; it is the most precise schema contract in the repo.
- DuckDB returns JS `Date` objects for `TIMESTAMP` and `DATE`; query modules should convert with `toIsoDateTime` or `toIsoDate`.
- Schema changes can break `health rebuild`, which deletes all analytics tables listed in `apps/server/src/cli.ts`.
- New nullable columns can preserve old local DBs, but they cannot backfill source detail that was never stored. Document rebuild requirements when needed.

## Escalation

If a schema change requires data backfill beyond `ALTER TABLE ADD COLUMN`, state whether existing local DuckDB files must run `bun run health rebuild` and update user-facing docs accordingly.
