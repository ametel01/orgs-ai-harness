# orgs-ai-harness

CLI-first harness for generating, validating, approving, caching, and exporting
organization and repository agent skill packs.

The harness keeps generated agent guidance in a repo-local org pack, records
approval and eval state, and exports approved packs into runtime-friendly
directories only after the generated artifacts have passed validation.

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
uv run harness cache refresh <repo-id>
uv run harness export codex <repo-id>
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

Approved or verified packs can be refreshed into a repo-local
`.agent-harness/cache/` directory and exported for a runtime target such as
Codex. Draft and investigation states require explicit development flags before
export.

## Directory Map

- `src/orgs_ai_harness/`: first-party harness source.
- `tests/`: unittest-based regression tests, run through pytest by default.
- `org-agent-skills/`: tracked harness-managed org pack and generated artifacts.
- `.agent-harness/`: tracked repo-local cache/export artifacts for this harness
  repo.
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
make verify     # lint, typecheck, test
make security   # pip-audit and detect-secrets baseline check
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
