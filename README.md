# orgs-ai-harness

`orgs-ai-harness` is a CLI-first path toward a complete AI harness: a working
agent runtime that lets an LLM act through tools, feedback, memory, context
management, permissions, skills, delegation, and iteration. The canonical target
architecture is defined in [HARNESS_SPEC.md](local-docs/HARNESS_SPEC.md).

The current implemented CLI focuses on the skill-pack lifecycle inside that
runtime: generating, validating, approving, caching, and exporting organization
and repository agent skill packs. Those packs give the runtime durable operating
knowledge while the rest of the runtime loop is built out.

## Setup

Install `uv`, then sync the locked development environment:

```sh
uv sync --frozen
```

Run the CLI through the installed script:

```sh
uv run harness --help
```

The raw unittest command remains a fallback/debugging reference:

```sh
PYTHONPATH=src python3 -m unittest tests.test_org_pack_foundation
```

## Core CLI Workflows

Initialize or attach an org skill pack:

```sh
uv run harness org init --name <org-name>
uv run harness org init --repo <path-or-git-url>
```

Register repositories and inspect coverage:

```sh
uv run harness repo add <path-or-url>
uv run harness repo discover <github-owner>
uv run harness repo list
uv run harness validate
uv run harness validate <repo-id>
uv run harness explain <repo-id>
```

Generate, review, and promote repository knowledge:

```sh
uv run harness onboard <repo-id>
uv run harness approve <repo-id> --all
uv run harness reject <repo-id> --reason "<reason>"
uv run harness eval <repo-id>
uv run harness eval <repo-id> --ci --summary-path .agent-harness/ci-eval/<repo-id>.json
uv run harness review changed-files --repo-id <repo-id> --files src/app.py --json-path .agent-harness/pr-review/<repo-id>.json --markdown-path .agent-harness/pr-review/<repo-id>.md
uv run harness cache refresh <repo-id>
uv run harness export codex <repo-id>
```

Start the current deterministic runtime slice:

```sh
uv run harness run "summarize this repo state"
uv run harness run "edit then validate" --permission workspace-write --adapter codex-local
uv run harness run --resume --session-id <session-id>
```

Create and review proposed updates after source changes:

```sh
uv run harness improve <repo-id>
uv run harness refresh <repo-id>
uv run harness proposals list
uv run harness proposals show <proposal-id>
uv run harness proposals apply <proposal-id> --yes
```

## Artifact Lifecycle

Repository entries start as selected coverage in `harness.yml`. Onboarding scans
source repositories, writes draft repo artifacts under `org-agent-skills/repos/`,
and records scan evidence, unknowns, generated skills, eval fixtures, and pack
reports.

Approval is explicit. `harness approve <repo-id> --all` writes `approval.yml`,
protects approved artifact hashes, records an approval trace, and moves the repo
to `approved-unverified`. `harness eval <repo-id>` can promote an approved pack
to `verified` when replay checks pass, or keep it `approved-unverified` with
warnings when human-approved guidance has not been fully verified.
`harness eval <repo-id> --ci` is the workflow-safe replay path: it uses the
deterministic fixture adapter, emits a stable JSON summary, writes eval report
artifacts, and does not promote or rewrite approval lifecycle metadata.

`harness review changed-files` is the artifact-only PR/change review path. It
accepts explicit repo-relative files with `--files` or `--files-from`, or a
local git diff with `--base <ref> --head <ref>`, then writes deterministic JSON
and Markdown when `--json-path` and `--markdown-path` are provided. Review
artifacts include changed files, risk items, suggested local checks, suggested
eval ids, matched generated skills/resolver context, missing coverage, and
warnings. The GitHub Actions `PR Review Artifacts` job runs this command for
eligible approved or verified local repo packs and uploads
`.agent-harness/pr-review/` as `pr-review-artifacts`. It is artifact-only: it
does not post PR comments, request reviewers, mutate GitHub state, or block
merges based on risk classification.

Approved or verified packs can be refreshed into a repo-local
`.agent-harness/cache/` directory and exported for a runtime target such as
Codex. Draft and investigation states require explicit development flags before
export.

## Target Harness Architecture

The full harness architecture is broader than skill generation. The runtime must
eventually own:

