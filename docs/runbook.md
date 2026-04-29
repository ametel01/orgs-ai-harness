# Runbook

This runbook covers common local operations and failure modes for the current
implemented `orgs-ai-harness` CLI. The canonical target is the complete harness
runtime in [`local-docs/HARNESS_SPEC.md`](../local-docs/HARNESS_SPEC.md); the
operations below maintain the skill-pack lifecycle foundation for that runtime.

Run commands from the repository root unless a command explicitly targets
`org-agent-skills/`.

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

## First-Run Setup

Use the setup wizard when starting from a GitHub owner, GitHub profile, or a
local-only workspace:

```sh
uv run harness setup https://github.com/<owner>
uv run harness setup <github-owner>
uv run harness setup local
```

The wizard can initialize `org-agent-skills/`, discover or register
repositories, optionally clone selected repositories, validate the pack, and
offer skill generation, approval, development eval replay, cache refresh, export,
and explain steps.

## Add Or Discover Repositories

Initialize an org pack when starting from an empty workspace:

```sh
uv run harness org init --name <org-name>
uv run harness org init --github https://github.com/<owner>
uv run harness org init --repo <path-or-git-url>
```

Register a local repo, remote URL, or external dependency reference:

```sh
uv run harness repo add <path-or-url> --purpose "<purpose>" --owner <owner>
uv run harness repo add git@github.com:vendor/sdk.git --external
uv run harness repo list
uv run harness validate
```

Discover GitHub repositories with the GitHub CLI. In non-interactive use, pass
`--select` with comma-separated repo ids or names:

```sh
gh auth status
uv run harness repo discover <github-owner> --select api-service,web-app
uv run harness repo discover --github-org <org> --select api-service
uv run harness repo discover --github-user <user> --select cli-tools
```

Clone selected repositories while registering them:

```sh
uv run harness repo discover <github-owner> \
  --select api-service,web-app \
  --clone \
  --clone-dir ./covered-repos
```

Archived repositories and forks are hidden unless requested:

```sh
uv run harness repo discover --github-org <org> \
  --include-archived \
  --include-forks \
  --select old-tool,forked-sdk
```

Repair, deactivate, or remove coverage:

```sh
uv run harness repo set-path <repo-id> <path>
uv run harness repo deactivate <repo-id> --reason "<reason>"
uv run harness repo remove <repo-id> --reason "<reason>"
uv run harness repo remove <repo-id> --reason "<reason>" --force
```

If discovery fails, confirm `gh` is installed, authenticated, and authorized for
the owner or organization. If non-interactive discovery fails with
`repo discover requires --select`, rerun with an explicit selection.

## Onboard A Repository

Run a read-only scan before skill generation when you want to inspect evidence:

```sh
uv run harness onboard <repo-id> --scan-only
uv run harness validate <repo-id>
```

Generate draft artifacts:

```sh
uv run harness onboard <repo-id>
uv run harness onboard <repo-id> --llm codex --skill-target codex
uv run harness onboard <repo-id> --llm claude --skill-target claude
uv run harness onboard <repo-id> --llm template --skill-target codex
uv run harness validate <repo-id>
uv run harness explain <repo-id>
```

Common failures:

- `repo id is not registered`: run `uv run harness repo list` and use the exact
  `id`.
- `repo <id> has no local path`: run `uv run harness repo set-path <repo-id>
  <path>` before onboarding.
- `repo path does not exist`: clone the repo or update the registry path.
- `repo is an external dependency reference`: external entries are not selected
  coverage and cannot be onboarded.
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
uv run harness approve <repo-id>
uv run harness explain <repo-id>
uv run harness validate <repo-id>
```

Approve all artifacts:

```sh
uv run harness approve <repo-id> --all --rationale "<rationale>"
```

Approve with exclusions, or reject when generated artifacts are wrong or unsafe:

```sh
uv run harness approve <repo-id> --exclude <artifact-path>
uv run harness reject <repo-id> --reason "<reason>"
```

Common failures:

- `has no generated draft artifacts`: run onboarding first.
- `approval cannot exclude every generated artifact`: reject or regenerate
  instead of creating an empty approved pack.
- `is not in draft status`: only draft packs can be approved or rejected.
- Protected artifact validation fails after approval: use the proposal flow for
  accepted changes instead of editing approved artifacts directly.

## Eval Replay And Verification

Run local replay after approval:

```sh
uv run harness eval <repo-id>
uv run harness eval <repo-id> --adapter codex-local
uv run harness eval <repo-id> --development
uv run harness validate <repo-id>
```

Eval writes `eval-report.yml` and appends trace summaries. A pack stays
`approved-unverified` when replay thresholds are not met, blocking unknowns
remain, or safety checks fail. Inspect:

- `repos/<repo-id>/eval-report.yml`
- `repos/<repo-id>/unknowns.yml`
- `repos/<repo-id>/pack-report.md`
- `trace-summaries/eval-events.jsonl`

Use `--development` for draft-only local checks. Development eval results do not
approve or verify a pack.

## Runtime Sessions

Start the runtime vertical slice. The default permission mode is read-only:

```sh
uv run harness run "summarize this repo state"
uv run harness run "summarize this repo state" --adapter fixture
uv run harness run "summarize this repo state" --permission read-only
```

The output includes a session id and JSONL log path under
`.agent-harness/sessions/`. A healthy session contains at least:

- `session_started`
- `context_assembled`
- one or more `adapter_decision` events
- one or more `tool_call` events
- matching `tool_result` events
- matching `adapter_observation` events for tool-call decisions
- `final_response`

The default CLI path is deterministic and fixture-adapter driven. It exercises
the adapter protocol and read-only tool loop without calling a real LLM provider.
To route decisions through a local subprocess, configure a command that reads the
prompt from stdin and writes exactly one strict JSON decision to stdout:

```sh
ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND="codex-local" \
  uv run harness run "summarize this repo state" --adapter codex-local
