---
name: release-changelog-workflow
description: Use this skill when preparing releases, version bumps, changelog entries, GitHub tag workflows, GoReleaser changes, generated release notes, or release-gate summaries in ametel01 repos. It detects manual changelog versus tag-driven release variants.
---

# Release Changelog Workflow

## Purpose

Keep release work aligned with each repo's observed release mechanism instead of imposing one universal process.

## When to use

Use when editing `CHANGELOG.md`, version fields, release workflows, installer scripts, GoReleaser config, package metadata, or docs that describe shipped behavior.

## Evidence from repositories

Strength: Moderate.

- `agents-toolbelt`: `CHANGELOG.md` follows Keep a Changelog and SemVer; `.github/workflows/release.yml` runs GoReleaser on `v*` tags with checksums, cosign, and provenance.
- `vitals-db`: `CHANGELOG.md` documents SemVer releases with release-gate commands and release links.
- `agent-vitals`: `CHANGELOG.md` uses version sections and user-facing change categories.
- `horizon-starknet`: `.github/workflows/release.yml` creates GitHub releases from `v*` tags with generated notes and prerelease detection.

## Standard workflow

1. Detect the repo's release variant from `CHANGELOG.md`, version fields, and `.github/workflows/release.yml`.
2. Update changelogs only when the repo already keeps one or the user asks.
3. Keep release notes user-facing: Added, Changed, Fixed, Removed, security, release gate, and migration notes where relevant.
4. Include validation commands actually run for the release candidate when the repo's changelog uses release gates.
5. For tag-driven workflows, do not create tags or releases unless the user explicitly asks.

## Variant table

| Variant | Repos | Guidance |
|---|---|---|
| Manual changelog plus SemVer | `agents-toolbelt`, `vitals-db` | Preserve Keep a Changelog-style headings and release links when present |
| Simple version changelog | `agent-vitals` | Match existing concise version sections |
| Generated GitHub release notes | `horizon-starknet` | Keep workflow tag behavior; changelog may not be canonical |
| No clear release process | `orgs-ai-harness` | Do not invent a changelog or release workflow without explicit request |

## Required checks

- Confirm version numbers and dates are consistent.
- Keep tag pattern `v*` workflows intact unless deliberately changing release behavior.
- If touching GoReleaser or install scripts, run or explain release-specific validation.
- Link changelog entries to actual changed behavior, not internal implementation churn.

## Common pitfalls

- Do not assume every repo uses Keep a Changelog, even though several do.
- Do not use generated release notes as a substitute for manual changelog entries in repos that maintain them.
- Do not claim release validation commands ran unless they did.

## Escalation / uncertainty rules

If release evidence is weak, phrase changes as draft release notes and ask before tagging, publishing, or modifying release automation.
