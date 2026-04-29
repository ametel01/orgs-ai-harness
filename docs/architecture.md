# Architecture

`orgs-ai-harness` is being built toward the canonical harness definition in
[`local-docs/HARNESS_SPEC.md`](../local-docs/HARNESS_SPEC.md): a complete agent
runtime that lets an LLM act through tools, feedback, memory, context
management, permissions, skills, delegation, and iteration.

The current Python package implements the skill-pack lifecycle foundation for
that runtime: generating, validating, approving, caching, and exporting
organization and repository agent skill packs. Command parsing is in one
entrypoint, while implemented lifecycle behavior lives in small domain modules
under `src/orgs_ai_harness/`.

The committed `docs/` directory is the current documentation surface. MkDocs is
not introduced yet because the repository only needs a few operational pages.
Introduce a docs site when navigation, versioning, search, or published docs
become real needs.

## Module Boundaries

- `cli.py` parses commands, handles prompts, prints user-facing output, and
  delegates work to domain modules.
- `org_pack.py` resolves, initializes, and attaches org pack roots.
- `config.py` preserves and renders supported `harness.yml` top-level blocks.
- `repo_registry.py` owns `harness.yml` repository entries and coverage status
  updates.
- `repo_discovery.py` wraps GitHub discovery and optional cloning.
- `repo_onboarding.py` scans selected local repositories and creates draft
  repo pack artifacts.
- `approval.py` records explicit human approval and protected artifact hashes.
- `eval_replay.py` runs local replay checks, writes eval reports, and promotes
  packs to `verified` only when thresholds are met.
- `explain.py` renders a read-only state summary for one repository.
- `cache_manager.py` creates repo-local read-only caches and runtime exports.
- `proposals.py` records evidence-backed proposed changes without mutating
  accepted artifacts until a user applies the proposal.
- `validation.py` validates org pack shape, required metadata, approved
  artifact integrity, and coverage status.
- `artifact_schemas.py` contains shared `TypedDict` contracts for JSON/YAML-like
  artifacts.
- `llm_runner.py` is the shared subprocess helper for long-running local LLM CLI
  calls.
- `runtime_events.py`, `runtime_context.py`, `runtime_permissions.py`,
  `runtime_tools.py`, `runtime_hooks.py`, `runtime_recovery.py`, and
  `runtime_runner.py` implement the first runtime vertical slice.

Dependency direction should stay one-way: `cli.py` depends on domain modules,
domain modules depend on `repo_registry.py` and shared schemas, and validation
reads artifacts without driving generation. Avoid importing CLI concerns into
domain modules.

## Target Runtime Model

The full harness architecture must add runtime ownership around the implemented
skill lifecycle:

- outer iteration loop: observe state, choose an action, call a tool, observe the
  result, update the plan, and repeat until the task is done
- context management and compression: decide which files, messages, tool results,
  summaries, and dropped details belong in the active context
- skills and tools management: expose primitive tools plus higher-level skills
  through validated schemas, permission levels, execution paths, and result
  formats
- sub-agent management: spawn isolated child agents with focused prompts,
  restricted tools, scoped permissions, and explicit result collection
- session persistence and recovery: record messages, tool calls, tool results,
  edits, approvals, compactions, errors, and recovery markers
- system prompt assembly and project context injection: compose base
  instructions, tool descriptions, permission rules, current workspace metadata,
  project instructions, repo conventions, and available skills
- lifecycle hooks: allow structured pre-tool and post-tool policy, audit,
  redaction, and workflow checks
- permission and safety layer: enforce read-only, workspace-write, full-access,
  command-risk, and approval rules before dispatching tools

The existing modules remain useful inside this larger runtime. Skill generation,
resolver generation, validation, trace recording, cache/export, proposals, and
safety policy become runtime subsystems rather than the whole product boundary.

## Implemented Runtime Slice

`harness run <goal>` is currently deterministic and local. It does not call an
LLM, spawn sub-agents, or make autonomous edits. The slice proves the runtime
contracts that later model-driven loops will use:

- session logs are append-only JSONL files under `.agent-harness/sessions/`
- events include session ids, event ids, timestamps, event type, cwd/workspace
  metadata, and JSON-safe payloads
