# Architecture

`orgs-ai-harness` is a CLI-first Python package for generating, validating,
approving, caching, and exporting organization and repository agent skill packs.
The repo intentionally keeps the architecture simple: command parsing is in one
entrypoint, while lifecycle behavior lives in small domain modules under
`src/orgs_ai_harness/`.

The committed `docs/` directory is the current documentation surface. MkDocs is
not introduced yet because the repository only needs a few operational pages.
Introduce a docs site when navigation, versioning, search, or published docs
become real needs.

## Module Boundaries

- `cli.py` parses commands, handles prompts, prints user-facing output, and
  delegates work to domain modules.
- `config.py` locates and initializes org pack roots.
- `repo_registry.py` owns `harness.yml` repository entries and coverage status
  updates.
- `repo_discovery.py` wraps GitHub discovery and optional cloning.
- `repo_onboarding.py` scans selected local repositories and creates draft
  repo pack artifacts.
- `approval.py` records explicit human approval and protected artifact hashes.
- `eval_replay.py` runs local replay checks, writes eval reports, and promotes
  packs to `verified` only when thresholds are met.
- `cache_manager.py` creates repo-local read-only caches and runtime exports.
- `proposals.py` records evidence-backed proposed changes without mutating
  accepted artifacts until a user applies the proposal.
- `validation.py` validates org pack shape, required metadata, approved
  artifact integrity, and coverage status.
- `artifact_schemas.py` contains shared `TypedDict` contracts for JSON/YAML-like
  artifacts.
- `llm_runner.py` is the shared subprocess helper for long-running local LLM CLI
  calls.

Dependency direction should stay one-way: `cli.py` depends on domain modules,
domain modules depend on `repo_registry.py` and shared schemas, and validation
reads artifacts without driving generation. Avoid importing CLI concerns into
domain modules.

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
- `external`: the entry is a dependency reference, not selected coverage.

Onboarding starts with `harness repo add` or `harness repo discover`, then
`harness onboard <repo-id>` scans safe evidence and writes draft artifacts:

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
in `approval.yml`. `harness eval <repo-id>` compares baseline answers against
skill-pack answers, writes `eval-report.yml`, appends eval trace summaries, and
updates approval status.

## Cache And Export Lifecycle

`harness cache refresh <repo-id>` is only allowed for `approved-unverified` and
`verified` repos. It copies approved pack content into the target repo's
`.agent-harness/cache/`, writes `metadata.json` and `pack-ref`, writes a
`.agent-harness.yml` pointer, and makes the cache read-only.

`harness export codex <repo-id>` and `harness export generic <repo-id>` export
from the cache into `.agent-harness/cache/exports/<target>/`. Draft and
investigation exports require explicit development flags so runtime consumers do
not accidentally use unreviewed guidance.

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
