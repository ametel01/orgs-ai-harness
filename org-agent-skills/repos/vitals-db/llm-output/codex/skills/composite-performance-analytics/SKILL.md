---
name: composite-performance-analytics
description: Use this skill when changing vitals-db composite analytics, performance reports, readiness, training strain, aerobic efficiency, run economy, fatigue flags, load quality, recovery debt, consistency index, or paid-report answer/evidence/action logic.
---

# Composite Performance Analytics

## Purpose

Maintain the higher-level performance report layer that turns raw health signals into conservative answer/evidence/action recommendations.

## When To Use

- Editing files such as `advanced_report.ts`, `readiness.ts`, `training_strain.ts`, `aerobic_efficiency_trend.ts`, `fitness_trend.ts`, `run_fatigue.ts`, `load_quality.ts`, `recovery_debt.ts`, `run_economy.ts`, or `consistency_index.ts`.
- Changing composite DTOs, action kinds, confidence, sample quality, claim strength, or report section ordering.
- Updating `/metrics/composites/*` routes or the `/performance` dashboard report panel.

## Inspect First

- Product intent: `docs/ADVANCED_COMPOSITE_ANALYTICS.md`.
- Composite DTOs: `packages/core/src/dto.ts`.
- Window helpers: `packages/queries/src/composite_windows.ts`.
- Sample quality: `packages/queries/src/sample_quality.ts`.
- Report assembly: `packages/queries/src/advanced_report.ts`.
- Tests: `packages/queries/src/__tests__/advanced_report.test.ts` plus the specific composite test.
- API contract: `docs/API_CONTRACT.md`.
- Frontend use: `apps/web/app/performance/page.tsx`, `apps/web/lib/api.ts`.

## Standard Workflow

1. Keep the report shape as answer, 2-4 evidence items, action, confidence, sample quality, and claim strength.
2. Use `CompositeResultSchema.parse` or the relevant DTO schema for every returned composite result.
3. Use `buildCompositeWindows(range)` for current, baseline, acute, chronic, and 12-week windows instead of recreating ad hoc date math.
4. Make claims conservative. Prefer `suggests`, `worth_watching`, or low confidence when samples are sparse or mixed.
5. Preserve the four ordered report sections in `AdvancedCompositeReport`: `fitness_direction`, `easy_run_quality`, `recovery_state`, `workout_diagnoses`.
6. Select top-level recommendations by conservative severity, then confidence/sample-quality tie-breakers as in `advanced_report.ts`.
7. Add tests that seed enough signal to prove the classification and the chosen action.

## Invariants

- Composite evidence arrays must have 1-4 items and every evidence item needs label, value, and detail.
- Actions must use the enumerated kinds: `push`, `maintain`, `reduce_intensity`, `add_sleep`, `run_easier`, `retest`, `watch`.
- Sample quality must downgrade when core inputs are missing, misaligned, too short, or not route-backed where relevant.
- Run fatigue flags are ordered newest first and diagnose runs as clean aerobic, cardiac drift, under recovered, pacing fade, or poor sample quality.
- Decoupling is null for runs under 45 minutes or missing aligned HR/speed samples.
- Fixed-HR pace defaults to 120-130 bpm and should not imply fitness changes without enough aligned samples.

## Validation Commands

Run the specific composite test first:

```bash
bun test packages/queries/src/__tests__/advanced_report.test.ts
bun test packages/queries/src/__tests__/readiness.test.ts
bun test packages/queries/src/__tests__/run_fatigue.test.ts
```

For API/frontend exposure:

```bash
bun test apps/server/src/__tests__/server.test.ts
bun run typecheck
```

Final gate:

```bash
bun run verify
```

## Common Pitfalls

- Do not overclaim causality. The Apple Health data can suggest fatigue, recovery, or pacing issues, but many causes are external.
- Do not return empty evidence to make a report pass; use a low-confidence retest/watch result when inputs are missing.
- Avoid adding a chart-only metric to the composite report without an actionable interpretation.
- Keep composite routes additive; do not break existing raw metric contracts to support report logic.
- If frontend copy changes, keep it concise and tied to the DTO evidence rather than duplicating business logic in React.

## Escalation

If a new composite requires data that is not ingested yet, split the work: first add ingestion/schema/query coverage, then add the composite after the raw signal is testable.