- context assembly records workspace metadata, git metadata, project
  instructions, harness/cache state, and bounded skill/resolver summaries
- the tool registry exposes typed tool metadata, permission requirements, and
  structured `ToolResult` payloads
- read-only mode can inspect cwd, git status, and text search results
- safe shell dispatch uses argv execution and conservative command risk
  classification; destructive, network, deployment, and unknown command classes
  are denied without higher permission support
- workspace-write file tools exist behind explicit `workspace-write`
  permission, enforce workspace boundaries, reject protected generated pack
  paths, and report changed paths for audit
- pre-tool hooks can deny dispatch and post-tool hooks can attach warnings or
  audit metadata
- recovery reads session logs, reports malformed records, pending tool calls,
  latest errors, recovery markers, and final responses

Deferred runtime features remain model planning, context compression,
sub-agent delegation, approval prompts, broad shell access, network/deployment
tools, and write-session repair beyond inspection.

## Implemented Vs Deferred

This table tracks the production docs against the local roadmap in
[`org-skill-harness-advanced-paths.md`](../local-docs/org-skill-harness-advanced-paths.md)
and the pinned Agent Skills contract in
[`AGENTS_SKILLS_SPEC.md`](../local-docs/AGENTS_SKILLS_SPEC.md). The local docs
remain planning/reference material; this page records what the current code
actually supports.

| Capability area | Implemented now | Still deferred |
| --- | --- | --- |
| Core harness runtime | A deterministic read-only `harness run <goal>` path that starts a session, assembles context, dispatches inspection tools, observes results, and records a final response | LLM-owned act/observe/adjust planning, autonomous code changes, context compression, and sub-agent delegation |
| Session persistence | Append-only JSONL event logs with stable session ids, event ids, timestamps, event types, cwd/workspace metadata, and JSON-safe payloads | Long-term memory, compaction checkpoints, cross-session retrieval, and write-session repair beyond inspection |
| Recovery | Session replay can summarize malformed records, latest recovery markers, latest errors, pending tool calls, and final responses; `harness run --resume --session-id <id>` inspects read-only sessions | Resuming model state, replaying unfinished tool outputs, and completing interrupted write sessions |
| Context assembly | Workspace, OS/date, git status, recent commits, project instructions, harness/cache state, and bounded skill/resolver summaries are returned as structured sections | Token-budget optimization, semantic retrieval, automatic summarization, and dropped-context audit trails |
| Tool registry | Runtime tools have stable ids, descriptions, input schema metadata, required permissions, callable dispatch, and structured result contracts | External tool adapters, hosted/provider tools, tool streaming, and rich schema validation beyond local metadata |
| Permission model | `read-only`, `workspace-write`, `full-access`, and `high-risk` levels exist; dispatch denies tools above the active permission; command risk is classified before shell execution | Human approval prompts, persisted approval grants, sub-agent scopes, and configurable organization policy plugins |
| Shell execution | `local.shell` uses argv-safe subprocess execution, bounded output, exit-code capture, timeout, and conservative allow/deny classification | Network, deployment, destructive, and unknown command classes without a future approval path |
| File writes | `local.write_file` is available behind `workspace-write`, checks workspace boundaries, rejects protected generated pack paths, and reports changed files | Patch application, multi-file transactions, rollback plans, and recovery that completes interrupted edits |
| Lifecycle hooks | Pre-tool hooks can deny dispatch; post-tool hooks can attach warning/audit metadata; pre-hook failures fail closed | Hook plugin discovery, external audit sinks, redaction hooks, and workflow-specific hook packs |
| Agent Skills format | Generated skills are validated for frontmatter, directory-name agreement, description length, and valid `references/` links; packs can be exported for runtime targets | Automated refresh from the live Agent Skills spec, optional metadata policy, and compatibility/allowed-tools enforcement |
| Skill-pack lifecycle | Repo discovery, onboarding, generated skills, resolver metadata, approval hashes, eval replay, cache/export, proposals, and verified pack state are implemented | Batch onboarding, PR review workflows, CI eval replay, hosted dashboard, release readiness campaigns, and autonomous improvement |

## Org Pack Layout

An org pack root contains:

- `harness.yml`: org metadata, provider placeholders, selected repositories,
  redaction configuration, and command permission policy.
