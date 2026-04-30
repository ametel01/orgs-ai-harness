# Org Skill Harness Advanced Paths

Status: post-runtime roadmap and backlog memo, not operational documentation.

The current operational documentation lives in `README.md`, `docs/architecture.md`,
`docs/runbook.md`, and `docs/user-guide.md`. This memo preserves future product
paths that should remain outside the core runtime unless their revisit trigger
is met. `HARNESS_SPEC.md` is canonical for what the harness itself must become.

## 1. Purpose

This memo preserves product and architecture paths intentionally deferred from
the core harness runtime. The runtime itself is no longer a deferred path: per
`HARNESS_SPEC.md`, the product target is a working agent runtime with an outer
loop, context management, tools and skills, session persistence, hooks,
permissions, and sub-agent delegation.

The paths below should not be built into the core runtime unless their revisit
trigger is met.

Implementation notes marked **COMPLETED** reflect behavior found in the current
source, tests, and operational docs. They do not mark the whole path complete
when only prerequisites or a narrower slice exist.

Each path records:

- what it adds
- why it is outside the core runtime
- prerequisites
- risks
- trigger for revisiting
- relationship to the core runtime architecture

## 2. Design Principles for Post-Runtime Paths

- Treat the Harness 1.0 architecture in `HARNESS_SPEC.md` as the product core.
- Push reusable judgment and process into skills.
- Push deterministic execution into explicit tools and adapters.
- Preserve stable interfaces so later paths can attach without rewriting onboarding.
- Prefer deep modules with clear ownership over interdependent shallow modules.
- Do not promote a post-runtime path into the core unless it improves the
  act/observe/adjust loop, context quality, safety, persistence, delegation, or
  skill usefulness directly.

## 3. Core Harness Runtime

Current implementation status:

- **COMPLETED**: first runtime vertical slice. `harness run <goal>` owns an
  adapter-driven local session loop with context assembly, fixture and
  `codex-local` adapters, a typed tool catalog, read-only default permission,
  explicit `workspace-write` opt-in, bounded local file writes, known validation
  shell commands, lifecycle hooks, append-only JSONL events, changed-file
  metadata, final responses, max-step/error diagnostics, and recovery
  inspection.
- Still deferred: complete autonomous runtime scope, context compression,
  approval prompts, sub-agent delegation, durable memory, broad shell/network or
  deployment tools, patch transactions, rollback, and write-session repair.

What it adds:

- A complete agent session loop owned by the harness.
- Interactive task execution, tool planning, context management, and session memory.
- Direct use of skills during normal engineering work.
- Runtime-owned tool dispatch, permission checks, lifecycle hooks, prompt
  assembly, compaction, persistence, and sub-agent delegation.

Why this is core:

- `HARNESS_SPEC.md` defines a harness as a complete agent runtime, not only a
  skill-pack lifecycle manager.
- The skill lifecycle is one subsystem inside the runtime, not the product
  boundary.
- The differentiator is the closed feedback loop: act, observe, adjust, repeat.

Prerequisites:

- Stable adapter interface.
- Stable tool registry and result format.
- Context management and compression policy.
- Session event log format.
- Safety policy engine and approval model.
- Project instruction and skill loading model.

Risks:

- Broader command execution risk.
- Much larger test matrix.
- UX complexity around long-running sessions.
- Permission mistakes if tool dispatch and hooks are underspecified.

Acceptance signal:

- The harness can run a useful local coding task through an outer loop, call
  tools, persist the session, enforce permissions, use generated skills, and
  recover after a failure.

Relationship to implemented foundation:

- Must reuse Skill Generator, Resolver Generator, Safety Policy Engine, Trace Recorder, Eval Runner, and Adapter Interface.
- Must not fork the skill format or bypass central pack validation.

## 4. CI Eval Replay

Current implementation status:

- **COMPLETED**: local eval replay. `harness eval <repo-id>` runs baseline and
  skill-pack passes, scores objective evidence, writes `eval-report.yml`,
  appends eval trace events, and can promote approved packs to `verified`.
