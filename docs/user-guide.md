# User Guide

This guide covers the current implemented CLI workflow. The canonical product
target is the full harness runtime described in
[`local-docs/HARNESS_SPEC.md`](../local-docs/HARNESS_SPEC.md); the commands
below cover the skill-pack lifecycle foundation that runtime will use.

Run commands from the repository root or from a workspace that contains
`org-agent-skills/`.

## Setup

Install dependencies and check the CLI:

```sh
uv sync --frozen
uv run harness --help
```

The package also exposes `python -m orgs_ai_harness`, but `uv run harness` is
the documented command path.

## First-Run Wizard

Use the interactive setup wizard for a GitHub org, GitHub profile, or local-only
workspace:

```sh
uv run harness setup https://github.com/<owner>
uv run harness setup <github-owner>
uv run harness setup local
```

The wizard can initialize `org-agent-skills/`, discover or register
repositories, optionally clone selected repositories, validate the pack, and
offer skill generation, approval, development eval replay, cache refresh, export,
and explain steps.

## Manual Org Setup

Create a local org pack:

```sh
uv run harness org init --name <org-name>
uv run harness validate
```

Infer the org name from GitHub:

```sh
uv run harness org init --github https://github.com/<owner>
uv run harness validate
```

Attach an existing local pack or record a remote pack URL:

```sh
uv run harness org init --repo <path-or-git-url>
uv run harness validate
```

## Repository Coverage

Register local, remote, or external repositories explicitly:

```sh
uv run harness repo add ../api-service --purpose "Core API" --owner platform
uv run harness repo add git@github.com:acme/web-app.git --owner product
uv run harness repo add git@github.com:vendor/sdk.git --external
uv run harness repo list
uv run harness validate
```

Discover GitHub repositories with the locally authenticated `gh` CLI:

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

Archived repositories and forks are hidden unless included explicitly:

```sh
uv run harness repo discover --github-org <org> \
  --include-archived \
  --include-forks \
  --select old-tool,forked-sdk
```

Repair, deactivate, or remove coverage:

```sh
uv run harness repo set-path api-service ../api-service
uv run harness repo deactivate api-service --reason "Paused during ownership transfer"
uv run harness repo remove api-service --reason "Registered by mistake"
uv run harness repo remove api-service --reason "Retired service" --force
```

## Onboarding

Run a read-only scan when you want to inspect evidence before skill generation:

```sh
uv run harness onboard <repo-id> --scan-only
uv run harness validate <repo-id>
```

Generate a draft repo pack:

```sh
uv run harness onboard <repo-id> --llm codex --skill-target codex
uv run harness onboard <repo-id> --llm claude --skill-target claude
uv run harness onboard <repo-id> --llm template --skill-target codex
uv run harness validate <repo-id>
```

`--skill-target both` stages and installs generated repo skills for both Codex
and Claude Code. LLM generation writes prompts and logs under
`org-agent-skills/repos/<repo-id>/`, stages generated skills under
`llm-output/`, then snapshots validated skills into the repo pack.

## Review And Approval

Render the review view:

```sh
uv run harness approve <repo-id>
```

Approve or reject generated artifacts:

```sh
uv run harness approve <repo-id> --all --rationale "Initial reviewed pack"
uv run harness approve <repo-id> --exclude repos/<repo-id>/skills/example/SKILL.md
uv run harness reject <repo-id> --reason "Generated guidance is too uncertain"
```

Approved packs move to `approved-unverified`. Protected artifacts should change
through proposals rather than direct regeneration.

## PR Review Artifacts

Generate artifact-only review output for an explicit changed-file set:

```sh
uv run harness review changed-files \
  --repo-id <repo-id> \
  --files src/app.py tests/test_app.py \
  --json-path .agent-harness/pr-review/<repo-id>.json \
  --markdown-path .agent-harness/pr-review/<repo-id>.md
```

Use a newline-delimited file list for deterministic CI or scripted calls:

```sh
uv run harness review changed-files \
  --repo-id <repo-id> \
  --files-from .agent-harness/pr-review/changed-files.txt \
  --json-path .agent-harness/pr-review/<repo-id>.json \
  --markdown-path .agent-harness/pr-review/<repo-id>.md
```

Use local git refs when the repository checkout contains both commits:

```sh
uv run harness review changed-files \
  --repo-id <repo-id> \
  --base <base-ref> \
  --head <head-ref> \
  --json-path .agent-harness/pr-review/<repo-id>.json \
  --markdown-path .agent-harness/pr-review/<repo-id>.md
```

Inputs must identify one registered, active, non-external repo with a local
path. Changed files must be repo-relative and outside `.git`. The command does
not execute suggested checks; it only writes review artifacts.

