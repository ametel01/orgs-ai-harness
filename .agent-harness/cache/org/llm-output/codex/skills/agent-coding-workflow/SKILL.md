---
name: agent-coding-workflow
description: Use this skill when an AI coding agent edits ametel01 repos, especially with dirty worktrees, generated skills, CLI harness code, or multi-file changes. It encodes read-before-edit, scoped diffs, command verification, validation-before-completion, and user-change preservation habits observed across the org.
---

# Agent Coding Workflow

## Purpose

Make agent edits deliberate, scoped, and verifiable across the org's repos.

## When to use

Use before making code or generated-skill edits, when working in a dirty tree, when commands fail, or when the task spans multiple files or repos.

## Evidence from repositories

Strength: Moderate.

- `agent-vitals/AGENTS.md`: requires finishing requested parts and validating before completion.
- Current org pack instructions for `org-agent-skills`: require reading target file and a related caller/test/type before edits, verifying cwd and command syntax, and adapting after failures.
- `agents-toolbelt/CONTRIBUTING.md`: asks for focused changes, full quality gates, docs for behavior/workflow changes, and logical commits.
- `orgs-ai-harness/tests/test_org_pack_foundation.py`: validates generated pack and skill structure, installation targets, and no unintended global writes in tests.

## Standard workflow

1. Inspect git status and treat existing changes as user-owned.
2. Read the target file before editing. Also read at least one related caller, test, type definition, workflow, or validator.
3. Keep edits scoped to the requested behavior and surrounding conventions.
4. Before shell commands, confirm `cwd` and command syntax. After a failure, read the full error and change approach.
5. Run relevant validation before reporting completion.
6. Report what changed, what validation ran, and any residual gap.

## Required checks

- Do not revert or overwrite unrelated user changes.
- Do not write org-level skills directly into global runtime install targets; use staging or repo paths requested by the task.
- Prefer `rg`/`rg --files` for repo inspection.
- Use existing scripts, validators, and tests rather than inventing one-off checks.

## Common pitfalls

- Blind edits in unfamiliar files.
- Retrying a failing command without changing the approach.
- Reporting completion after only a subset of requested work.
- Mixing unrelated cleanup into a focused change.
- Treating generated output as installed runtime state when the harness expects staged output.

## Escalation / uncertainty rules

If user-owned changes conflict with the requested edit, stop and explain the conflict. If validation cannot run because a tool is missing or a service is unavailable, state exactly which check was skipped and why.
