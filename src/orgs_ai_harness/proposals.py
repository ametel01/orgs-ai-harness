"""Proposal generation and read-only review helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re

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
class ProposalSummary:
    proposal_id: str
    repo_id: str
    status: str
    risk: str
    summary: str
    proposal_root: Path


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
    patch = _render_patch(entry)

    (proposal_root / "summary.md").write_text(summary, encoding="utf-8")
    (proposal_root / "evidence.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in evidence),
        encoding="utf-8",
    )
    (proposal_root / "patch.diff").write_text(patch, encoding="utf-8")
    (proposal_root / "metadata.yml").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return ImproveResult(repo_id=entry.id, proposal_id=proposal_id, proposal_root=proposal_root)


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


def _render_patch(entry: RepoEntry) -> str:
    target = f"repos/{entry.id}/skills/build-test-debug/SKILL.md"
    return "\n".join(
        [
            f"diff --git a/{target} b/{target}",
            f"--- a/{target}",
            f"+++ b/{target}",
            "@@",
            "+<!-- Proposal note: review recent trace evidence before changing accepted commands. -->",
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
