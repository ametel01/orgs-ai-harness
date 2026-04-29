---
name: atb-cli-runtime-tui
description: Use this skill when changing agents-toolbelt Cobra commands, `cmd/atb/runtime.go`, Bubble Tea pickers, install/update/uninstall/status/catalog command behavior, stdout/stderr rendering, `--yes` non-interactive mode, or TUI selection/search behavior.
---

# ATB CLI Runtime TUI

## Purpose

Keep command handlers thin, runtime workflows testable, and interactive flows compatible with non-interactive automation.

## When to use

Use for `cmd/atb/*` command changes, `internal/tui/*` changes, CLI output changes, `--yes` behavior, target/dependency pickers, or status/catalog formatting.

## Inspect first

- `cmd/atb/root.go`
- The command file being changed in `cmd/atb`
- `cmd/atb/runtime.go`
- `internal/tui/picker.go`
- `internal/tui/dependency.go`
- `internal/tui/target.go`
- Related `cmd/atb/*_test.go` and `internal/tui/*_test.go`

## Repository map

- Cobra files define command shape and delegate to runtime functions.
- Runtime functions take injected `context.Context`, stdin, stdout, and stderr to keep tests deterministic.
- Bubble Tea pickers own selection, search, collapsed nice-tier rows, and target/dependency choices.
- `make run ARGS='...'` or `make run <args>` runs the CLI through `go run ./cmd/atb`.

## Standard workflow

1. Keep Cobra `RunE` handlers minimal; move behavior into runtime helpers or internal packages.
2. Preserve writer injection. Do not hardcode stdout/stderr/stdin where tests need buffers or readers.
3. Support non-interactive `atb install -y`: select `DefaultSelected` tools, skip TUI pickers, and print shell-hook suggestions without applying rc-file changes.
4. For interactive changes, test model updates directly with Bubble Tea messages instead of snapshotting terminal output only.
5. Keep tabular output routed through `tabwriter` for `status` and `catalog`.

## Validation commands

- Runtime commands: `go test ./cmd/atb`
- TUI behavior: `go test ./internal/tui`
- Manual command smoke test: `make run ARGS='catalog'`
- Full gate: `make verify`

## Common pitfalls

- Do not add shell rc-file mutation without explicit user confirmation.
- Quitting a picker with `q` or `esc` clears selections; do not accidentally preserve abandoned selections.
- Nice-tier tools start collapsed behind an expansion row.
- Tests that set environment variables or PATH should not use `t.Parallel` in ways that race process-global state.

## Escalation

If a CLI change affects persisted state, package-manager execution, or generated skills, also apply the relevant state/plan or skill-generation workflow before final validation.
