---
name: documentation-update-standards
description: Use this skill when changing README content, docs directories, API contracts, CLI behavior, developer workflows, deployment notes, changelogs, or user-facing behavior in ametel01 repos. It explains how to keep docs aligned with code and CI without inventing unsupported policies.
---

# Documentation Update Standards

## Purpose

Keep user-facing and contributor docs synchronized with actual commands, APIs, workflows, and release behavior.

## When to use

Use when changes affect CLI commands, APIs, dashboards, ingest/indexer behavior, contracts, deployment, CI/developer workflow, release notes, or setup instructions.

## Evidence from repositories

Strength: Strong.

- `vitals-db`: detailed `README.md`, `docs/API_CONTRACT.md`, and changelog entries updated with API, ingest, and UI behavior.
- `horizon-starknet`: broad `docs/**` for contracts, indexer/frontend integration, deployment, events, Sentry, gap analyses, and plans.
- `agents-toolbelt`: `README.md`, `CONTRIBUTING.md`, and `CHANGELOG.md` describe install behavior, local gates, release packaging, and safety rules.
- `agent-vitals`: `README.md`, `docs/index.html`, and `CHANGELOG.md` document CLI commands, metrics, prescriptions, and provider differences.

## Standard workflow

1. Identify whether the change is user-facing, developer-facing, API-facing, or release-facing.
2. Update the nearest authoritative doc, not every doc.
3. Keep command examples executable and aligned with manifests/workflows.
4. Update changelogs when the repo already uses them and the change is functional or visible.
5. Preserve domain-specific contract docs such as API DTOs, event docs, deployment notes, and integration specs.

## Required checks

- For API shape changes, update contract docs and tests together.
- For workflow or quality gate changes, update README/CONTRIBUTING and CI scripts together.
- For deployment or environment changes, update env var names, ports, and setup commands.
- For UI changes, document behavior only where the repo already documents app surfaces or release gates.

## Common pitfalls

- Do not add generic docs that duplicate the code without helping users.
- Do not leave stale command examples after renaming scripts or Make targets.
- Do not create a changelog in repos without one unless asked.
- Do not treat planning documents as canonical if a README or API contract already owns the current behavior.

## Exceptions

For narrow internal refactors with no behavior, command, API, or workflow change, docs may be unnecessary. Mention that decision if the user expected documentation.
