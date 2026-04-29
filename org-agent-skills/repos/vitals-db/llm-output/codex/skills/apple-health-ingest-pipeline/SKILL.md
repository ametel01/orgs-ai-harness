---
name: apple-health-ingest-pipeline
description: Use this skill when modifying vitals-db Apple Health XML ingest, including HK identifiers, saxes streaming parser behavior, crop/rebuild, mappers, unit normalization, UTC timestamps, _ingest_seen deduplication, fixtures, and ingest tests.
---

# Apple Health Ingest Pipeline

## Purpose

Change Apple Health export ingestion without breaking the streaming parser, UTC normalization, incremental import, deduplication, or analytics table shape.

## When To Use

- Adding support for a new `HKQuantityTypeIdentifier*`, `HKCategoryTypeIdentifier*`, workout child node, unit, or sleep state.
- Changing `health ingest`, `health crop`, or `health rebuild`.
- Editing fixture XML or ingest tests.
- Debugging duplicate rows, missing rows, time-zone shifts, crop behavior, or unsupported Apple Health records.

## Inspect First

- Supported identifiers: `packages/core/src/identifiers.ts`.
- Sleep state normalization: `packages/core/src/sleep.ts`.
- Parser: `packages/ingest/src/parser.ts`.
- Mapping and unit conversion: `packages/ingest/src/mappers.ts`.
- Writer/dedup: `packages/ingest/src/writer.ts`.
- Incremental state and crop path: `packages/ingest/src/incremental.ts`, `packages/ingest/src/cleanup.ts`.
- Schema targets: `packages/db/src/migrations/*.sql`.
- CLI entrypoint: `apps/server/src/cli.ts`.
- Tests: `packages/ingest/src/__tests__/*`, `apps/server/src/__tests__/fixture.test.ts`, `fixtures/sample.xml`.

## Standard Workflow

1. Start from the identifier and target table. If a new HealthKit type is supported, add it to `packages/core/src/identifiers.ts`.
2. Extend the parser only if the XML node or child attributes are not already captured.
3. Map values in `mappers.ts` into existing analytics tables or add a database migration first when a new column/table is required.
4. Keep timestamps normalized through `hkDateToMs`, `formatDuckTs`, and `parseHKDate`; analytics tables should remain UTC wall-clock DuckDB timestamps.
5. Preserve sparse measurement rows in `performance` and `energy`; do not combine unrelated source samples into one row unless the schema changes intentionally.
6. Add or update parser, mapper, ingest integration, migration, and API fixture tests according to the affected surface.
7. If fixture XML changes, keep `fixtures/sample.xml` useful for the MVP API surfaces and preserve the test expectation that `Record` nodes appear before `Workout` nodes.

## Invariants

- Manual samples with `MetadataEntry key="HKWasUserEntered" value="1"` are skipped.
- Unsupported Apple Health record types are ignored.
- Incremental imports apply a 24-hour lookback buffer around `last_import_ts`.
- Incremental imports crop to supported nodes before parsing a repeat import window.
- `_ingest_seen` deduplicates repeated imports using `dedup_key`; repeated imports must not create duplicate analytics rows.
- `last_import_ts` can advance when a newer duplicate row is processed.
- `last_import_file` stores the absolute source file path for `health rebuild`.
- Workout ids are stable SHA-1 values derived from raw workout type and start/end timestamps.
- Sleep ingest stores normalized `state` plus nullable `raw_state` for Core/Deep/REM/Unspecified detail.

## Validation Commands

Use narrow tests first:

```bash
bun test packages/ingest/src/__tests__/parser.test.ts
bun test packages/ingest/src/__tests__/mappers.test.ts
bun test packages/ingest/src/__tests__/ingest.test.ts
bun test packages/ingest/src/__tests__/cleanup.test.ts
```

For fixture/API impact:

```bash
bun test apps/server/src/__tests__/fixture.test.ts
bun test apps/server/src/__tests__/server.test.ts
```

Final gate for broad changes:

```bash
bun run verify
```

## Common Pitfalls

- Apple dates look like `YYYY-MM-DD HH:MM:SS ±HHMM`; parse the offset, do not drop it.
- DuckDB `TIMESTAMP` has no zone; this repo stores UTC-normalized wall-clock strings.
- Unknown or malformed numeric values should usually produce `null` mappings, not `NaN`.
- Unknown units should not silently pass through as if they were SI units.
- Workout child tables (`workout_stats`, `workout_events`, `workout_metadata`, `workout_routes`) must stay tied to the stable workout id.
- `workouts` insert uses `ON CONFLICT DO NOTHING RETURNING id`; duplicate workouts count as skipped rather than aborting ingest.

## Escalation

If Apple Health exports contain a type or unit not represented in tests, add a minimal XML fixture and document the intended normalized unit in the test before changing query or API behavior.
