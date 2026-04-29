---
name: agent-vitals-packaging-installs
description: >
  Use this skill when changing agent-vitals package metadata, Bun/Node build workflow, install or uninstall scripts, generated Claude slash commands, auto-scan script, published files, or release/version notes. Keywords: package.json, bun.lock, dist/index.js, scripts/install.sh, scripts/uninstall.sh, scripts/auto-scan.sh, CHANGELOG.
---

# Agent Vitals Packaging And Installs

## Purpose

Preserve the repo’s Bun development workflow, Node runtime CLI, and shell-script installation behavior.

## When To Use

Use for `package.json`, `bun.lock`, shell scripts under `scripts/`, `SKILL.md` slash-command behavior, `CHANGELOG.md`, and release-facing docs.

## Repository Map

- `package.json`: Bun package manager, Node `bin`, build/check/format scripts, Node engine.
- `bun.lock`: dependency lockfile; do not replace with npm or pnpm locks.
- `scripts/install.sh`: builds and writes Claude command files under `~/.claude/commands`.
- `scripts/uninstall.sh`: removes generated Claude command files.
- `scripts/auto-scan.sh`: runs built CLI against the default vitals DB.
- `SKILL.md`: user-invocable `/vitals` behavior for Claude Code.
- `CHANGELOG.md`: release notes.

## Standard Workflow

1. Read `package.json`, the target script, and README/CLAUDE instructions before changing build or install behavior.
2. Use Bun for repo dependency workflows: `bun install`, `bun add`, `bun add -d`, `bun run build`, `bun run check`.
3. Preserve the runtime distinction: development uses Bun, but the built CLI runs with Node via `dist/index.js` and `#!/usr/bin/env node`.
4. When editing install scripts, keep generated command files deterministic and point them at the absolute built `dist/index.js`.
5. When changing user-facing command names, update `scripts/install.sh`, `scripts/uninstall.sh`, `SKILL.md`, README, and changelog/docs as needed.

## Validation Commands

Run from the repository root:

```bash
bun install --frozen-lockfile
bun run build
bun run check
node dist/index.js --version
node dist/index.js health --db /tmp/agent-vitals-package.db
```

For install-script changes, validate in a temporary HOME:

```bash
HOME=/tmp/agent-vitals-install-home bash scripts/install.sh
find /tmp/agent-vitals-install-home/.claude/commands -maxdepth 1 -type f -name 'vitals*.md' -print
HOME=/tmp/agent-vitals-install-home bash scripts/uninstall.sh
```

## Common Pitfalls

- Do not create `package-lock.json`, `pnpm-lock.yaml`, or `yarn.lock`.
- The repo currently has no `test` script; do not report `npm test` or `bun test` as a real validation gate unless tests are added.
- `scripts/install.sh` may lag package-manager guidance. If touching it, inspect current behavior and align deliberately rather than mixing package managers accidentally.
- `package.json.files` publishes `dist/**/*`; source-only assets needed at runtime must either be copied into `dist` or resolved with a fallback.
- Shell scripts should stay non-interactive and safe for repeated runs.

## Escalation Rules

If a packaging change affects the published CLI entrypoint or install locations, validate with a temp HOME and document exactly which files are written.