- **COMPLETED**: ordinary CI quality gates. `.github/workflows/ci.yml` runs
  `make verify` across Python versions and `make security` after verification.
- **COMPLETED**: CI eval replay. `harness eval <repo-id> --ci` runs the
  deterministic fixture replay path without prompts, credentials, network
  adapters, or lifecycle promotion; emits a stable JSON summary; and can write
  that summary with `--summary-path`. `.github/workflows/ci.yml` discovers
  eligible approved repos, skips ineligible repos with explicit reasons, runs
  the CI command, and uploads `.agent-harness/ci-eval/` as a non-blocking
  artifact.
- Still deferred: dashboard or PR-comment presentation of eval trends beyond
  uploaded CI artifacts.

What it adds:

- Running baseline and skill-pack evals in CI.
- Regression checks for pack changes.
- Automated status reports on pull requests.

Why deferred:

- Requires decisions about checkout state, secrets, network policy, runtime isolation, cost control, and flake handling.
- Local replay is enough to prove the initial eval loop.

Prerequisites:

- Stable eval YAML schema.
- Deterministic local replay.
- Command risk tiers and persisted permissions.
- Redacted trace summaries.

Risks:

- CI secrets exposure.
- Slow or flaky eval runs.
- High setup cost for small teams.
- Confusing pass/fail semantics if LLM judging is involved.

Trigger for revisiting:

- Users accept packs through Git PRs and need automated regression checks.
- Multiple contributors update the central skill pack.

Relationship to core runtime:

- Must use the same Eval Runner, Safety Policy Engine, and Validation Engine.
- CI should not introduce a second scoring model.

## 5. Hosted Dashboard

Current implementation status:

- No completed hosted dashboard implementation found. Current visibility is
  CLI- and file-based through `harness explain`, proposal review commands,
  trace summary files, pack reports, cache metadata, and operational docs.

What it adds:

- Web UI for repo coverage, pack status, eval trends, proposals, traces, and approvals.
- Collaboration workflows for teams that do not want CLI-only review.

Why deferred:

- Hosted state conflicts with the current preference for Git-reviewed artifacts.
- Dashboard work would slow down the CLI lifecycle.

Prerequisites:

- Stable pack layout.
- Stable trace summary schema.
- Stable proposal model.
- Clear collaboration needs from users.

Risks:

- Hosted dependency.
- Auth and tenancy complexity.
- Divergence between dashboard state and Git source of truth.

Trigger for revisiting:

- Teams use the CLI successfully but struggle with proposal review, audit, or cross-team visibility.

Relationship to core runtime:

- Dashboard must treat central org skills repo as source of truth.
- Dashboard should visualize, not replace, validation and proposal flows.

## 6. Autonomous Improvement

Current implementation status:

- **COMPLETED**: proposal-first improvement primitives. `harness improve`,
  `harness refresh`, `harness proposals list`, `harness proposals show`,
  `harness proposals apply --yes`, and `harness proposals reject` collect
  redacted evidence, create reviewable proposal artifacts, and require explicit
  user approval before mutating accepted artifacts.
- Still deferred: autonomous auto-apply, continuous self-mutation, and
  automatic policy/eval/resolver changes.

What it adds:

- Automatically applying skill, resolver, eval, or policy updates from traces.
- Continuous self-improvement loops.

Why deferred:

- The current trust model requires proposal-first, human-approved learning.
- Automatic mutation could corrupt accepted knowledge or hide unsafe behavior.

Prerequisites:

- High-quality trace evidence.
- Strong validation gates.
- Mature eval coverage.
- Clear rollback model.

Risks:

- Silent drift.
- Self-confirming evals.
- Unsafe policy mutation.
- Loss of user trust.

Trigger for revisiting:

- Proposal quality is consistently high and users repeatedly approve the same classes of changes.
- Teams request configurable auto-apply for low-risk updates.

Relationship to core runtime:

- Must pass through Proposal Manager and Validation Engine.
- Should begin with narrow low-risk classes, such as reference-only updates.

## 7. Additional Repo Discovery Providers

Current implementation status:

- **COMPLETED**: GitHub discovery through the `gh` CLI, explicit
  non-interactive `--select`, interactive selection, archive/fork filters,
  optional cloning, GitHub org/user source inference, and registry reuse.
- Still deferred: GitLab, Bitbucket, local monorepo discovery, and custom SCM
  providers.

What it adds:

- GitLab, Bitbucket, local monorepo discovery, and custom SCM providers.

Why deferred:

- GitHub through `gh` is enough to validate org/profile discovery and explicit repo selection.
- Multiple providers would force premature abstraction work.

Prerequisites:

- Stable normalized repo discovery fields.
- Provider interface proven by `github-gh`.
- User demand for non-GitHub hosts.

Risks:

- Provider-specific concepts leak into registry.
- Authentication complexity.
- Inconsistent archive/fork/private semantics.

Trigger for revisiting:

- Target users cannot try the harness because their repos are not discoverable through GitHub.

Relationship to core runtime:

- Must implement the same Repo Discovery Provider interface.
- Must not change Repo Registry semantics.

## 8. Batch Onboarding

Current implementation status:

- **COMPLETED**: interactive setup can select multiple registered local repos,
  run per-repo onboarding for the selected set, continue after a per-repo
  onboarding error, and optionally run approval, development eval replay, cache
  refresh, export, and explain steps for onboarded repos.
- Still deferred: a dedicated non-interactive batch onboarding command, queueing,
  scheduling, consolidated batch reports, and first-class partial-failure
  semantics. The direct `harness onboard` command remains one repo at a time.

What it adds:

- Onboarding many selected repos in one command.
- Queueing, scheduling, partial failure handling, and consolidated reports.

Why deferred:

- Per-repo onboarding keeps review, evals, traces, and unknowns understandable.
- Batch onboarding introduces complex progress, failure, and approval UX.

Prerequisites:

- Reliable one-repo onboarding.
- Stable pack status model.
- Fast validation and replay.
- Clear resource controls.

Risks:

- Noisy generated proposals.
- Hard-to-review changes.
- Expensive or long-running local commands.
- Confusing partial failures.

Trigger for revisiting:

- Users successfully onboard several repos one by one and ask to automate the repeated sequence.

Relationship to core runtime:

- Must reuse the same per-repo lifecycle.
- Should produce per-repo reports plus an aggregate summary.

## 9. PR/Review Workflow

Current implementation status:

- **COMPLETED**: generated pack review and approval flow. `harness approve`
  renders review state, supports approving all or excluding generated
  artifacts, records protected hashes and approval traces, and `harness reject`
  records explicit rejection without mutating accepted artifacts.
- **COMPLETED**: artifact-only PR/change review workflow. `harness review
  changed-files` accepts explicit changed-file sets, files-from inputs, or local
  git base/head refs; classifies changed-file risk; suggests local checks from
  known evidence; suggests matching eval ids; maps changed files to generated
  skills, resolver context, scan evidence, unknowns, and missing coverage; and
  writes deterministic JSON and Markdown artifacts. `.github/workflows/ci.yml`
  runs a pull-request-only `PR Review Artifacts` job that discovers eligible
  approved or verified local repo packs, uploads `.agent-harness/pr-review/`,
  and skips ineligible repos with explicit artifact output.
- Still deferred: PR comments, reviewer assignment, dashboard presentation,
  merge-blocking policy, autonomous review decisions, and deeper SCM platform
  integrations beyond uploaded GitHub Actions artifacts.

What it adds:

- Applying repo skills and org standards to pull request review.
- Risk classification, review checklists, and suggested evals based on changed files.

Why deferred:

- The first implemented slice is intentionally artifact-only and local-first.
- Commenting, reviewer assignment, dashboards, and merge-blocking behavior need
  stronger trust semantics and platform-specific policy.

Prerequisites:

- Verified repo skills.
- Org engineering standards.
- Resolver behavior for changed files.
- Command safety policy for validation commands.

Risks:

- False confidence in reviews.
- Review noise.
- Platform-specific integration complexity.

Trigger for revisiting:

- Users have verified repo packs and want the next frequent workflow that benefits from them.

Relationship to core runtime:

