---
name: atb-catalog-package-lifecycle
description: Use this skill when adding or changing agents-toolbelt catalog tools, install/update/uninstall methods, package-manager support, dependency bootstrapping, tool verification commands, tiers, categories, or skill exposure in `internal/catalog` and `internal/pkgmgr`.
---

# ATB Catalog Package Lifecycle

## Purpose

Preserve the contract between the embedded tool registry, package-manager selection, dependency bootstrapping, verification, TUI grouping, and generated skills.

## When to use

Use when editing `internal/catalog/registry.json`, catalog validation, package-manager implementations, dependency bootstrap logic, or tool lifecycle commands.

## Inspect first

- `internal/catalog/catalog.go`
- `internal/catalog/registry.json`
- `internal/pkgmgr/manager.go`
- `internal/pkgmgr/detect.go`
- `internal/pkgmgr/dependency.go`
- `internal/catalog/catalog_test.go`
- `internal/pkgmgr/*_test.go`

## Repository map

- `catalog.Tool`: registry schema for ID, binary, tier, category, platform support, lifecycle methods, verification, and skill exposure.
- `pkgmgr.Manager`: executes structured lifecycle commands without shell string execution.
- `pkgmgr.ResolveDependencies`: bootstraps secondary managers such as `cargo`, `go`, and `pipx` via system package managers.
- `catalog.CategoryLabels`: shared by TUI and generated `cli-tools` skill output.

## Standard workflow

1. Add or edit registry entries with complete `install_methods`, `verify.command`, `expected_exit_codes`, `platforms`, and `tags`.
2. Keep `id` and `bin` unique; tests validate duplicate IDs and binaries.
3. Prefer structured command arrays. Do not introduce `sh -c` for install, update, uninstall, or verify actions.
4. For Cargo installs that support it, preserve `cargo install --locked`.
5. When adding a new category, update `CategoryLabels` and any tests that assert human-readable labels.
6. When a tool should appear in generated agent capability inventory, set `skill_expose: true`; otherwise keep it hidden.

## Validation commands

- Catalog and package-manager checks: `go test ./internal/catalog ./internal/pkgmgr`
- Lifecycle integration impact: `go test ./internal/plan ./cmd/atb`
- Full gate before completion: `make verify`

## Common pitfalls

- Registry validation requires every install method to have `manager`, `package`, `command`, `update_command`, and `uninstall_command`.
- `requires_sudo` is metadata only; command arrays still include `sudo` where needed.
- Tool selectors in update/uninstall can match both catalog ID and binary name; avoid changes that break either path.
- Verify regexes are compiled at runtime; invalid regexes turn verification into an error, not a skipped version.

## Escalation

If a tool needs an unsupported package manager or platform-specific install shape, add tests that demonstrate the selection behavior before changing runtime install logic.
