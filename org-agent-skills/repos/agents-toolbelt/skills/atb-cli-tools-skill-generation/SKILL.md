---
name: atb-cli-tools-skill-generation
description: Use this skill when changing agents-toolbelt generation of the `cli-tools` Agent Skill for Claude Code and Codex, including skill targets, default paths, generated SKILL.md content, category ordering, verified tool inventory, or `internal/skill` golden tests.
---

# ATB CLI Tools Skill Generation

## Purpose

Maintain the generated `cli-tools` skill that exposes verified local binaries to coding agents.

## When to use

Use for edits to `internal/skill`, skill target paths, generated `SKILL.md` frontmatter/body, category ordering, skill exposure rules, or runtime calls that refresh and persist skills.

## Inspect first

- `internal/skill/skill.go`
- `internal/skill/target.go`
- `internal/skill/testdata/golden_skill.md`
- `internal/skill/*_test.go`
- `cmd/atb/runtime.go` functions `persistVerifiedSkill` and `refreshVerifiedTools`
- `internal/catalog/catalog.go` for category labels

## Repository map

- `skill.Generate`: renders one minimal `cli-tools` skill from verified, exposable catalog tools.
- `skill.Write`: writes the same content to all requested target paths.
- `skill.Targets`: Claude Code path is `.claude/skills/cli-tools/SKILL.md`; Codex path is `.agents/skills/cli-tools/SKILL.md`.
- `refreshVerifiedTools`: verifies both managed and external installed tools before generation.

## Standard workflow

1. Keep generated skill frontmatter valid Agent Skills YAML with `name: cli-tools` and a concrete description.
2. Only include tools with `SkillExpose` set and successful verification.
3. Preserve category ordering from `categoryOrder`, with unknown categories sorted after known categories.
4. Update `internal/skill/testdata/golden_skill.md` whenever generated output intentionally changes.
5. Keep target selection behavior consistent with state: stored target IDs are reused; `none` means opt out.

## Validation commands

- Skill generator tests: `go test ./internal/skill`
- Runtime generation tests: `go test ./cmd/atb`
- Catalog category impact: `go test ./internal/catalog`
- Full gate: `make verify`

## Common pitfalls

- Do not write generated runtime skills directly during tests except under temp HOME paths.
- Do not list unverified or hidden tools just because they exist in the catalog.
- Default target paths are relative to the user's home directory, not the repository root.
- A generated inventory is intentionally minimal; avoid turning it into tool tutorials.

## Escalation

If changing generated skill format, validate the output against the Agent Skills spec and update the golden file in the same change.
