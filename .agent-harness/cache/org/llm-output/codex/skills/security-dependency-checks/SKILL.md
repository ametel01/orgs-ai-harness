---
name: security-dependency-checks
description: Use this skill when changing dependencies, install/update scripts, secret handling, GitHub Actions security, vulnerability scanning, checksums, provenance, Dependabot, TruffleHog, govulncheck, or frontend script-safety rules in ametel01 repos. Guidance is conditional where evidence is repo-specific.
---

# Security Dependency Checks

## Purpose

Preserve the security gates already present in the org and detect when a repo has stronger local requirements.

## When to use

Use for dependency updates, CI changes, installer/update logic, secret handling, release artifacts, frontend script loading, and vulnerability or secret-scanning workflows.

## Evidence from repositories

Strength: Moderate.

- `horizon-starknet`: `frontend-ci.yml` runs TruffleHog with `--only-verified`; `dependabot.yml` covers frontend, indexer, and GitHub Actions; frontend Biome blocks direct `next/script` imports in favor of `SecureScript`.
- `agents-toolbelt`: `Makefile` includes `govulncheck`; `CHANGELOG.md` records checksum verification, cosign signing, SLSA provenance, and fail-closed update behavior.
- `agents-toolbelt/CONTRIBUTING.md`: forbids unsafe `sh -c` command execution for install/update/uninstall actions and silent shell rc mutation.
- `vitals-db` and `agent-vitals`: no dedicated security workflow observed; apply only repo-local scripts and dependency hygiene there.

## Standard workflow

1. Inspect workflows, Dependabot config, installer scripts, and package manager lockfiles before changing dependencies or security-sensitive code.
2. Preserve lockfiles and frozen install behavior.
3. Keep secret-scanning and vulnerability checks wired into existing gates.
4. For install/update tools, use structured command arguments and explicit ownership checks.
5. For frontend code, follow local import restrictions and avoid bypassing script-safety wrappers.

## Commands

- `agents-toolbelt`: `make vulncheck` or full `make verify`.
- `horizon-starknet/packages/frontend`: CI includes TruffleHog in the workflow; local code validation uses package scripts.
- Dependency updates: use the repo's lockfile-owning package manager and inspect generated diffs.

## Required checks

- Never commit `.env`, private keys, tokens, local configs, or generated secrets.
- Do not weaken checksum, signature, provenance, or fail-closed behavior in installer/update paths.
- Do not bypass lints by loosening Biome, Go, or workflow security rules unless the reason is concrete and scoped.
- Keep dependency PR grouping and labels consistent when editing Dependabot.

## Common pitfalls

- Do not assume every repo has the same security gate; the strongest observed gates are in `horizon-starknet` and `agents-toolbelt`.
- Do not run arbitrary shell strings in tool management code where structured args are expected.
- Do not replace keyless signing, checksums, or provenance with undocumented manual steps.

## Escalation / uncertainty rules

If a requested change reduces a security gate, call it out explicitly and ask for confirmation unless the user already made the risk tradeoff clear.
