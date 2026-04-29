---
name: next-dashboard-pages
description: Use this skill when modifying the vitals-db Next.js dashboard under apps/web, including App Router pages, server component data fetching, ECharts chart components, format helpers, API client usage, dashboard, sleep, performance, workouts, and Next 16 behavior.
---

# Next Dashboard Pages

## Purpose

Change the dashboard UI while preserving the repo's server-component data flow, Zod-validated API client, UTC formatting, and current Next 16 constraints.

## When To Use

- Editing files under `apps/web/app`, `apps/web/components`, or `apps/web/lib`.
- Adding dashboard charts, cards, filters, routes, or API client functions.
- Debugging frontend schema errors, build failures, or stale dashboard data.

## Inspect First

- Local instruction: `apps/web/AGENTS.md`. It warns that this is Next 16; read relevant local docs under `node_modules/next/dist/docs/` before relying on older Next assumptions.
- API client and schemas: `apps/web/lib/api.ts`.
- Formatting helpers: `apps/web/lib/format.ts`.
- Layout/nav/styles: `apps/web/app/layout.tsx`, `apps/web/components/SidebarNav.tsx`, `apps/web/app/globals.css`.
- Chart wrappers: `apps/web/components/charts/LineChart.tsx`, `apps/web/components/charts/StackedBar.tsx`.
- Existing pages: `apps/web/app/(dashboard)/page.tsx`, `apps/web/app/performance/page.tsx`, `apps/web/app/sleep/page.tsx`, `apps/web/app/workouts/page.tsx`, `apps/web/app/workouts/[id]/page.tsx`.
- Backend contract: `docs/API_CONTRACT.md`.

## Standard Workflow

1. Keep pages that read live health data as dynamic server-rendered pages with `export const dynamic = "force-dynamic"`.
2. Fetch through `apps/web/lib/api.ts`; do not call `fetch` directly from pages unless adding a reusable client wrapper.
3. Validate every API response with the matching `@vitals/core` Zod schema in `api.ts`.
4. Model fetch failures with `FetchResult<T>` and render `ErrorBanner`, empty states, or `notFound()` as appropriate.
5. Use `todayIso()` and `windowStartIso()` for UTC date windows and format output with `format.ts`.
6. Keep ECharts usage inside `"use client"` chart components. Pass stable keys with `chartDataKey` when chart data changes.
7. Preserve the existing shell, sidebar, card, tag, grid, and chart styling unless the task explicitly asks for a redesign.

## Invariants

- `VITALS_API_URL` defaults to `http://localhost:8787`.
- Server components receive `params` and `searchParams` as promises in this codebase.
- `requestJson` uses `cache: "no-store"` to avoid stale dashboard data.
- `WorkoutDetailPage` maps 404 detail responses to `notFound()`.
- Time and date labels are displayed in UTC for stable output.
- The performance page composes many endpoint calls; keep expensive per-workout detail fetches bounded by `RUN_LIMIT`.
- Chart components must dispose ECharts instances on unmount and resize on window resize.

## Validation Commands

For formatting helpers:

```bash
bun test apps/web/lib/__tests__/format.test.ts
```

For TypeScript and build behavior:

```bash
bun run typecheck
bun run --filter @vitals/web build
```

For API contract changes feeding the UI:

```bash
bun test apps/server/src/__tests__/server.test.ts
```

Final gate:

```bash
bun run verify
```

## Common Pitfalls

- Do not trust `apps/web/README.md` for this repo's workflow; it is scaffold text.
- Adding a backend endpoint without adding a schema-validated function in `apps/web/lib/api.ts` leads to duplicated fetch logic and weaker error handling.
- Passing unbounded data into ECharts can make the performance page slow; follow existing window constants and limits.
- Avoid local-time formatting; use UTC formatting helpers so tests and screenshots are stable across time zones.
- With `exactOptionalPropertyTypes`, avoid passing object keys with `undefined` values.

## Escalation

If a Next API appears inconsistent with training data, read the local Next 16 docs in `node_modules/next/dist/docs/` for that feature and cite the local behavior in your final note.