The JSON artifact has `schema_version: 1` and stable sections for `changed_files`,
`risk`, and `context`. Risk levels are `low`, `medium`, and `high`. Suggested
commands come only from known local evidence such as generated script manifests,
onboarding eval expected commands, scan hypothesis command candidates,
deterministic repo manifests, and the built-in `harness validate <repo-id>`
pattern. Suggested evals are stable eval ids whose expected files overlap the
changed-file set. Missing skills, missing scan evidence, missing artifacts, and
malformed artifacts are represented as context or warnings instead of review
automation claims.

The GitHub Actions `PR Review Artifacts` job runs on `pull_request`, discovers
the registered repo matching the PR checkout, skips ineligible repos with
`.agent-harness/pr-review/discovery.json` and `SKIPPED.md`, and uploads
`.agent-harness/pr-review/` as `pr-review-artifacts`. The first workflow is
artifact-only. It does not post comments, request reviewers, mutate GitHub
state, or block merges based on risk classification; the job can still fail if
the artifact command itself cannot run.

## Release Readiness Artifacts

Generate artifact-only release readiness output for one registered repo:

```sh
uv run harness release readiness \
  --repo-id <repo-id> \
  --version v1.2.3 \
  --files CHANGELOG.md package.json \
  --json-path .agent-harness/release-readiness/<repo-id>.json \
  --markdown-path .agent-harness/release-readiness/<repo-id>.md
```

Use a newline-delimited changed-file list for deterministic scripted calls:

```sh
uv run harness release readiness \
  --repo-id <repo-id> \
  --version v1.2.3 \
  --files-from .agent-harness/release-readiness/changed-files.txt \
  --json-path .agent-harness/release-readiness/<repo-id>.json \
  --markdown-path .agent-harness/release-readiness/<repo-id>.md
```

Use local git refs when the checkout contains both commits:

```sh
uv run harness release readiness \
  --repo-id <repo-id> \
  --version v1.2.3 \
  --base <base-ref> \
  --head <head-ref> \
  --json-path .agent-harness/release-readiness/<repo-id>.json \
  --markdown-path .agent-harness/release-readiness/<repo-id>.md
```

Eligible repos must be registered, active, non-external, and have a local path.
The GitHub Actions workflow also requires an approved or verified local pack
with `approval.yml`, `eval-report.yml`, and `evals/onboarding.yml`. Missing
changelog, version, lockfile, CI, migration, deployment, approval, eval, scan,
skill, or resolver evidence is represented in the artifact as missing evidence,
warnings, or risk items instead of being treated as release automation.

The JSON artifact has `schema_version: 1` and stable sections for `release`,
`lifecycle`, `context`, `release_evidence`, `missing_evidence`, and `risk`.
Risk levels are `low`, `medium`, and `high`. Suggested commands come only from
known local evidence and are recommendations; the command does not execute
them. Suggested evals are existing onboarding eval ids whose expected files
overlap the release changed-file set. The Markdown artifact is a concise human
readout of the same data.

The GitHub Actions `Release Readiness Artifacts` job runs only from
`workflow_dispatch`. It discovers the registered repo matching the current
checkout, skips ineligible repos with
`.agent-harness/release-readiness/discovery.json` and `SKIPPED.md`, and uploads
`.agent-harness/release-readiness/` as `release-readiness-artifacts`. The first
workflow is artifact-only. It does not tag, publish, deploy, create GitHub
Releases, post comments, request reviewers, mutate GitHub state, or block
merges based on risk classification.

## Dependency Campaign Artifacts

Generate artifact-only dependency campaign output across eligible registered
repos:

```sh
uv run harness dependency campaign \
  --name dependency-campaign \
  --package fastapi \
  --json-path .agent-harness/dependency-campaign/campaign.json \
  --markdown-path .agent-harness/dependency-campaign/campaign.md
```

The `--package` filter is optional and may be repeated. It records deterministic
campaign inputs for maintainers; the command does not query package registries,
compare latest versions, edit dependency files, run package-manager commands, or
open pull requests.

Eligible repos must be registered, active, non-external, and have a resolvable
local path. The inventory scans local files for supported dependency manifests
and lockfiles such as `package.json`, `pyproject.toml`, `requirements.txt`,
`go.mod`, `Cargo.toml`, `package-lock.json`, `bun.lock`, `uv.lock`, `go.sum`,
and `Cargo.lock`. Malformed manifests, missing lockfiles, missing generated
pack evidence, missing approval metadata, missing eval evidence, and skipped
repos are represented as artifact data, warnings, or risk items.

The JSON artifact has `schema_version: 1` and stable sections for `campaign`,
`summary`, `repos`, `rollout_plan`, `skipped_repos`, and `warnings`. Risk
levels are `low`, `medium`, and `high`. Suggested commands come only from known
local evidence such as generated script manifests, onboarding eval expected
commands, scan command candidates, deterministic repo manifests, and the built-in
`harness validate <repo-id>` pattern. Suggested evals are existing onboarding
eval ids whose expected files overlap dependency manifests. The Markdown
artifact is a concise human readout of the same data.

