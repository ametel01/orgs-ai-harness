---
name: agent-vitals-session-ingestion
description: >
  Use this skill when changing agent-vitals session scanning, Claude or Codex log adapters, tool-call classification, JSONL parsing, source filters, SQLite ingestion, or provider metadata. Keywords: scan, ClaudeAdapter, CodexAdapter, ParsedSessionLog, tool_calls, thinking_blocks, read/edit/search/bash classification, --source.
---

# Agent Vitals Session Ingestion

## Purpose

Keep provider log parsing and database ingestion consistent across Claude Code and Codex sources.

## When To Use

Use for changes under `src/scanner/`, adapter registration in `src/index.ts`, ingestion methods in `src/db/database.ts`, or schema fields in `src/db/schema.ts`.

## Repository Map

- `src/scanner/types.ts`: provider-neutral parsed session contract.
- `src/scanner/claude-adapter.ts`: Claude JSONL shapes, pattern detection, Claude tool classification.
- `src/scanner/codex-adapter.ts`: Codex rollout parsing, shell command classification, `apply_patch` target extraction, reasoning token depth.
- `src/scanner/scanner.ts`: adapter discovery, skip/re-scan logic, transaction ingestion, idempotent session replacement.
- `src/db/schema.ts` and `src/db/database.ts`: persistent tables, migrations, insert helpers.
- `src/index.ts`: creates adapters and validates `--source claude|codex|all`.

## Standard Workflow

1. Read `src/scanner/types.ts` first so new fields stay provider-neutral.
2. Read the adapter being changed and `src/scanner/scanner.ts` before patching ingestion.
3. If adding a metric input, update the parsed type, both adapters when applicable, DB schema, migration path, insert helper, and ingestion transaction.
4. Keep `Scanner.scan()` idempotent: when source metadata changes, delete old session rows and reinsert all derived rows in one transaction.
5. Preserve provider segmentation. New persisted rows that belong to a session must remain joinable to `sessions.provider`; new daily metrics must be computed for `_all` and each concrete provider.

## Validation Commands

Run from the repository root:

```bash
bun run build
bun run check
node dist/index.js scan --source codex --db /tmp/agent-vitals-codex-test.db --verbose
node dist/index.js scan --source claude --db /tmp/agent-vitals-claude-test.db --verbose
```

If local Claude or Codex logs are unavailable, run `bun run build` and `bun run check`, then state that live scan validation was skipped because no source logs were present.

## Common Pitfalls

- Do not classify build or test commands as research just because they read files internally; they should stay `bash`.
- Codex shell reads are best-effort. `sed`, `cat`, `nl`, `head`, and similar simple commands may infer `targetFile`; compound commands or redirects should usually be ambiguous.
- Codex `apply_patch` is a mutation and should extract the first `*** Update/Add/Delete File:` target.
- Claude and Codex thinking-depth signals are not equivalent. Codex can use `reasoning_output_tokens`; Claude uses visible content or signature proxy.
- Avoid swallowing parse errors in ways that hide adapter regressions; existing adapters count parse errors and continue scanning.

## Escalation Rules

If a new provider format cannot be represented by `ParsedSessionLog` without provider-specific leakage, stop and adjust `src/scanner/types.ts` deliberately before touching metrics or reports.