- `org/`: org-level generated skills and resolver metadata.
- `repos/<repo-id>/`: generated repo-specific artifacts.
- `trace-summaries/`: append-only approval and eval event summaries.
- `proposals/<proposal-id>/`: proposed changes with evidence, metadata, and a
  patch.

In this repository, the tracked pack root is `org-agent-skills/`.

## Repository Lifecycle

Repository coverage moves through explicit states:

- `selected`: the repo is registered for coverage but has not been onboarded.
- `onboarding`: a scan or generation operation is in progress.
- `needs-investigation`: scan evidence exists but required information is
  missing or unsafe to infer.
- `draft`: generated artifacts exist and need human review.
- `approved-unverified`: a human approved the pack, but local eval replay has
  not verified it.
- `verified`: approved artifacts passed replay thresholds.
- `deactivated`: the registry entry is inactive but retained for audit history.
- `external`: the entry is a dependency reference, not selected coverage.

Onboarding starts with `harness setup`, `harness repo add`, or
`harness repo discover`. Non-interactive discovery must pass `--select`.
`harness onboard <repo-id> --scan-only` writes scan artifacts without generated
skills. `harness onboard <repo-id>` scans safe evidence, uses the configured
skill generator, and writes draft artifacts:

- `onboarding-summary.md`
- `unknowns.yml`
- `scan/scan-manifest.yml`
- `scan/hypothesis-map.yml`
- `skills/*/SKILL.md`
- `resolvers.yml`
- `evals/onboarding.yml`
- `scripts/check-pack-shape.py`
- `scripts/manifest.yml`
- `pack-report.md`

`harness validate <repo-id>` is the structural gate for generated artifacts.
`harness approve <repo-id> --all` records approval metadata and protected hashes
in `approval.yml`. `harness reject <repo-id>` records a rejection in
`approval.yml`, leaves draft artifacts intact, and moves coverage back to
`needs-investigation`. `harness eval <repo-id>` compares baseline answers
against skill-pack answers, writes `eval-report.yml`, appends eval trace
summaries, and updates approval status.

## Cache And Export Lifecycle

`harness cache refresh <repo-id>` is only allowed for `approved-unverified` and
`verified` repos. It copies approved pack content into the target repo's
`.agent-harness/cache/`, writes `metadata.json` and `pack-ref`, writes a
`.agent-harness.yml` pointer, and makes the cache read-only.

`harness export codex <repo-id>` and `harness export generic <repo-id>` export
from the cache into `.agent-harness/cache/exports/<target>/`. Draft exports
require `--allow-draft`; `needs-investigation` exports require `--development`
so runtime consumers do not accidentally use unreviewed guidance.

## Proposal Lifecycle

Accepted artifacts are protected after approval. Later learning should use the
proposal flow:

- `harness improve <repo-id>` collects redacted eval or trace evidence and
  creates a proposal when there is enough signal.
- `harness refresh <repo-id>` detects source commit drift and creates a refresh
  proposal when the source changed.
- `harness proposals list` and `harness proposals show <proposal-id>` expose
  proposed changes for review.
- `harness proposals apply <proposal-id> --yes` mutates target artifacts only
  after explicit approval.

Each proposal carries `metadata.yml`, `summary.md`, `evidence.jsonl`, and
`patch.diff`. Proposal metadata records target artifacts, affected evals, risk,
status, and source evidence.

## Redaction And Sensitive Files

Onboarding uses an allowlist-style evidence scan. It reads known safe files such
as repository docs, CI workflows, package manifests, and scripts. It skips files
whose names look sensitive, including `.env`, local config, private keys,
credentials, tokens, secrets, and key material.

Proposal evidence also applies redaction patterns from `harness.yml`:

```yaml
redaction:
  globs: []
  regexes: []
```

Keep secrets out of generated artifacts. Add redaction regexes before collecting
evidence from a repository that may contain organization-specific identifiers,
tokens, endpoints, or customer data.

## Quality Gates

The canonical local gates are:

```sh
make verify
make security
make build
```

`make verify` runs Ruff format/lint, Pyright, and coverage. `make security`
runs `pip-audit`, Bandit against `src/orgs_ai_harness`, and detect-secrets
against tracked files. `make build` verifies package build output.