The GitHub Actions `Dependency Campaign Artifacts` job runs only from
`workflow_dispatch`. It discovers active local repos with dependency manifest
evidence, skips missing or ineligible states with
`.agent-harness/dependency-campaign/discovery.json` and `SKIPPED.md`, and
uploads `.agent-harness/dependency-campaign/` as
`dependency-campaign-artifacts`. The first workflow is artifact-only. It does
not edit manifests, run package-manager upgrades, open PRs, post comments,
request reviewers, mutate approvals, publish, deploy, or block merges.

## Eval, Cache, And Export

Run local eval replay after approval:

```sh
uv run harness eval <repo-id>
uv run harness eval <repo-id> --adapter codex-local
uv run harness eval <repo-id> --development
```

Run CI-safe eval replay when a workflow or automation needs deterministic,
machine-readable output:

```sh
uv run harness eval <repo-id> --ci
uv run harness eval <repo-id> --ci --summary-path .agent-harness/ci-eval/<repo-id>.json
```

CI mode is non-interactive, fixture-adapter only, and local-only. It rejects
draft, investigation, external, inactive, unapproved, or missing-artifact repos
with a JSON summary whose `status` is `error`. Successful summaries include
`repo_id`, `status`, `baseline_pass_rate`, `skill_pack_pass_rate`,
`baseline_delta`, `rediscovery_cost_delta`, `report_path`, and `trace_path`.
Unlike local/manual eval, CI mode does not promote packs or rewrite approval
lifecycle metadata.

Refresh the repo-local cache and export for a runtime:

```sh
uv run harness cache refresh <repo-id>
uv run harness export codex <repo-id>
uv run harness export generic <repo-id>
```

Draft and investigation exports require explicit development flags:

```sh
uv run harness export generic <repo-id> --allow-draft
uv run harness export generic <repo-id> --development
```

## Runtime Session Slice

Run the current runtime loop from a workspace. Sessions are read-only unless
`--permission workspace-write` is passed:

```sh
uv run harness run "summarize this repo state"
uv run harness run "summarize this repo state" --adapter fixture
uv run harness run "inspect only" --permission read-only
```

The command starts a persisted session, assembles bounded context, and asks the
default deterministic runtime adapter for read-only tool-call and final-response
decisions. Use `--adapter fixture` to select that adapter explicitly. It prints
the session id and log path. Logs are written as JSONL under
`.agent-harness/sessions/` and include adapter decisions, observations, tool
calls, tool results, errors, and final responses.

Use workspace-write only when the adapter should be allowed to write files under
the current workspace and run known local validation commands:

```sh
uv run harness run "update a file and validate it" --permission workspace-write
ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND="codex-local" \
  uv run harness run "update a file and validate it" \
    --adapter codex-local \
    --permission workspace-write
```

Workspace-write sessions can use `local.write_file` inside the workspace and
`local.shell` for known validation commands such as `make test`, `make verify`,
`make lint`, `uv run pytest`, `uv run ruff`, and `uv run pyright`. Read/list
inspection tools remain available in both modes. Writes outside the workspace,
protected generated pack paths, full-access tools, destructive commands,
network/deployment commands, git network commands, and unknown shell commands
are denied and logged with active and required permission metadata.

Use the local subprocess-backed adapter when you have a compatible command that
reads the assembled prompt from stdin and writes exactly one JSON decision to
stdout:

```sh
ORGS_AI_HARNESS_CODEX_LOCAL_COMMAND="codex-local" \
  uv run harness run "summarize this repo state" --adapter codex-local
```

Set `ORGS_AI_HARNESS_CODEX_LOCAL_TIMEOUT=<seconds>` to override the default
30-second subprocess timeout. `codex-local` receives the selected permission
mode. It can request allowed tools or return a final response; denied tools and
commands end the session with a non-zero CLI status and a structured diagnostic.

Inspect or resume an existing session:

```sh
uv run harness run --resume --session-id <session-id>
```

Malformed model output, missing executables, non-zero subprocess exits, stderr,
timeouts, max-step stops, failed validation commands, and denied tools are
surfaced in the final diagnostic summary and written as session events. Logs
also record the selected permission mode and changed-file metadata from
successful writes. Approval prompts, full-access execution, broad autonomous
shell/network/deployment behavior, rollback, sub-agents, and context compression
remain deferred.

## Proposals And Refresh

Create and review evidence-backed proposals:

```sh
uv run harness improve <repo-id>
uv run harness refresh <repo-id>
uv run harness proposals list
uv run harness proposals show <proposal-id>
```

Apply or reject proposals explicitly:

```sh
uv run harness proposals apply <proposal-id> --yes
uv run harness proposals reject <proposal-id> --reason "Insufficient evidence"
uv run harness validate <repo-id>
uv run harness cache refresh <repo-id>
```

`refresh` creates proposals after source commit drift. It does not overwrite
accepted artifacts directly.

## Inspect State

Use `explain` for a compact state report:

```sh
uv run harness explain <repo-id>
```

The output summarizes coverage, lifecycle status, cache state, approved skills,
evals, unknowns, and proposal state.
