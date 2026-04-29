# Python Best Practices Repository Evaluation

This note evaluates the current repository against
`local-docs/PYTHON_BEST_PRACTICES.md`.

Last refreshed: 2026-04-29.

## Current State

- The repository is a Python CLI package using a `src/` layout.
- `uv` is the standard dependency and environment workflow.
- `pyproject.toml` defines project metadata, the `harness` script entrypoint,
  setuptools build backend, Ruff, Pyright, pytest, and coverage settings.
- `uv.lock` is committed.
- The Makefile is the canonical local command surface for sync, lint,
  typecheck, test, coverage, verify, security, pre-commit, and build.
- CI runs `make verify` on Python 3.11, 3.12, and 3.13, then runs
  `make security` after the verify matrix passes.
- Runtime code uses type annotations, dataclasses, and shared `TypedDict`
  artifact contracts where JSON-like artifacts cross module boundaries.
- The former monolithic test suite has been split into focused pytest files.
- Property-based tests cover important artifact and parsing edge behavior.
- README documents setup, core workflows, artifact lifecycle, directory map, and
  quality gates.
- Committed docs now cover architecture, lifecycle, redaction policy, and
  troubleshooting runbooks.

## Completed Best-Practice Work

The following gaps identified in the earlier evaluation are now closed:

- Dependency workflow: `uv` is configured and `uv.lock` is committed.
- Linting and formatting: Ruff is configured and part of `make verify`.
- Type checking: Pyright is configured and part of `make verify`.
- Testing: pytest is the standard runner.
- Coverage: `pytest-cov` is configured with subprocess tracing and
  `fail_under = 81`.
- CI: GitHub Actions runs the verification and security gates.
- Security: `pip-audit`, tuned Bandit, and detect-secrets run through
  `make security`.
- Local command alignment: Makefile targets mirror CI behavior.
- Pre-commit: optional contributor hooks are configured.
- Artifact typing: shared schema contracts live in
  `src/orgs_ai_harness/artifact_schemas.py`.
- Test organization: the foundation test suite is split by domain.
- Property testing: Hypothesis covers artifact edges and path/parser behavior.
- LLM subprocess reuse: long-running local LLM process handling is centralized
  in `src/orgs_ai_harness/llm_runner.py`.
- CLI maintainability: command handling is split into focused handler functions.
- Documentation: README, `docs/architecture.md`, and `docs/runbook.md` cover
  the currently supported workflows.

## Current Quality Gates

Canonical local commands:

```sh
make sync
make verify
make security
make build
```

`make verify` runs:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q --cov=orgs_ai_harness --cov-report=term-missing
```

`make security` runs:

```sh
uv run pip-audit
uv run bandit -r src/orgs_ai_harness
uv run detect-secrets-hook --baseline .secrets.baseline $(git ls-files)
```

`make build` runs:

```sh
uv build
```

## Remaining Gaps

These are follow-up opportunities, not blockers for the baseline Python quality
stack.

### Documentation Publishing

The repository now has committed Markdown docs, but no generated docs site.
That is acceptable for the current size. Introduce MkDocs only when the project
needs navigation, search, published docs, or versioned documentation.

### Type Strictness

Pyright currently runs in basic mode. This is a practical baseline. Future work
can raise strictness module by module, starting with modules that exchange
artifact dictionaries or parse `harness.yml`.

### Structured Logging

Long-running operations still mostly report progress through user-facing
printing. That is acceptable for a CLI, but structured logging would help if the
harness grows into a service, background worker, or larger automation surface.

### Multi-Version Local Matrix

CI already tests Python 3.11, 3.12, and 3.13. A local `tox` or `nox` matrix is
not necessary yet, but it may become useful if contributors need to reproduce
the full CI matrix locally.

### Security Depth

The current security gate covers dependency audit, tuned Python SAST, and
secret scanning. Optional future additions:

- Semgrep for broader custom SAST rules.
- SBOM generation with CycloneDX tooling.
- Dependabot or Renovate for dependency update automation.

### Performance Guardrails

No concrete performance regression target exists yet. Likely future benchmark
targets are repository scanning, validation, proposal rendering, and cache
export. Add `pytest-benchmark` only after one of those paths has a measurable
target and a regression threshold.

## Updated Recommended Order

All baseline items from the original evaluation have been implemented. Future
work should be ordered by need rather than by baseline setup:

1. Raise Pyright strictness in high-risk modules as they are touched.
2. Add structured logging if CLI progress output becomes hard to consume.
3. Add MkDocs when docs need publishing or search.
4. Add Semgrep, SBOM generation, or dependency-update automation if security
   review requires them.
5. Add benchmark tests only after a specific performance target is accepted.

## Closed Issue Mapping

- #74: Coverage is enforced by `make verify`.
- #75: Artifact schema contracts are typed.
- #76: Tests are split by domain.
- #77: Property checks cover artifact edge behavior.
- #78: LLM subprocess progress handling is shared.
- #79: CLI command handlers are split.
- #80: Tuned Bandit SAST is part of `make security`.
- #81: Architecture and runbook docs are committed.
- #82: This evaluation document reflects the current repository state.