- Natural first post-onboarding expansion.
- Should reuse resolvers, evals, command policy, and traces.

## 10. Release Readiness

Current implementation status:

- **COMPLETED**: artifact-only release readiness command and workflow.
  `harness release readiness --repo-id <repo-id>` accepts optional release
  identifiers, explicit changed files, file lists, or local git ranges, then
  writes deterministic JSON and Markdown artifacts when `--json-path` and
  `--markdown-path` are provided.
- **COMPLETED**: release readiness context and risk artifacts. Artifacts include
  lifecycle status, approval/eval evidence, pack metadata, unknowns, scan
  evidence, generated skills/resolvers, local release evidence, missing
  evidence, stable `low`/`medium`/`high` risk items, suggested local checks, and
  suggested eval ids.
- **COMPLETED**: GitHub Actions artifact-only release readiness workflow.
  `.github/workflows/ci.yml` exposes a `workflow_dispatch` job that discovers
  the matching eligible approved or verified local pack, runs the artifact
  command, uploads `.agent-harness/release-readiness/`, and writes explicit skip
  artifacts when no eligible repo is found.
- Still deferred: release tags, publishing, deployments, GitHub Release
  creation, comments, dashboards, reviewer requests, and merge-blocking release
  governance.

What it adds:

- Local release readiness artifacts.
- Version, changelog, CI, migration, deployment, approval, eval, and missing
  evidence summaries.
- Release risk summary and suggested local checks/evals without executing those
  checks.

Why deferred:

- Publishing, deployment, issue-tracker, changelog mutation, and merge policy
  integrations still require more mature governance decisions.

Prerequisites:

- Verified repo skill packs.
- Release policy org skills.
- Command permissions for build/test/release checks.

Risks:

- High operational trust requirements.
- Incorrect release guidance can be costly.
- External system dependencies.

Trigger for revisiting:

- PR/review workflow is working and users want release governance.

Relationship to core runtime:

- Should reuse org skills, repo skills, resolvers, eval traces, and safety policy.

## 11. Dependency Upgrade Campaign

Current implementation status:

- **COMPLETED**: onboarding scan evidence recognizes dependency manifests such
  as `requirements.txt`, lockfiles, and package manifests, and generated skills
  can capture repo-specific dependency or quality-gate guidance.
- **COMPLETED**: artifact-only dependency campaign first slice. `harness
  dependency campaign` accepts a campaign name and optional package filters,
  resolves active non-external local repos, collects local dependency manifests
  and lockfiles, represents malformed or missing evidence explicitly, classifies
  conservative risk, suggests commands and eval ids only from known local
  evidence, produces deterministic rollout ordering, and writes stable JSON and
  Markdown artifacts. `.github/workflows/ci.yml` exposes a manual
  `workflow_dispatch` job that uploads `.agent-harness/dependency-campaign/` as
  `dependency-campaign-artifacts` and skips missing org packs, missing local
  paths, or no dependency manifest evidence with reviewable artifacts.
- Still deferred: dependency file edits, package-manager upgrades, registry
  lookups, PR creation, comments, dashboards, auto-merge, merge-blocking policy,
  campaign tracking beyond uploaded artifacts, and regression reporting beyond
  suggested local checks/evals.

What it adds:

- Cross-repo dependency inventory.
- Upgrade planning.
- Rollout policy.
- Validation and regression tracking.

Why deferred:

- Requires multiple covered repos and cross-repo dependency graphing.

Prerequisites:

- Several verified repos.
- Dependency manifest extraction.
- Cross-repo reporting.
- Upgrade policy skills.

Risks:

- Large blast radius.
- Package-manager-specific complexity.
- Partial upgrade failures.

Trigger for revisiting:

- Teams have multiple covered repos and repeatedly run dependency campaigns.

Relationship to core runtime:

- Builds on selected repo registry, repo skills, evals, and traces.

## 12. Bug or Customer Investigation

Current implementation status:

- **COMPLETED**: internal evidence primitives that a future investigation flow
  can reuse, including redacted trace evidence, proposal evidence files,
  unknowns, scan manifests, hypothesis maps, and `harness explain`.
