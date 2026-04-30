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

Use CI replay for pull request artifacts:

```sh
uv run harness eval <repo-id> --ci --summary-path .agent-harness/ci-eval/<repo-id>.json
```

CI replay is deterministic and local-only. It supports only the fixture adapter,
does not use credentials, does not prompt, and does not update `harness.yml`,
`approval.yml`, or `pack-report.md`. It still writes `eval-report.yml`, appends
eval traces, prints JSON to stdout, and writes the same JSON to `--summary-path`
when provided. The GitHub Actions `CI Eval Replay` job discovers eligible
registered repos, skips ineligible repos with a reason in
`.agent-harness/ci-eval/discovery.json`, runs the CI command for eligible repos,
and uploads `.agent-harness/ci-eval/` as the `ci-eval-replay` artifact. The job
is artifact-only and non-blocking because it sets `continue-on-error: true`.

Common CI replay failures:

- `supports only deterministic local adapter 'fixture'`: remove a non-fixture
  `--adapter` value from CI.
- `not eligible for CI eval replay`: approve the pack or inspect the lifecycle
  status before expecting CI replay.
- `has no human-approved pack metadata`, `no user-approved onboarding evals`, or
  `missing evals`: rerun onboarding/review and approve the generated eval
  artifact.

## PR Review Artifact Workflow

Use PR review artifacts when a maintainer needs a local, reviewable summary of
changed-file risk and relevant harness context without PR comments or GitHub
state mutation.

Manual artifact generation:

```sh
uv run harness review changed-files \
  --repo-id <repo-id> \
  --files src/app.py tests/test_app.py \
  --json-path .agent-harness/pr-review/<repo-id>.json \
  --markdown-path .agent-harness/pr-review/<repo-id>.md
```

Git-ref input for a local checkout:

```sh
uv run harness review changed-files \
  --repo-id <repo-id> \
  --base <base-ref> \
  --head <head-ref> \
  --json-path .agent-harness/pr-review/<repo-id>.json \
  --markdown-path .agent-harness/pr-review/<repo-id>.md
```

The JSON artifact is the machine-readable contract. It includes
`schema_version`, `status`, `repo_id`, `source`, optional `base`/`head`,
`changed_files`, `risk`, and `context`. The `risk` section contains per-file
`low`, `medium`, or `high` classifications, suggested local commands, suggested
eval ids, and warnings. Suggested commands are recommendations only; they are
not executed by the review command. The `context` section contains matched
skills, scan evidence, unknowns, generated artifact statuses, changed-path
classification, and missing coverage. The Markdown artifact is a concise human
readout of the same contract for PR artifact inspection.

The GitHub Actions `PR Review Artifacts` job runs only on pull requests. It
checks out with full history, discovers the matching registered repo, and runs
the review command with the PR base and head SHAs when the repo is active,
non-external, approved or verified, has `approval.yml`, has a repo artifact
root, and has a resolvable local path. It uploads `.agent-harness/pr-review/` as
the `pr-review-artifacts` artifact. If no eligible repo is found, it exits
successfully and uploads `discovery.json` plus `SKIPPED.md`.

Interpretation guidance:

- High risk means the changed path touches sensitive, CI, dependency, generated,
  or similar high-impact surfaces. It is not a merge-blocking decision.
- Suggested checks are derived from known local evidence and should be run by a
  maintainer or future workflow before relying on the change.
- Suggested eval ids identify existing onboarding evals whose expected files
  overlap the change.
- Missing coverage means generated skills, resolvers, scan evidence, or local
  artifacts do not fully explain the changed path.

The workflow is artifact-only. It does not post PR comments, request reviewers,
label pull requests, update dashboards, mutate approvals, or block merges based
on risk classification. A job failure means the artifact command or CI setup
failed, not that an automated reviewer rejected the PR.

## Release Readiness Artifact Workflow

Use release readiness artifacts when maintainers need a local, reviewable
release summary without publishing, deployment, tags, GitHub Releases, comments,
or merge-blocking policy.

Manual artifact generation:

```sh
uv run harness release readiness \
  --repo-id <repo-id> \
  --version v1.2.3 \
  --files CHANGELOG.md package.json \
  --json-path .agent-harness/release-readiness/<repo-id>.json \
  --markdown-path .agent-harness/release-readiness/<repo-id>.md
```

Git-ref input for a local checkout:

```sh
uv run harness release readiness \
  --repo-id <repo-id> \
  --version v1.2.3 \
  --base <base-ref> \
  --head <head-ref> \
  --json-path .agent-harness/release-readiness/<repo-id>.json \
  --markdown-path .agent-harness/release-readiness/<repo-id>.md
```

