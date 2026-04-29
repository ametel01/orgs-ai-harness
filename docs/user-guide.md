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

## Eval, Cache, And Export

Run local eval replay after approval:

```sh
uv run harness eval <repo-id>
uv run harness eval <repo-id> --adapter codex-local
uv run harness eval <repo-id> --development
```

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
