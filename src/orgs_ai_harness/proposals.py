"""Proposal generation and read-only review helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ProposalError(Exception):
    """Raised when proposal operations cannot be completed."""


@dataclass(frozen=True)
class ImproveResult:
    repo_id: str
    proposal_id: str | None
    proposal_root: Path | None
    reason: str | None = None


@dataclass(frozen=True)
class RefreshResult:
    repo_id: str
    proposal_id: str | None
    proposal_root: Path | None
    previous_commit: str
    current_commit: str
    reason: str | None = None


@dataclass(frozen=True)
class ProposalSummary:
    proposal_id: str
    repo_id: str
    status: str
    risk: str
    summary: str
    proposal_root: Path


@dataclass(frozen=True)
class ProposalDecisionResult:
    proposal_id: str
    repo_id: str
    status: str
    changed_artifacts: tuple[str, ...]


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password)([\"'\s:=]+)([^\"'\s,}]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def improve_repo(root: Path, repo_id: str) -> ImproveResult:
    """Create the first evidence-backed proposal for a repo when traces justify it."""

    root = root.resolve()
    entry = _find_repo(root, repo_id)
    evidence = _collect_evidence(root, entry.id)
    if not evidence:
        return ImproveResult(
            repo_id=entry.id,
            proposal_id=None,
            proposal_root=None,
            reason="insufficient evidence",
        )

    proposal_id = _next_proposal_id(root / "proposals")
    proposal_root = root / "proposals" / proposal_id
    proposal_root.mkdir(parents=True)

    metadata = _proposal_metadata(root, entry, proposal_id, evidence)
    summary = _render_summary(metadata, evidence)
    target = str(metadata["target_artifacts"][0])
    patch = _render_patch(target)

    (proposal_root / "summary.md").write_text(summary, encoding="utf-8")
    (proposal_root / "evidence.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in evidence),
        encoding="utf-8",
    )
    (proposal_root / "patch.diff").write_text(patch, encoding="utf-8")
    (proposal_root / "metadata.yml").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return ImproveResult(repo_id=entry.id, proposal_id=proposal_id, proposal_root=proposal_root)


def refresh_repo(root: Path, repo_id: str) -> RefreshResult:
    """Detect source changes and create a refresh proposal without mutating accepted artifacts."""

    root = root.resolve()
    entry = _find_repo(root, repo_id)
    if entry.local_path is None:
        raise ProposalError(f"repo {entry.id} has no local path; run 'harness repo set-path'")
    previous_commit = _last_recorded_source_commit(root, entry.id)
    current_commit = _current_source_commit(root, entry)
    if previous_commit == "unknown":
        return RefreshResult(
            repo_id=entry.id,
            proposal_id=None,
            proposal_root=None,
            previous_commit=previous_commit,
            current_commit=current_commit,
            reason="no recorded onboarding source commit",
        )
    if current_commit == previous_commit:
        return RefreshResult(
            repo_id=entry.id,
            proposal_id=None,
            proposal_root=None,
            previous_commit=previous_commit,
            current_commit=current_commit,
            reason="source unchanged",
        )

    proposal_id = _next_proposal_id(root / "proposals")
    proposal_root = root / "proposals" / proposal_id
    proposal_root.mkdir(parents=True)
    affected_evals = _affected_eval_ids(root, entry.id)
    target = (root / "repos" / entry.id / "onboarding-summary.md").relative_to(root).as_posix()
    metadata = {
        "schema_version": 1,
        "id": proposal_id,
        "repo_id": entry.id,
        "status": "open",
        "risk": "medium",
        "proposal_type": "onboarding summary updates",
        "target_artifacts": [target],
        "affected_evals": affected_evals,
        "evidence": [f"refresh:{entry.id}:{previous_commit}..{current_commit}"],
        "created_from": ["source_refresh"],
        "created_at": datetime.now(UTC).isoformat(),
        "previous_source_commit": previous_commit,
        "current_source_commit": current_commit,
    }
    evidence = [
        {
            "created_from": "source_refresh",
            "repo_id": entry.id,
            "previous_source_commit": previous_commit,
            "current_source_commit": current_commit,
            "affected_evals": affected_evals,
        }
    ]
    (proposal_root / "summary.md").write_text(_render_refresh_summary(metadata), encoding="utf-8")
    (proposal_root / "evidence.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in evidence),
        encoding="utf-8",
    )
    (proposal_root / "patch.diff").write_text(_render_patch(target), encoding="utf-8")
    _write_metadata(proposal_root, metadata)
    return RefreshResult(
        repo_id=entry.id,
        proposal_id=proposal_id,
        proposal_root=proposal_root,
        previous_commit=previous_commit,
        current_commit=current_commit,
    )


def list_proposals(root: Path) -> tuple[ProposalSummary, ...]:
    """Return proposal summaries sorted by id."""

    root = root.resolve()
    proposals_root = root / "proposals"
    if not proposals_root.is_dir():
        return ()
    summaries: list[ProposalSummary] = []
    for proposal_root in sorted(path for path in proposals_root.iterdir() if path.is_dir()):
        metadata = _load_metadata(proposal_root)
        summaries.append(
            ProposalSummary(
                proposal_id=str(metadata.get("id", proposal_root.name)),
                repo_id=str(metadata.get("repo_id", "")),
                status=str(metadata.get("status", "unknown")),
                risk=str(metadata.get("risk", "unknown")),
                summary=_first_summary_line(proposal_root / "summary.md"),
                proposal_root=proposal_root,
            )
        )
    return tuple(summaries)


def render_proposal_show(root: Path, proposal_id: str) -> str:
    """Render a compact proposal detail view."""

    root = root.resolve()
    normalized_id = proposal_id.strip()
    if not normalized_id:
        raise ProposalError("proposal id cannot be empty")
    proposal_root = root / "proposals" / normalized_id
    if not proposal_root.is_dir():
        raise ProposalError(f"proposal id is not found: {normalized_id}")
    metadata = _load_metadata(proposal_root)
    evidence_refs = metadata.get("evidence")
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    diff_lines = _compact_diff(proposal_root / "patch.diff")

    lines = [
        f"Proposal: {metadata.get('id', normalized_id)}",
        f"Repo: {metadata.get('repo_id', '')}",
        f"Status: {metadata.get('status', 'unknown')}",
        f"Risk: {metadata.get('risk', 'unknown')}",
        "",
        "Summary",
        _first_summary_line(proposal_root / "summary.md"),
        "",
        "Evidence References",
        *[f"- {item}" for item in evidence_refs],
        "",
        "Compact Diff",
        *diff_lines,
    ]
    return "\n".join(lines) + "\n"


def apply_proposal(root: Path, proposal_id: str, *, approved: bool = False) -> ProposalDecisionResult:
    """Apply an open proposal after explicit user approval."""

    if not approved:
        raise ProposalError("proposal apply requires explicit approval; pass --yes")
    root = root.resolve()
    proposal_root = _proposal_root(root, proposal_id)
    metadata = _load_metadata(proposal_root)
    _ensure_open(metadata, proposal_root)
    repo_id = _metadata_string(metadata, "repo_id", proposal_root)
    target_artifacts = _metadata_string_list(metadata, "target_artifacts", proposal_root)
    patch = _parse_patch(proposal_root / "patch.diff")
    if patch.target not in target_artifacts:
        raise ProposalError(f"patch target is not listed in proposal metadata: {patch.target}")
    target_path = root / patch.target
    if not target_path.is_file():
        raise ProposalError(f"proposal target artifact is missing: {patch.target}")
    before = target_path.read_text(encoding="utf-8")
    after = _apply_append_patch(before, patch.added_lines)
    target_path.write_text(after, encoding="utf-8")
    _update_approval_hashes(root, repo_id, [patch.target])
    metadata.update(
        {
            "status": "applied",
            "applied_at": datetime.now(UTC).isoformat(),
            "applied_artifacts": [patch.target],
        }
    )
    _write_metadata(proposal_root, metadata)
    return ProposalDecisionResult(
        proposal_id=str(metadata.get("id", proposal_id)),
        repo_id=repo_id,
        status="applied",
        changed_artifacts=(patch.target,),
    )


def reject_proposal(root: Path, proposal_id: str, *, reason: str) -> ProposalDecisionResult:
    """Reject an open proposal while preserving all target artifacts."""

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ProposalError("proposal rejection reason cannot be empty")
    root = root.resolve()
    proposal_root = _proposal_root(root, proposal_id)
    metadata = _load_metadata(proposal_root)
    _ensure_open(metadata, proposal_root)
    repo_id = _metadata_string(metadata, "repo_id", proposal_root)
    metadata.update(
        {
            "status": "rejected",
            "rejected_at": datetime.now(UTC).isoformat(),
            "rejection_reason": normalized_reason,
        }
    )
    _write_metadata(proposal_root, metadata)
    return ProposalDecisionResult(
        proposal_id=str(metadata.get("id", proposal_id)),
        repo_id=repo_id,
        status="rejected",
        changed_artifacts=(),
    )


def _find_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ProposalError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if not entry.active:
            raise ProposalError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.external or entry.coverage_status == "external":
            raise ProposalError(f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}")
        return entry
    raise ProposalError(f"repo id is not registered: {normalized_repo_id}")


def _collect_evidence(root: Path, repo_id: str) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    trace_root = root / "trace-summaries"
    for trace_path in sorted(trace_root.glob("*.jsonl")):
        for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("repo_id") != repo_id:
                continue
            evidence_item = _event_to_evidence(root, trace_path, line_number, event)
            if evidence_item is not None:
                evidence.append(evidence_item)
    return evidence


def _event_to_evidence(root: Path, trace_path: Path, line_number: int, event: dict[str, object]) -> dict[str, object] | None:
    event_type = event.get("event_type")
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    created_from: str | None = None
    if event_type == "scoring" and payload.get("passed") is False:
        created_from = "eval_failure"
    elif event_type == "command_approval":
        created_from = "command_correction"
    elif event_type == "approval":
        created_from = "user_approval_comment"
    if created_from is None:
        return None

    relative_trace = trace_path.relative_to(root).as_posix()
    return {
        "created_from": created_from,
        "event_type": event_type,
        "event_id": event.get("event_id"),
        "trace": f"{relative_trace}:{line_number}",
        "payload": _redact_jsonable(payload),
    }


def _proposal_metadata(
    root: Path,
    entry: RepoEntry,
    proposal_id: str,
    evidence: list[dict[str, object]],
) -> dict[str, object]:
    created_from = sorted({str(item["created_from"]) for item in evidence})
    affected_evals = sorted(
        {
            str(payload.get("task_id"))
            for item in evidence
            if isinstance(item.get("payload"), dict)
            for payload in (item["payload"],)
            if isinstance(payload.get("task_id"), str) and payload.get("task_id")
        }
    )
    target = _target_artifact(root, entry)
    return {
        "schema_version": 1,
        "id": proposal_id,
        "repo_id": entry.id,
        "status": "open",
        "risk": "medium" if "eval_failure" in created_from else "low",
        "proposal_type": "unknown updates",
        "target_artifacts": [target],
        "affected_evals": affected_evals,
        "evidence": [str(item["trace"]) for item in evidence],
        "created_from": created_from,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _target_artifact(root: Path, entry: RepoEntry) -> str:
    preferred = root / "repos" / entry.id / "skills" / "build-test-debug" / "SKILL.md"
    if preferred.is_file():
        return preferred.relative_to(root).as_posix()
    return (root / "repos" / entry.id / "onboarding-summary.md").relative_to(root).as_posix()


def _render_summary(metadata: dict[str, object], evidence: list[dict[str, object]]) -> str:
    sources = ", ".join(str(item) for item in metadata.get("created_from", []))
    target_artifacts = metadata.get("target_artifacts")
    targets = target_artifacts if isinstance(target_artifacts, list) else []
    return "\n".join(
        [
            f"# Proposal {metadata['id']}: evidence-backed update for {metadata['repo_id']}",
            "",
            f"- Status: {metadata['status']}",
            f"- Risk: {metadata['risk']}",
            f"- Created From: {sources}",
            f"- Evidence Items: {len(evidence)}",
            "",
            "## Target Artifacts",
            *[f"- {target}" for target in targets],
            "",
            "## Rationale",
            "Trace and review evidence indicate the accepted repo knowledge may need a human-reviewed update.",
        ]
    ) + "\n"


def _render_refresh_summary(metadata: dict[str, object]) -> str:
    return "\n".join(
        [
            f"# Proposal {metadata['id']}: refresh updates for {metadata['repo_id']}",
            "",
            f"- Status: {metadata['status']}",
            f"- Risk: {metadata['risk']}",
            f"- Previous Source Commit: {metadata['previous_source_commit']}",
            f"- Current Source Commit: {metadata['current_source_commit']}",
            "",
            "## Rationale",
            "The repository source changed since the last recorded onboarding scan. Review the proposed update before mutating accepted artifacts.",
        ]
    ) + "\n"


def _render_patch(target: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{target} b/{target}",
            f"--- a/{target}",
            f"+++ b/{target}",
            "@@",
            "+<!-- Proposal note: review recent trace evidence before changing accepted knowledge. -->",
            "",
        ]
    )


def _next_proposal_id(proposals_root: Path) -> str:
    proposals_root.mkdir(parents=True, exist_ok=True)
    highest = 0
    for path in proposals_root.iterdir():
        if not path.is_dir() or not path.name.startswith("prop_"):
            continue
        try:
            highest = max(highest, int(path.name.removeprefix("prop_")))
        except ValueError:
            continue
    return f"prop_{highest + 1:03d}"


def _load_metadata(proposal_root: Path) -> dict[str, object]:
    metadata_path = proposal_root / "metadata.yml"
    if not metadata_path.is_file():
        raise ProposalError(f"proposal metadata is missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProposalError(f"proposal metadata is malformed: {metadata_path} ({exc.msg})") from exc
    if not isinstance(metadata, dict):
        raise ProposalError(f"proposal metadata must be an object: {metadata_path}")
    return metadata


def _write_metadata(proposal_root: Path, metadata: dict[str, object]) -> None:
    (proposal_root / "metadata.yml").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _proposal_root(root: Path, proposal_id: str) -> Path:
    normalized_id = proposal_id.strip()
    if not normalized_id:
        raise ProposalError("proposal id cannot be empty")
    proposal_root = root / "proposals" / normalized_id
    if not proposal_root.is_dir():
        raise ProposalError(f"proposal id is not found: {normalized_id}")
    return proposal_root


def _ensure_open(metadata: dict[str, object], proposal_root: Path) -> None:
    status = metadata.get("status")
    if status != "open":
        raise ProposalError(f"proposal {proposal_root.name} is not open: status={status}")


def _metadata_string(metadata: dict[str, object], field: str, proposal_root: Path) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProposalError(f"proposal metadata field {field} must be a non-empty string: {proposal_root / 'metadata.yml'}")
    return value


def _metadata_string_list(metadata: dict[str, object], field: str, proposal_root: Path) -> list[str]:
    value = metadata.get(field)
    if not isinstance(value, list) or not value:
        raise ProposalError(f"proposal metadata field {field} must be a non-empty list: {proposal_root / 'metadata.yml'}")
    strings = [item for item in value if isinstance(item, str) and item.strip()]
    if len(strings) != len(value):
        raise ProposalError(f"proposal metadata field {field} must contain only strings: {proposal_root / 'metadata.yml'}")
    return strings


@dataclass(frozen=True)
class _ParsedPatch:
    target: str
    added_lines: tuple[str, ...]


def _parse_patch(path: Path) -> _ParsedPatch:
    if not path.is_file():
        raise ProposalError(f"proposal patch is missing: {path}")
    target: str | None = None
    added_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+++ b/"):
            target = line.removeprefix("+++ b/")
            continue
        if not line.startswith("+") or line.startswith("+++") or line.startswith("diff "):
            continue
        added_lines.append(line[1:])
    if target is None:
        raise ProposalError(f"proposal patch missing target header: {path}")
    if not added_lines:
        raise ProposalError(f"proposal patch has no added lines: {path}")
    return _ParsedPatch(target=target, added_lines=tuple(added_lines))


def _apply_append_patch(before: str, added_lines: tuple[str, ...]) -> str:
    addition = "\n".join(added_lines)
    if addition in before:
        return before
    separator = "" if before.endswith("\n") else "\n"
    return f"{before}{separator}{addition}\n"


def _update_approval_hashes(root: Path, repo_id: str, changed_artifacts: list[str]) -> None:
    approval_path = root / "repos" / repo_id / "approval.yml"
    if not approval_path.is_file():
        return
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProposalError(f"approval metadata is malformed: {approval_path} ({exc.msg})") from exc
    if not isinstance(approval, dict):
        raise ProposalError(f"approval metadata must be an object: {approval_path}")
    protected = approval.get("protected_artifacts")
    if not isinstance(protected, list):
        return
    changed = set(changed_artifacts)
    for item in protected:
        if not isinstance(item, dict) or item.get("path") not in changed:
            continue
        artifact_path = root / str(item["path"])
        item["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    approval_path.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _last_recorded_source_commit(root: Path, repo_id: str) -> str:
    for path in (
        root / "repos" / repo_id / "scan" / "scan-manifest.yml",
        root / "repos" / repo_id / "eval-report.yml",
    ):
        if not path.is_file():
            continue
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(artifact, dict):
            continue
        value = artifact.get("repo_source_commit")
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"


def _current_source_commit(root: Path, entry: RepoEntry) -> str:
    assert entry.local_path is not None
    repo_path = (root / entry.local_path).resolve()
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def _affected_eval_ids(root: Path, repo_id: str) -> list[str]:
    evals_path = root / "repos" / repo_id / "evals" / "onboarding.yml"
    if not evals_path.is_file():
        return []
    try:
        artifact = json.loads(evals_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    tasks = artifact.get("tasks") if isinstance(artifact, dict) else None
    if not isinstance(tasks, list):
        return []
    eval_ids = [
        task["id"]
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"].strip()
    ]
    return sorted(eval_ids)


def _first_summary_line(path: Path) -> str:
    if not path.is_file():
        return "(missing summary)"
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.removeprefix("#").strip()
    return "(empty summary)"


def _compact_diff(path: Path) -> list[str]:
    if not path.is_file():
        return ["- patch.diff is missing"]
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return ["- patch.diff is empty"]
    return lines[:40]


def _redact_jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _redact_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_jsonable(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub(_redaction_replacement, redacted)
        return redacted
    return value


def _redaction_replacement(match: re.Match[str]) -> str:
    if match.lastindex == 3:
        return f"{match.group(1)}{match.group(2)}[REDACTED]"
    if match.lastindex == 1:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"
