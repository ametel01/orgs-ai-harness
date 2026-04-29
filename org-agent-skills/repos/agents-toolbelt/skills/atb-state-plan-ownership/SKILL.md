---
name: atb-state-plan-ownership
description: Use this skill when changing agents-toolbelt state reconciliation, install receipts, managed-vs-external ownership, install/update/uninstall planning, verification persistence, shell-hook state, or lifecycle summaries in `internal/state`, `internal/discovery`, `internal/plan`, and `cmd/atb/runtime.go`.
---

# ATB State Plan Ownership

## Purpose

Protect the ownership model: `atb` may update or uninstall only tools with managed receipts, while external PATH tools remain visible and verified but not owned.

## When to use

Use for changes involving state files, discovery snapshots, install/update/uninstall plans, receipt fields, verification metadata, shell-hook status, or summary rendering.

## Inspect first

- `internal/state/state.go`
- `internal/discovery/discovery.go`
- `internal/plan/plan.go`
- `internal/plan/update.go`
- `internal/plan/uninstall.go`
- `internal/plan/executor.go`
- `cmd/atb/runtime.go`
- Related tests in `internal/plan`, `internal/state`, and `cmd/atb`

## Repository map

- `state.State`: persisted JSON under the user config dir.
- `state.ToolState`: receipt and verification record; managed receipts store lifecycle commands.
- `discovery.Reconcile`: merges catalog tools, PATH lookup results, and persisted state.
- `plan.Build*Plan`: produces deterministic actions with skip reasons.
- `plan.Execute*Plan`: executes actions and mutates state.

## Standard workflow

1. Model behavior through `State`, `ToolPresence`, `Plan`, and `Summary` instead of one-off CLI logic.
2. Keep managed receipts after successful install even when post-install verification fails.
3. Preserve external tools in status and generated inventory when verified, but never mark them managed without an install receipt.
4. For updates and uninstalls, prefer lifecycle commands stored in receipts over the current catalog method when present.
5. Persist state after install, update, and uninstall execution before post-processing that can fail.
6. Keep action ordering deterministic: tier rank first, then tool ID where applicable.

## Validation commands

- State and discovery: `go test ./internal/state ./internal/discovery`
- Planning and execution: `go test ./internal/plan`
- Runtime state flows: `go test ./cmd/atb`
- Full gate: `make verify`

## Common pitfalls

- `State.Tool()` returns a value copy plus `bool`; after mutation call `SetTool` or `AddReceipt`.
- `updateReceiptVerification` intentionally records failed verification details without deleting receipts.
- `uninstall` must refuse external tools and report them separately from generic skipped actions.
- A canceled skill-target picker records the `none` sentinel so future update/uninstall commands do not write skills unexpectedly.

## Escalation

If a requested change blurs managed and external ownership, stop and clarify the lifecycle contract before editing uninstall or update behavior.
