---
name: agent-vitals-dashboard-docs
description: >
  Use this skill when editing the agent-vitals single-file dashboard, dashboard HTTP API, docs site, README metric explanations, source selectors, date filters, chart data, or user-facing dashboard documentation. Keywords: dashboard.html, serveDashboard, React CDN, Recharts, /api/metrics, docs/index.html.
---

# Agent Vitals Dashboard And Docs

## Purpose

Keep the static dashboard, dashboard API, and user-facing docs synchronized with CLI and metric behavior.

## When To Use

Use for `src/dashboard/server.ts`, `src/dashboard/dashboard.html`, `docs/index.html`, README dashboard/docs sections, and any metric or source selector displayed in the browser UI.

## Repository Map

- `src/dashboard/server.ts`: local HTTP server and JSON API routes.
- `src/dashboard/dashboard.html`: single-file React dashboard loaded from CDNs; no frontend build step.
- `src/db/database.ts`: dashboard query helpers and date filters.
- `src/regression/detector.ts`: `/api/health` response.
- `docs/index.html`: published documentation site, separate from runtime dashboard.
- `README.md`: concise command overview and links.

## Standard Workflow

1. Read both `server.ts` and `dashboard.html` before changing dashboard data contracts.
2. Keep API route outputs JSON-only except `/` and `/index.html`.
3. Preserve source normalization: URL `source=all` maps to provider `_all`; `claude` and `codex` remain concrete providers.
4. Keep date filters in `YYYY-MM-DD` format. Invalid dates should be ignored rather than passed into SQL.
5. For dashboard UI changes, edit `src/dashboard/dashboard.html` directly. It is intentionally a single HTML file with React, PropTypes, Recharts, and Babel from CDN.
6. If metrics or commands change, update `docs/index.html` and README only with supported behavior from source.

## Validation Commands

Run from the repository root:

```bash
bun run build
bun run check
node dist/index.js dashboard --source all --port 7847 --db /tmp/agent-vitals-dashboard.db
```

Then check these URLs while the server is running:

```text
http://localhost:7847/
http://localhost:7847/api/sessions?source=all
http://localhost:7847/api/metrics?source=codex
http://localhost:7847/api/health?source=claude
```

Stop the dashboard process before finishing.

## Common Pitfalls

- `dashboard.html` is copied/resolved at runtime from either `dist/dashboard/dashboard.html` or `src/dashboard/dashboard.html`; keep build output expectations in mind.
- Recharts UMD expects PropTypes as a global dependency; do not remove the PropTypes CDN script unless the chart library changes.
- Do not introduce a separate frontend package manager or build pipeline for the dashboard.
- The docs site in `docs/index.html` is not the runtime dashboard; update the right file for the task.
- Browser validation needs populated metrics to prove chart content, but API route shape can be checked with an empty temp DB.

## Escalation Rules

If a dashboard change needs new database shape, implement and validate the DB/query layer first, then update API and UI together.
