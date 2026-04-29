---
name: test-workflow-standards
description: Use this skill when adding, fixing, or reviewing tests in ametel01 repos using Bun test, Vitest, Playwright, Go tests, Python unittest/pytest-style harness tests, or Cairo snforge tests. It captures shared expectations for regression coverage, deterministic fixtures, and CI-aligned test commands.
---

# Test Workflow Standards

## Purpose

Add tests that match the target repo's framework and validate behavior without brittle implementation coupling.

## When to use

Use when touching test files, fixing bugs, changing APIs, refactoring logic, adding CLI behavior, changing smart contracts, or modifying frontend/indexer flows.

## Evidence from repositories

Strength: Strong for testing culture; Moderate for exact patterns because frameworks vary.

- `vitals-db`: many `__tests__` across packages, `bun test`, changelog release gates naming test counts and commands.
- `horizon-starknet`: extensive Cairo contract tests, frontend unit/e2e tests, indexer Vitest tests, `docs/TEST_QUALITY_AUDIT.md`.
- `agents-toolbelt`: Go unit and integration tests across `internal/**`, `make test` includes normal and race tests.
- `orgs-ai-harness`: `tests/test_org_pack_foundation.py` validates CLI and pack behavior with deterministic temp directories.

## Standard workflow

1. Locate existing tests near the changed behavior and match their framework and naming style.
2. Prefer focused regression tests for bug fixes and pure-function tests for extracted logic.
3. Keep fixtures deterministic and local. Use temp directories, fakes, or controlled inputs instead of machine state.
4. Test public behavior first. Only inspect internal state when existing tests require it and document why.
5. Run the narrow test first, then the repo or package gate if the change has broader impact.

## Commands

- Bun packages: `bun test` or the package's `bun run test`.
- `horizon-starknet` frontend e2e: `bun run test:e2e --project=chromium`.
- `horizon-starknet` indexer: `bun run test` from `packages/indexer`.
- Cairo contracts: `cd contracts && snforge test`.
- Go CLI: `make test` or `make verify` for full validation.
- Python harness: run the relevant `unittest`/pytest-compatible tests with the repo's current Python setup.

## Required checks

- Add or update tests for new non-trivial behavior.
- Keep tests parallelizable unless they explicitly mutate process-global state.
- Make assertion failures actionable with expected behavior in the message or test name.
- Preserve exact boundary coverage for time, expiry, parsing, state, and CLI lifecycle changes.

## Common pitfalls

- Avoid broad lifecycle tests as the only coverage; keep one integration test if useful, but pin individual behaviors separately.
- Avoid approximate assertions unless the domain requires rounding tolerance; document the exact tolerance.
- Avoid hidden global state in contract and CLI tests.
- Do not rely on local databases, shell rc files, installed tools, or network services unless the test is explicitly integration-scoped.

## Exceptions

When a repo has no matching test harness for the touched area, document the gap and run the nearest build/typecheck/lint gate. Do not invent a new framework without user approval.
