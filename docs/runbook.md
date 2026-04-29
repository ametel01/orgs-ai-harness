# Runbook

This runbook covers common local operations and failure modes for
`orgs-ai-harness`. Run commands from the repository root unless a command
explicitly targets `org-agent-skills/`.

## Setup And Health Check

Install and sync the locked environment:

```sh
uv sync --frozen
```

Check the CLI and current gates:

```sh
uv run harness --help
make verify
make security
make build
```

Use `make verify` before committing normal code or docs changes. Use all three
gates before closing issue work.

## Add Or Discover Repositories

Initialize an org pack when starting from an empty workspace:

```sh
uv run harness org init --name <org-name>
```

Register a local repo or URL:

```sh
uv run harness repo add <path-or-url>
uv run harness repo list
uv run harness validate
```

Discover GitHub repositories with the GitHub CLI:

```sh
gh auth status
uv run harness repo discover <github-owner>
```

If discovery fails, confirm `gh` is installed, authenticated, and authorized for
the owner or organization.

## Onboard A Repository

Generate draft artifacts:

```sh
uv run harness onboard <repo-id>
uv run harness validate <repo-id>
uv run harness explain <repo-id>
```

Common failures:

- `repo id is not registered`: run `uv run harness repo list` and use the exact
  `id`.
- `repo <id> has no local path`: set or repair the local path before onboarding.
- `repo path does not exist`: clone the repo or update the registry path.
- `needs-investigation`: inspect `unknowns.yml`, fill missing context, then
  rerun onboarding.
- Validation errors for missing artifacts: rerun onboarding or restore the
  generated file named in the error.

Sensitive files are skipped during scanning. If generated artifacts mention
secrets or private identifiers, add redaction rules in `harness.yml`, remove the
leaked artifact content, and rerun the scan/generation path.

## Review And Approve

Inspect generated files before approval:

```sh
uv run harness explain <repo-id>
uv run harness validate <repo-id>
```

Approve all artifacts:

```sh
uv run harness approve <repo-id> --all
```

Approve with exclusions when a generated artifact is wrong or unsafe:

```sh
uv run harness approve <repo-id> --exclude <artifact-path>
```

Common failures:

- `has no generated draft artifacts`: run onboarding first.
- `approval cannot exclude every generated artifact`: reject or regenerate
  instead of creating an empty approved pack.
- Protected artifact validation fails after approval: use the proposal flow for
  accepted changes instead of editing approved artifacts directly.

## Eval Replay And Verification

Run local replay after approval:

```sh
uv run harness eval <repo-id>
uv run harness validate <repo-id>
```

Eval writes `eval-report.yml` and appends trace summaries. A pack stays
`approved-unverified` when replay thresholds are not met, blocking unknowns
remain, or safety checks fail. Inspect:

- `repos/<repo-id>/eval-report.yml`
- `repos/<repo-id>/unknowns.yml`
- `repos/<repo-id>/pack-report.md`
- `trace-summaries/eval-events.jsonl`

For a draft-only local check, use the development mode exposed by the CLI help.
Do not treat development eval results as approval or verification.

## Cache And Export

Refresh a repo-local cache after approval:

```sh
uv run harness cache refresh <repo-id>
```

Export for a runtime target:

```sh
uv run harness export codex <repo-id>
uv run harness export generic <repo-id>
```

Common failures:

- `must be approved-unverified or verified before cache refresh`: approve the
  pack first.
- `repo cache is missing`: run `harness cache refresh <repo-id>` before export.
- `unsupported export target`: use `codex` or `generic`.
- `cache does not include applied proposals`: refresh the cache after applying
  proposals.

The cache and exports are made read-only. Refresh the cache rather than editing
cached files by hand.

## Proposal Workflow

Create evidence-backed proposals after source changes or eval learning:

```sh
uv run harness improve <repo-id>
uv run harness refresh <repo-id>
uv run harness proposals list
uv run harness proposals show <proposal-id>
```

Apply only after review:

```sh
uv run harness proposals apply <proposal-id> --yes
uv run harness validate <repo-id>
uv run harness cache refresh <repo-id>
```

Reject with a reason when the proposal is not useful:

```sh
uv run harness proposals reject <proposal-id> --reason "<reason>"
```

Common failures:

- `insufficient evidence`: run evals or collect new source changes before
  calling `improve` again.
- `source unchanged`: no refresh proposal is needed.
- `proposal is not open`: it was already applied or rejected.
- `patch target is not listed`: proposal metadata and patch are inconsistent;
  regenerate the proposal.

## Quality Gate Failures

Use the failing command output as the source of truth:

- Ruff format check: run `make format`, inspect the diff, then rerun
  `make verify`.
- Ruff lint: fix the reported rule locally; keep ignores local and justified.
- Pyright: fix the typed boundary rather than weakening shared contracts.
- Coverage: add focused regression tests for the changed behavior.
- `pip-audit`: update or pin the affected dependency through `uv`.
- Bandit: fix the unsafe pattern or add a narrow `# nosec <rule>` with a nearby
  rationale when the subprocess or placeholder is intentional.
- detect-secrets: remove the value when it is a real secret; update the baseline
  only for generated false positives after review.
- Build: inspect packaging output and `pyproject.toml` before retrying.

## Incident Checklist

When generated artifacts contain unsafe or stale content:

1. Stop exporting the affected repo pack.
2. Remove or replace the unsafe generated artifact content.
3. Add or tighten `harness.yml` redaction rules if sensitive material leaked.
4. Regenerate or create a proposal, depending on whether artifacts were already
   approved.
5. Run `uv run harness validate <repo-id>`.
6. Run `make verify`, `make security`, and `make build`.
7. Refresh cache and export only after approval status is correct.
