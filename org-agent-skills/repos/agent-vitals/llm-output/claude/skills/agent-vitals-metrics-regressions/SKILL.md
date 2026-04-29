---
name: agent-vitals-metrics-regressions
description: >
  Use this skill when adding or changing agent-vitals quality metrics, daily metric SQL, rolling regression thresholds, provider-scoped benchmarks, health status, compare output, or metric labels. Keywords: MetricsAnalyzer, daily_metrics, RegressionDetector, read_edit_ratio, blind_edit_rate, Claude-only metrics, provider _all.
---

# Agent Vitals Metrics And Regressions

## Purpose

Maintain the metric pipeline from ingested SQLite rows to daily metrics, health alerts, and benchmark-aware reports.

## When To Use

Use for changes in `src/metrics/analyzer.ts`, `src/regression/detector.ts`, metric lists in `src/reports/*`, dashboard metric expectations, and CLI `compare` metric output.

## Repository Map

- `SPEC.md`: source of truth for what metrics mean and why they exist.
- `src/metrics/analyzer.ts`: computes all daily metrics for `_all` and each provider in `sessions`.
- `src/regression/detector.ts`: compares rolling 7-day windows and returns green/yellow/red health.
- `src/reports/terminal.ts` and `src/reports/markdown.ts`: labels, formats, benchmarks, and provider-local benchmark suppression.
- `src/index.ts`: `health`, `compare`, and report command wiring.
- `src/db/database.ts`: metric query helpers such as `upsertDailyMetric`, `getDailyMetrics`, and date filters.

## Standard Workflow

1. Start from `SPEC.md` for the metric definition and threshold intent.
2. Inspect the source tables in `src/db/schema.ts` and existing query helpers before writing SQL.
3. Add or modify computation in `MetricsAnalyzer.computeAll()` and keep provider behavior: emit `_all`, then one row per concrete provider.
4. If a metric appears in user output, update terminal report, markdown report, CLI compare, dashboard expectations, and README/docs where relevant.
5. For Claude-calibrated metrics, keep non-Claude handling explicit. Existing Claude-only metrics are `thinking_depth_median`, `thinking_depth_redacted_pct`, `cost_estimate`, and `context_pressure`.

## Validation Commands

Run from the repository root:

```bash
bun run build
bun run check
node dist/index.js scan --source all --db /tmp/agent-vitals-metrics-test.db
node dist/index.js health --source all --db /tmp/agent-vitals-metrics-test.db
node dist/index.js report --source all --db /tmp/agent-vitals-metrics-test.db
```

If `/tmp/agent-vitals-metrics-test.db` has no local session data, the scan/report commands may be low-signal; still run build and check, and note the data gap.

## Common Pitfalls

- Do not apply Claude thresholds blindly to Codex metrics whose scale is provider-local.
- Avoid metric name drift. The same key must be used in analyzer, reports, regression detector, prescriptions, compare, dashboard, and docs.
- `daily_metrics` has a uniqueness constraint over date, metric, provider, model, and project path; preserve upsert semantics.
- Treat zero denominators deliberately. Existing code uses safe division and sometimes returns numerator when there are reads but no edits.
- Keep rolling regression detection separate from absolute benchmark status; reports and health answer different questions.

## Escalation Rules

If a metric cannot be validated from current stored rows, add the missing ingestion data first rather than deriving it from report output or text formatting.