The JSON artifact is the machine-readable contract. It includes
`schema_version`, `status`, `repo_id`, `release`, `lifecycle`, `context`,
`release_evidence`, `missing_evidence`, and `risk`. The `risk` section contains
`low`, `medium`, or `high` items, suggested local commands, suggested eval ids,
and warnings. Suggested commands are not executed by the release readiness
command. The Markdown artifact is the human readout for artifact inspection.

The GitHub Actions `Release Readiness Artifacts` job runs only through
`workflow_dispatch`. It checks out with full history, discovers the matching
registered repo, and runs the release readiness command when the repo is active,
non-external, approved or verified, has `approval.yml`, has `eval-report.yml`,
has `evals/onboarding.yml`, has a repo artifact root, and has a resolvable local
path. It uploads `.agent-harness/release-readiness/` as the
`release-readiness-artifacts` artifact. If no eligible repo is found, it exits
successfully and uploads `discovery.json` plus `SKIPPED.md`.

Interpretation guidance:

- High risk means the release touches dependency, CI, migration, deployment,
  unverified pack, blocking unknown, approval metadata, or missing eval evidence.
  It is not a publish or merge-blocking decision.
- Suggested checks are derived from known local evidence and should be run by a
  maintainer or future workflow before relying on the release.
- Suggested eval ids identify existing onboarding evals whose expected files
  overlap the release changed-file set.
- Missing evidence means the local repo or generated pack does not expose
  expected release context such as changelog, version, CI, eval, approval, scan,
  skill, or resolver artifacts.

Common release readiness failures:

- `repo id is not registered`: run `uv run harness repo list` and use the exact
  `id`.
- `repo is an external dependency reference` or `repo is not active selected
  coverage`: release readiness only supports selected local coverage.
- `repo <id> has no local path` or `repo path does not exist`: set or repair the
  local path before generating artifacts.
- `release readiness requires both --base and --head`: pass both refs or neither.
- `cannot resolve base/head ref`: fetch the relevant commits or use `--files` /
  `--files-from` instead.

## Dependency Campaign Artifact Workflow

Use dependency campaign artifacts when maintainers need a local, reviewable
cross-repo dependency inventory, risk summary, and rollout order without
manifest edits, package-manager upgrades, PR creation, comments, or
merge-blocking policy.

Manual artifact generation:

```sh
uv run harness dependency campaign \
  --name dependency-campaign \
  --package fastapi \
  --json-path .agent-harness/dependency-campaign/campaign.json \
  --markdown-path .agent-harness/dependency-campaign/campaign.md
```

The `--package` filter is optional and may be repeated. It is recorded as
campaign input only; the command does not query registries or compare latest
versions.

The JSON artifact is the machine-readable contract. It includes
`schema_version`, `status`, `campaign`, `summary`, `repos`, `rollout_plan`,
`skipped_repos`, and `warnings`. Each repo entry contains dependency manifests,
lockfiles, package-manager evidence, generated pack status, missing evidence,
risk items, suggested local commands, and suggested eval ids. Suggested commands
and evals are recommendations derived from known local evidence and are not
executed by the command.

The GitHub Actions `Dependency Campaign Artifacts` job runs only through
`workflow_dispatch`. It discovers active local repos with dependency manifest
evidence, writes `.agent-harness/dependency-campaign/discovery.json`, and runs
the dependency campaign command when at least one eligible repo is found. It
uploads `.agent-harness/dependency-campaign/` as the
`dependency-campaign-artifacts` artifact. If no org pack, local repo path, or
dependency manifest evidence is available, it exits successfully and uploads
`discovery.json` plus `SKIPPED.md`.

Interpretation guidance:

- High risk means the campaign found malformed dependency manifests, unverified
  packs, approval metadata gaps, migration coupling, or similarly conservative
  rollout signals. It is not a merge-blocking decision.
- Suggested checks are derived from generated script manifests, onboarding eval
  expected commands, scan command candidates, deterministic repo manifests, and
  built-in `harness validate <repo-id>`.
- Suggested eval ids identify existing onboarding evals whose expected files
  overlap dependency manifests.
- Missing evidence means local files or generated pack artifacts do not expose
  expected dependency, lockfile, approval, eval, scan, skill, or resolver
  context.

Common dependency campaign failures:

- `dependency campaign name cannot be empty`: pass a non-empty `--name`.
- `requires at least one registered repository`: register repos before running
  a campaign.
- `has no eligible active local repositories`: activate selected local coverage
  or repair repo paths with `uv run harness repo set-path`.
- `package filters cannot be empty`: remove empty `--package` values.

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
