---
name: atb-release-installer-security
description: Use this skill when changing agents-toolbelt release packaging, GoReleaser config, GitHub Actions release flow, `scripts/install.sh`, self-update downloads, checksum verification, cosign signing, provenance attestation, version injection, or installer security behavior.
---

# ATB Release Installer Security

## Purpose

Preserve secure installation and update behavior for released `atb` binaries.

## When to use

Use for `.goreleaser.yaml`, `.github/workflows/release.yml`, `.github/workflows/ci.yml`, `scripts/install.sh`, `internal/selfupdate`, release docs, or checksum/signing changes.

## Inspect first

- `scripts/install.sh`
- `internal/selfupdate/selfupdate.go`
- `internal/selfupdate/selfupdate_test.go`
- `.goreleaser.yaml`
- `.github/workflows/release.yml`
- `.github/workflows/ci.yml`
- `README.md` installation section
- `CHANGELOG.md`

## Repository map

- GoReleaser builds `./cmd/atb` into `atb` for linux/darwin and amd64/arm64.
- Version is injected with `-X main.version={{ .Version }}`.
- Archives are named `atb_{{ .Os }}_{{ .Arch }}.tar.gz`.
- `checksums.txt` is SHA256, cosign signs the checksum artifact, and GitHub Actions attests `dist/*.tar.gz`.
- `scripts/install.sh` downloads archive plus checksums and verifies before install.
- `internal/selfupdate` fetches GitHub Releases, verifies checksums, size-limits downloads, and replaces the current executable.

## Standard workflow

1. Preserve fail-closed checksum verification in both installer and self-update paths.
2. Keep installer defaults: non-root installs to `~/.local/bin`; root installs to `/usr/local/bin`; never invoke `sudo` automatically.
3. Keep release assets and installer/self-update asset-name logic in sync.
4. Bound network downloads and binary sizes in self-update code.
5. Update README install commands and CHANGELOG when user-visible installer or release behavior changes.

## Validation commands

- Installer shell syntax: `bash -n scripts/install.sh`
- Self-update tests: `go test ./internal/selfupdate`
- Release config sanity, when GoReleaser is available: `goreleaser check`
- Full gate: `make verify`

## Common pitfalls

- Do not remove checksum validation to simplify install or update flows.
- Do not assume Go is installed on hosts using `scripts/install.sh`; it downloads released binaries.
- `atb update` must reject development builds where `version == "dev"`.
- Keep supported OS/arch pairs aligned across installer, self-update, and GoReleaser.

## Escalation

If release tooling such as GoReleaser or cosign is unavailable locally, run the Go and shell validations that do not require those binaries and state the skipped release-tool check.