- Still deferred: customer/support data integrations, observability/log inputs,
  evidence diarization as a workflow, hypothesis tracking across external data,
  and incident/customer privacy controls beyond current redaction primitives.

What it adds:

- Investigation workflows combining repo context, customer reports, logs, support tickets, and product concepts.
- Evidence diarization and hypothesis tracking.

Why deferred:

- Requires external customer/support data and stronger evidence handling.
- Higher privacy and redaction demands.

Prerequisites:

- Mature redaction policy.
- Stable trace and evidence schemas.
- Integrations with support or observability systems.

Risks:

- Sensitive customer data exposure.
- Weak evidence chains.
- Hallucinated root causes.

Trigger for revisiting:

- Users trust repo onboarding and want the harness to connect repo knowledge to real incidents or customer reports.

Relationship to core runtime:

- Should reuse skills, traces, unknowns, and proposal-first learning.

## 13. Incident Response

Current implementation status:

- **COMPLETED**: reusable primitives for a future incident workflow, including
  command risk classification, permission-gated runtime tools, denied-tool
  diagnostics, append-only session logs, trace summaries, proposal review, and
  redaction support.
- Still deferred: incident-specific runbooks, timeline tooling, observability
  integrations, postmortem support, and operational incident command policies.

What it adds:

- Incident runbooks, timelines, command policies, postmortem support, and evidence collection.

Why deferred:

- Incident workflows are high-trust and high-pressure.
- Requires stronger audit, permissions, integrations, and operational safety.

Prerequisites:

- Verified release and review workflows.
- Strong safety policy.
- Observability integrations.
- Mature audit trail.

Risks:

- Bad advice during incidents.
- Unsafe commands.
- Missing or mishandled evidence.

Trigger for revisiting:

- Users have mature harness adoption and request incident-specific workflows.

Relationship to core runtime:

- Must build on safety, trace, redaction, and proposal systems.

## 14. Harness Optimization Lab

Current implementation status:

- **COMPLETED**: measurement inputs exist through local eval scoring, baseline
  versus skill-pack metrics, rediscovery-cost deltas, eval trace summaries,
  runtime session JSONL logs, bounded context/prompt assembly, and proposal
  evidence.
- Still deferred: an experiment runner, benchmark task suites, strategy
  comparison reports, prompt/resolver/context-budget experiment tracking, and
  optimization dashboards.

What it adds:

- Systematic experiments over traces, evals, prompt shapes, resolver rules, adapter behavior, and context budgets.
- Measurement of harness changes against benchmark tasks.

Why deferred:

- Requires enough usage data and stable eval suites to be meaningful.

Prerequisites:

- Verified packs across multiple repos.
- Stable eval runner.
- Trace summaries with comparable metrics.

Risks:

- Premature optimization.
- Overfitting to small eval sets.
- Complexity in experiment tracking.

Trigger for revisiting:

- Teams have enough traces and evals to compare harness strategies.

Relationship to core runtime:

- Directly builds on traces, evals, scoring, and proposal-first improvement.

## 15. Semantic Pack Versioning and Marketplace

Current implementation status:

- **COMPLETED**: reproducible local pack references. Cache metadata records
  pack refs and source pack refs, `harness.yml` validates `org.skills_version:
  1`, and cache/export directories preserve approved pack content for runtime
  targets.
- Still deferred: semantic pack versions, published pack releases,
  compatibility policy, public distribution, shared/community packs, and a
  marketplace.

What it adds:

- Semantic versions for skill packs.
- Published releases.
- Shared/community packs or marketplace.

Why deferred:

- The initial pack lifecycle uses Git commit SHAs for reproducibility.
- Public compatibility contracts are premature.

Prerequisites:

- Stable pack format.
- Compatibility policy.
- Distribution and trust model.

Risks:

- Versioning overhead.
- Broken compatibility promises.
- Supply chain concerns.

Trigger for revisiting:

- Skill packs need to be shared outside a single org or consumed by many downstream repos.

Relationship to core runtime:

- Must preserve Agent Skills compatibility and validation.

**COMPLETED**
