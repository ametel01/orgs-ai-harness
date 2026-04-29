---
name: agent-vitals-prescriptions
description: >
  Use this skill when changing agent-vitals baseline recommendations, prescribe diagnostics, Claude settings writers, Codex config/AGENTS.md writers, known-fix thresholds, legacy .rules cleanup, or impact records. Keywords: prescribe, baseline, Prescriber, CODEX_KNOWN_FIXES, KNOWN_FIXES, AGENTS.md, config.toml, settings.json.
---

# Agent Vitals Prescriptions

## Purpose

Safely generate and apply source-specific quality prescriptions without corrupting user agent configuration.

## When To Use

Use for `src/prescriptions/*`, `prescribe` or `baseline` command behavior in `src/index.ts`, change tracking of prescription applies, and docs describing `--apply`.

## Repository Map

- `src/prescriptions/known-fixes.ts`: Claude thresholds and fix templates.
- `src/prescriptions/codex-known-fixes.ts`: Codex baselines and known fixes.
- `src/prescriptions/prescriber.ts`: diagnosis, rendering helpers, safe config writers.
- `src/changes/tracker.ts`: records config changes and computes impact.
- `src/index.ts`: terminal, JSON, Markdown, dry-run, and apply flows.
- `README.md` and `docs/index.html`: user-facing command behavior.

## Standard Workflow

1. Read the relevant known-fixes file and `Prescriber.diagnose()` before changing thresholds or prescription types.
2. Keep prescription types aligned with writer support: `env_var`, `settings_json`, `permissions`, `claude_md`, `codex_config_toml`, `codex_rules`, and `project_instructions`.
3. For Codex prose, write to `~/.codex/AGENTS.md` or project `AGENTS.md`; do not write freeform prose into `.rules` files because Codex parses `.rules` as Starlark.
4. Preserve managed-block replacement markers:
   - Claude: `<!-- agent-vitals prescriptions -->`
   - Codex AGENTS: `<!-- agent-vitals codex prescriptions -->`
   - Codex TOML: `# agent-vitals managed start`
5. Keep `--target project` behavior narrow. For Codex it should write project `AGENTS.md` only; for Claude it writes project-local `.claude`/`CLAUDE.md`.

## Validation Commands

Use throwaway home and project paths when validating `--apply` behavior:

```bash
bun run build
bun run check
HOME=/tmp/agent-vitals-home node dist/index.js baseline --source codex --apply --db /tmp/agent-vitals-prescribe.db
HOME=/tmp/agent-vitals-home node dist/index.js prescribe --source codex --format json --db /tmp/agent-vitals-prescribe.db
```

Also inspect generated files under `/tmp/agent-vitals-home/.codex/` when an apply path is touched.

## Common Pitfalls

- Do not write to the real `~/.claude` or `~/.codex` during validation unless the user explicitly asks.
- Deduplicate repeated prescription values; existing writers use sets to avoid repeated rules.
- Preserve user-owned config outside managed blocks.
- `writeCodexConfigToml()` only manages root-level `key = value` lines; table-scoped keys must remain untouched.
- When no prescriptions exist, `--apply` still records a no-prescriptions event for the selected provider when possible.

## Escalation Rules

If a new fix requires editing a config format without a managed-block or merge strategy, implement the writer first and validate on a temporary HOME before enabling it in known fixes.
