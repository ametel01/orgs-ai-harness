---
name: hono-api-routes
description: Use this skill when adding or changing vitals-db Hono API routes, server env handling, CLI serve behavior, query validation, HTTP error shapes, docs/API_CONTRACT.md, or frontend fetch functions that call apps/server endpoints.
---

# Hono API Routes

## Purpose

Expose query-layer behavior through the Hono server while preserving validation, error shapes, DTO contracts, and dashboard client parsing.

## When To Use

- Editing `apps/server/src/server.ts`, `apps/server/src/routes/metrics.ts`, `apps/server/src/routes/workouts.ts`, `apps/server/src/env.ts`, or `apps/server/src/cli.ts`.
- Adding HTTP endpoints for new query modules.
- Changing route query params, path params, error responses, or server environment variables.
- Wiring a new API call into `apps/web/lib/api.ts`.

## Inspect First

- App composition: `apps/server/src/server.ts`.
- Route patterns: `apps/server/src/routes/metrics.ts`, `apps/server/src/routes/workouts.ts`.
- Env parsing: `apps/server/src/env.ts`.
- CLI commands and serve/migrate flow: `apps/server/src/cli.ts`.
- Query exports: `packages/queries/src/index.ts`.
- DTO schemas: `packages/core/src/dto.ts`.
- Contract docs: `docs/API_CONTRACT.md`.
- Tests: `apps/server/src/__tests__/server.test.ts`, `apps/server/src/__tests__/fixture.test.ts`.
- Client parser: `apps/web/lib/api.ts`.

## Standard Workflow

1. Implement data behavior in `packages/queries` before adding the route.
2. Validate query params with Zod at the route boundary. Date inputs must accept valid `YYYY-MM-DD` or ISO datetimes with timezone offsets.
3. For `/metrics/*`, require both `from` and `to` unless matching an existing optional-list route.
4. Return `{ error: "invalid_query", issues }` with status 400 for bad query strings.
5. Return `{ error: "invalid_params", issues }` with status 400 for bad path params.
6. Return `{ error: "not_found" }` with status 404 when a workout-specific route references a missing workout.
7. Update `docs/API_CONTRACT.md` and `apps/web/lib/api.ts` if the endpoint is public or dashboard-facing.
8. Add route tests that parse the response using the exported Zod DTO schema.

## Invariants

- `createApp({ db })` should remain dependency-injected for tests.
- `health serve` opens the DB, runs migrations, creates the app, and serves on `PORT` default `8787`.
- `DB_PATH` defaults to `./vitals.duckdb`; `NODE_ENV` is `development`, `test`, or `production`.
- Route handlers should delegate analytics work to `@vitals/queries`; avoid embedding SQL in Hono route files.
- The server-wide error handler returns `{ error: "internal_error", message }` with status 500.
- Workout subroutes check that the workout exists before returning child data.

## Validation Commands

```bash
bun test apps/server/src/__tests__/server.test.ts
```

For fixture-backed end-to-end API coverage:

```bash
bun test apps/server/src/__tests__/fixture.test.ts
```

If web client fetchers changed:

```bash
bun run typecheck
bun run --filter @vitals/web build
```

Final gate:

```bash
bun run verify
```

## Common Pitfalls

- Zod `z.string().datetime()` alone does not accept date-only strings; use the existing date-only validation pattern.
- Query-string numbers arrive as strings; use `z.coerce.number()` where numeric params are accepted.
- Optional object properties must respect `exactOptionalPropertyTypes`; build params objects conditionally instead of assigning `undefined`.
- Adding a route without updating `apps/web/lib/api.ts` means the dashboard cannot schema-validate it.
- `docs/API_CONTRACT.md` is the human-readable public route index and should stay exact.

## Escalation

If a route needs authentication, streaming, CORS, or non-JSON behavior, stop and clarify scope; the current app is a local JSON API without those patterns.
