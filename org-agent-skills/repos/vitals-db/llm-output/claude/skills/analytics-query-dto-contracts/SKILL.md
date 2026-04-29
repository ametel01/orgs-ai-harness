---
name: analytics-query-dto-contracts
description: Use this skill when adding or changing vitals-db analytics query modules, DateRange handling, DTO/Zod schemas in @vitals/core, query tests, or API response contracts for metrics such as sleep, workouts, HRV, zones, load, energy, running dynamics, and daily aggregates.
---

# Analytics Query DTO Contracts

## Purpose

Keep analytics SQL, Zod DTOs, API responses, and frontend parsing aligned. Query modules should return DTO-validated JSON-safe values, not raw DuckDB row shapes.

## When To Use

- Adding a metric under `packages/queries/src`.
- Changing date window behavior, daily/weekly buckets, nullability, units, or response fields.
- Editing `packages/core/src/dto.ts`, `packages/core/src/zones.ts`, or `packages/core/src/sleep.ts`.
- Updating `docs/API_CONTRACT.md` for metric behavior.

## Inspect First

- DTO schemas and exported types: `packages/core/src/dto.ts`.
- Shared date helpers: `packages/queries/src/dates.ts`.
- Query export barrel: `packages/queries/src/index.ts`.
- Existing query module with similar shape, for example `steps.ts`, `energy.ts`, `sleep_nights.ts`, `zones.ts`, or `workouts.ts`.
- Tests under `packages/queries/src/__tests__` and the seed helper.
- Server routes in `apps/server/src/routes/*` if public API behavior changes.
- Web fetch parser in `apps/web/lib/api.ts` if the dashboard consumes the response.

## Standard Workflow

1. Define or update the DTO schema in `packages/core/src/dto.ts` first.
2. Implement the query so each returned row is parsed by the DTO schema before returning.
3. Use parameterized SQL with `?` placeholders and `SqlValue[]` values; avoid interpolating user input.
4. Use `normalizeRangeStart(range.from)` and `normalizeRangeEnd(range.to)` for date-bounded metric windows.
5. Convert DuckDB `DATE` and `TIMESTAMP` values to strings via `toIsoDate` and `toIsoDateTime`.
6. Export the query from `packages/queries/src/index.ts`.
7. Add focused tests in `packages/queries/src/__tests__` using the existing `makeFixtureDb` and seed helpers.
8. If exposed over HTTP, update route tests, `docs/API_CONTRACT.md`, and `apps/web/lib/api.ts`.

## Invariants

- Date-only upper bounds are inclusive full UTC days by using `< next_day 00:00:00`.
- Datetime upper bounds use `<=` exactly as supplied.
- The repo treats analytics buckets as UTC days/weeks.
- Missing data should use `null` where a metric is undefined and `[]` where no rows exist, matching existing DTOs.
- Ratios must be finite numbers between 0 and 1 unless the DTO explicitly models an already-scaled percent.
- `performance` and `energy` are sparse-source tables; filter `IS NOT NULL` or aggregate columns independently.
- HR zone order is `Z1..Z5`; Z2 is pinned at 115-125 bpm.
- Sleep night grouping uses `DATE(start_ts - INTERVAL 12 HOUR)` so post-midnight sleep stays attached to the bedtime date.

## Validation Commands

Run the affected query test first, for example:

```bash
bun test packages/queries/src/__tests__/sleep_nights.test.ts
bun test packages/queries/src/__tests__/zones.test.ts
```

For DTO changes:

```bash
bun test packages/core/src/__tests__/dto.test.ts
bun test apps/server/src/__tests__/server.test.ts
```

Final gate:

```bash
bun run verify
```

## Common Pitfalls

- Returning raw `Date` objects will fail downstream schema/API expectations; convert them.
- `0` is not the same as no data. Use nullable fields for undefined metrics like no HR samples.
- Do not duplicate date parsing logic in every query; use the shared helpers.
- Keep route docs exact. `docs/API_CONTRACT.md` names DTOs and error shapes used by clients.
- If a query changes a response shape, update frontend Zod parsing in `apps/web/lib/api.ts` or the web page may show a schema error.

## Escalation

When changing semantics that affect historical interpretation, such as sleep night keys, sparse performance aggregation, or zone bounds, call out the behavioral change in tests and docs before broadening the implementation.