```

Opt into workspace-write only for bounded local coding tasks:

```sh
uv run harness run "update a file and validate it" --permission workspace-write
ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND="codex-local" \
  uv run harness run "update a file and validate it" \
    --adapter codex-local \
    --permission workspace-write
```

Set `ORGS_AI_HARNESS_CODEX_LOCAL_TIMEOUT=<seconds>` when the default 30-second
timeout is too long or too short for local diagnostics. The model-backed path
returns a non-zero CLI status when the session ends with an adapter error,
denied tool, or max-step diagnostic.

Inspect/resume a session:

```sh
uv run harness run --resume --session-id <session-id>
```

Common failures:

- `harness run requires a goal unless --resume is used`: pass a goal string or
  add `--resume`.
- `harness run --resume requires --session-id`: provide the session id printed
  by the original run.
- A malformed session log is reported as recovery diagnostics rather than being
  silently ignored.
- Adapter exceptions, malformed decisions, max-step stops, and denied tool calls
  are logged as `error` events and surfaced through the final diagnostic summary.
- `codex-local adapter executable not found`: install the configured command or
  set `ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND` to the correct argv string.
- `adapter output must be valid JSON object text`: make the local adapter write
  only one JSON object to stdout, with no prose before or after it.
- `codex-local adapter exited with code ...` or `wrote stderr`: inspect the
  local subprocess stderr/stdout details in the diagnostic summary.
- `codex-local adapter timed out`: raise
  `ORGS_AI_HARNESS_CODEX_LOCAL_TIMEOUT` or debug the local command directly.
- `adapter-selected tool denied`: the adapter requested a tool or shell command
  outside the selected permission mode. Check the `tool_result.payload` for
  `active_permission`, `required_permission`, and `reason`.
- Denied write: rerun with `--permission workspace-write` when the write is
  intentional. If the denial says `path is outside workspace` or
  `path is protected`, choose a path under the workspace that is not a protected
  generated pack path.
- Denied high-risk shell command: destructive, network, deployment, git
  push/pull/fetch/clone, and unknown command classes remain blocked in
  workspace-write mode.
- Failed validation command: inspect the `local.shell` tool result `stdout`,
  `stderr`, and `exit_code`. A non-zero validation command is recorded as a
  structured tool result rather than a runtime crash.

Permission behavior is intentionally conservative. The CLI run path uses
read-only mode unless `--permission workspace-write` is passed. Workspace-write
allows `local.write_file` under the workspace and known validation commands such
as `make test`, `make verify`, `make lint`, `uv run pytest`, `uv run ruff`, and
`uv run pyright`. Session logs record the selected permission mode in
`session_started`, changed paths in write `tool_result` and observation payloads,
and structured denied diagnostics. Full-access tools, approval prompts,
rollback, sub-agents, broad shell access, network/deployment work, and
autonomous deployment behavior remain deferred.

## Cache And Export

Refresh a repo-local cache after approval:

```sh
uv run harness cache refresh <repo-id>
```

Export for a runtime target:

```sh
uv run harness export codex <repo-id>
uv run harness export generic <repo-id>
uv run harness export generic <repo-id> --allow-draft
uv run harness export generic <repo-id> --development
```

Common failures:

- `must be approved-unverified or verified before cache refresh`: approve the
  pack first.
- `repo cache is missing`: run `harness cache refresh <repo-id>` before export.
- `unsupported export target`: use `codex` or `generic`.
- `repo <id> is draft; pass --allow-draft`: draft exports must be intentional.
- `repo <id> needs investigation; pass --development`: investigation exports
  are development-only.
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
