---
name: agent-vitals-cli-reports
description: >
  Use this skill when changing agent-vitals Commander CLI commands, source option behavior, terminal report output, Markdown report output, compare/health/changelog-facing command docs, or command validation. Keywords: src/index.ts, report, health, compare, annotate, impact, changes, TerminalReport, MarkdownReport.
---

# Agent Vitals CLI And Reports

## Purpose

Keep command behavior, provider filters, report formats, and documentation aligned for the Node CLI.

## When To Use

Use for `src/index.ts`, `src/reports/terminal.ts`, `src/reports/markdown.ts`, CLI command docs in `README.md` or `docs/index.html`, and behavior that changes command output.

## Repository Map

- `src/index.ts`: Commander command definitions and option validation.
- `src/reports/terminal.ts`: chalk report layout, sparklines, metric sections.
- `src/reports/markdown.ts`: GitHub-postable metric tables and regression summaries.
- `src/regression/detector.ts`: health status used by reports and `health`.
- `src/changes/tracker.ts`: backs `annotate`, `impact`, and `changes`.
- `package.json`: `bin`, scripts, package manager, Node engine.

## Standard Workflow

1. Read the command implementation and the relevant report class before editing output.
2. Preserve `--source claude|codex|all` semantics. CLI accepts `all`; internal queries use `_all`.
3. Keep dry-run behavior as the default for commands that can write, especially `prescribe`.
4. If adding or renaming a command or option, update README command tables and docs.
5. For user-visible Markdown output, avoid ANSI color helpers and terminal-only symbols that do not render cleanly in GitHub.

## Validation Commands

Run from the repository root:

```bash
bun run build
bun run check
node dist/index.js --help
node dist/index.js report --format md --db /tmp/agent-vitals-cli.db
node dist/index.js health --source codex --db /tmp/agent-vitals-cli.db
node dist/index.js compare 2026-01-01:2026-01-07 2026-01-08:2026-01-14 --db /tmp/agent-vitals-cli.db
```

Use a temp `--db` for command-shape validation. Use a populated DB only when validating actual metric values.

## Common Pitfalls

- Do not let invalid `--source` values silently fall back; `resolveReportProvider()` exits with a clear error.
- Keep `program.version()` in sync with `package.json` when release version changes.
- Avoid widening command writes during refactors; `scan` writes the vitals DB, `prescribe --apply` writes configs, and `dashboard` starts a server.
- Terminal reports strip ANSI for padding; avoid changing layout helpers without checking alignment.
- There is no test script in `package.json`; use `bun run build` and `bun run check` as the repo’s reliable gates.

## Escalation Rules

If a command output change affects examples or docs, update docs in the same task or explicitly call out the mismatch.