- an outer act/observe/adjust loop
- context management and compression
- tool and skill registries
- sub-agent delegation with scoped permissions
- session persistence and recovery
- system prompt assembly and project context injection
- lifecycle hooks around tool execution
- permission and safety enforcement

The current CLI implements the skill, validation, trace, cache, export, proposal,
and safety-policy foundation that this runtime will use. It also includes a
first runtime vertical slice: `harness run <goal>` starts a read-only session by
default, assembles bounded workspace context, enforces tool permissions, asks
either the default deterministic fixture adapter or the subprocess-backed
`codex-local` adapter for tool-call or final-response decisions, writes adapter
decisions, observations, tool results, errors, changed-file metadata, and final
responses to an append-only session JSONL log under `.agent-harness/sessions/`,
and can inspect/resume an existing session log. `--permission workspace-write`
is an explicit opt-in for bounded local file writes and known validation
commands. Destructive, network, deployment, unknown, and full-access requests are
still denied and surfaced as diagnostics rather than approval prompts.

## Runtime Progress

Progress is tracked against the core runtime roadmap in
[`org-skill-harness-advanced-paths.md`](local-docs/org-skill-harness-advanced-paths.md)
and the skill format contract in
[`AGENTS_SKILLS_SPEC.md`](local-docs/AGENTS_SKILLS_SPEC.md).

| Area | Implemented | Deferred |
| --- | --- | --- |
| Skill lifecycle | Repo/org pack generation, validation, approval, eval replay, CI eval replay, cache, export, proposal flow | Hosted dashboard, autonomous improvement |
| Agent Skills contract | Generated `SKILL.md` frontmatter checks, directory-name matching, reference-link validation, bounded exported skill packs | Full external spec refresh automation and richer optional metadata policy |
| Runtime loop | Adapter-driven `harness run <goal>` sessions with read-only default mode, explicit `--permission workspace-write` opt-in, deterministic fixture/default adapter decisions, optional subprocess-backed `codex-local` decisions, context assembly, tool calls, observations, max-step/error safeguards, and final response events | Approval prompts, broad autonomous operation, context compression |
| Runtime persistence | Append-only session JSONL events for adapter decisions, observations, tool calls/results, errors, final responses, and recovery inspection | Durable memory model, compaction checkpoints, write-session repair |
| Runtime tools | Typed tool registry, structured results, read/list/search inspection tools, safe argv shell tool for known validation commands, and workspace-write file writes with changed-file audit metadata | Broad shell/network/deployment tools, approval-backed risky dispatch, patch transactions, rollback |
| Safety and hooks | Permission levels, command risk classification, pre-tool denial hooks, post-tool warnings, protected artifact write rejection | Interactive approval model, policy plugins, sub-agent permission scopes |

Deeper workflow and boundary notes live in:

- [User Guide](docs/user-guide.md)
- [Architecture](docs/architecture.md)
- [Runbook](docs/runbook.md)

## Directory Map

- `src/orgs_ai_harness/`: first-party harness source.
- `tests/`: unittest-based regression tests, run through pytest by default.
- `org-agent-skills/`: tracked harness-managed org pack and generated artifacts.
- `.agent-harness/`: tracked repo-local cache/export artifacts for this harness
  repo, plus CI-generated eval and PR review artifacts.
- `.github/workflows/`: CI gates for verification and security.
- `local-docs/`: ignored local planning and alignment notes.
- `.venv/`, `.coverage*`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info/`:
  local tool output excluded from normal source review.

Normal Python gates target `src/` and `tests/`. Generated pack directories and
local docs are excluded from Ruff and Pyright.

## Quality Gates

The Makefile is a thin wrapper around canonical `uv` commands:

```sh
make sync       # uv sync --frozen
make format     # Ruff format
make lint       # Ruff format check and lint
make typecheck  # Pyright basic mode
make test       # pytest
make coverage   # pytest coverage with subprocess tracing, fail_under=81
make verify     # lint, typecheck, coverage
make security   # pip-audit, Bandit, and detect-secrets baseline check
make build      # uv build
```

CI runs `make verify` on Python 3.11, 3.12, and 3.13, then runs
`make security` once after the verify matrix passes.

Pre-commit is optional contributor convenience, not the source of truth:

```sh
make pre-commit
uv run pre-commit install
```

The committed `.secrets.baseline` covers known generated-artifact findings so
new secret-like values fail the security gate without rewriting the baseline.
