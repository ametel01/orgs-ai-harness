---
name: atb-go-quality-gates
description: Use this skill when changing Go code, tests, Makefile quality gates, lint configuration, or developer docs in agents-toolbelt. Covers the repo's Go 1.26 toolchain, `make verify`, strict golangci-lint profile, test expectations, and documentation/changelog updates.
---

# ATB Go Quality Gates

## Purpose

Keep changes aligned with the repository's canonical Go workflow and CI gates.

## When to use

Use for any Go source edit, test edit, quality-gate change, `.golangci.yml` change, `Makefile` change, or developer workflow documentation change.

## Inspect first

- `Makefile`
- `CONTRIBUTING.md`
- `.golangci.yml`
- `.github/workflows/ci.yml`
- The package file being edited and its nearest `*_test.go`

## Repository map

- `cmd/atb`: Cobra command tree and runtime orchestration.
- `internal/*`: implementation packages; keep command handlers thin.
- `Makefile`: source of truth for local validation.
- `.tools/bin`: Makefile-installed staticcheck, golangci-lint, and govulncheck.

## Standard workflow

1. Confirm Go 1.26+ is available: `go env GOVERSION`.
2. Read the target file and at least one related test or caller before editing.
3. Prefer package-local tests with fakes, temp dirs, and injected `io.Reader`/`io.Writer` instead of machine-specific state.
4. Keep errors contextual with `fmt.Errorf("action: %w", err)` or existing `wrapError` helpers at boundaries.
5. Update `README.md` for user-facing behavior, `CHANGELOG.md` for functional changes, and `CONTRIBUTING.md` when workflow expectations change.

## Validation commands

- Narrow test while iterating: `go test ./path/to/package`
- Full local test target: `make test`
- Full CI-equivalent gate: `make verify`

`make verify` runs `fmt`, `vet`, `lint`, `test`, `build`, and `vulncheck`. CI runs `make verify` on Linux and macOS.

## Common pitfalls

- Do not submit a change that only passes `go test`; the repo expects the full Makefile gate before review.
- Do not bypass strict lint rules with broad `nolint`; narrow any directive and explain why.
- Keep tests parallelizable unless they mutate process-global state such as environment variables or PATH.
- `make lint` installs pinned tool versions through the Makefile; avoid replacing them with unpinned global tooling.

## Escalation

If Go 1.26 is unavailable or a Makefile tool cannot be installed, report the exact failing command and run the narrowest possible `go test` package validation instead.
