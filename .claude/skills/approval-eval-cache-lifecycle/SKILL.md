---
name: approval-eval-cache-lifecycle
description: Use this skill when changing draft approval, protected artifact metadata, eval replay, verification thresholds, repo-local cache refresh, export policy, or explain output.
---

# Approval Eval Cache Lifecycle

## Purpose

Maintain the trust lifecycle after a draft pack exists: human review, approval or rejection, local eval replay, verified status decisions, pinned repo-local caches, managed exports, and explain output.

## When To Use

Use this for `approval.py`, `eval_replay.py`, `cache_manager.py`, `explain.py`, status transitions, approval metadata, eval scoring, cache metadata, or export rules.

## Inspect First

- `src/orgs_ai_harness/approval.py` for review views, exclusions, protected artifacts, and approval traces.
- `src/orgs_ai_harness/eval_replay.py` for adapter contract, scoring, thresholds, traces, and report updates.
- `src/orgs_ai_harness/cache_manager.py` for cache refresh and export policy.
- `src/orgs_ai_harness/explain.py` for displayed state and boundary decisions.
- `local-docs/development-phases/sprint-06-review-approval-and-protected-artifacts.md`, `sprint-07-local-eval-replay-and-verification.md`, and the full application guide.

## Repository Map

- Draft artifacts live in `org-agent-skills/repos/<repo-id>/`.
- Approval metadata: `approval.yml`.
- Eval report: `eval-report.yml`; eval traces append to `trace-summaries/eval-events.jsonl`.
- Repo-local cache: `<covered-repo>/.agent-harness/cache/`.
- Runtime exports: `<covered-repo>/.agent-harness/cache/exports/<target>/`.

## Standard Workflow

1. Review draft artifacts with `approve <repo-id>` before accepting anything.
2. Approval writes protected artifact hashes and transitions to `approved-unverified`.
3. Rejection preserves draft artifacts and records a trace.
4. Eval refuses drafts unless `--development` is passed.
5. Verification requires approved artifacts and approved evals, then applies thresholds.
6. Cache refresh is allowed only for `approved-unverified` or `verified` packs.
7. Export reads from the pinned cache and enforces draft/development policy flags.
8. Explain should show coverage, status, cache, approved skills, evals, unknowns, proposals, and boundary decisions.

## Thresholds

- `verified` requires no blocking unknowns or safety failures and either at least `0.20` pass-rate improvement or at least `0.30` rediscovery-cost reduction.
- Otherwise approved packs remain `approved-unverified` unless failures force `needs-investigation`.

## Validation Commands

```bash
PYTHONPATH="$PWD/src" python3 -m unittest tests.test_org_pack_foundation.RepoOnboardingTests
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness validate <repo-id>
```

Use concrete CLI smoke flows in temp fixtures for lifecycle changes:

```bash
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness approve <repo-id> --all
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness eval <repo-id>
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness cache refresh <repo-id>
PYTHONPATH="$PWD/src" python3 -m orgs_ai_harness export codex <repo-id>
```

## Invariants

- Approval is explicit; draft packs are not trusted by generation alone.
- Protected artifact hashes must match approved artifacts exactly.
- Development evals and development exports must not imply verification.
- Cache metadata records pack status, pack ref, source pack ref, org skill pack path, warnings, and applied proposals.
- Cache and exports are made read-only after refresh/export.

## Common Pitfalls

- Allowing onboarding regeneration to overwrite approved artifacts.
- Marking a pack verified despite open blocking unknowns.
- Exporting stale cache after applied proposals.
- Forgetting approved-unverified warning metadata.
- Changing report fields without updating explain and tests.

## Escalation

If introducing a new adapter or export target, keep the fixture adapter deterministic and add policy checks before enabling broader execution.
