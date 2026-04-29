"""Render what the harness believes about one covered repository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ExplainError(Exception):
    """Raised when harness state cannot be explained."""


def render_explain(root: Path, repo_id: str) -> str:
    """Render deterministic harness state for one repository reference."""

    root = root.resolve()
    entry = _find_repo(root, repo_id)
    if entry is None:
        return _render_uncovered_explain(root, repo_id)
    artifact_root = root / "repos" / entry.id
    cache = _cache_state(root, entry)
    skills = _approved_skills(artifact_root)
    evals = _eval_state(artifact_root)
    unknowns = _open_unknowns(artifact_root / "unknowns.yml")
    boundary_decisions = _boundary_decisions(root, entry.id)
    proposals = _recent_proposals(root, entry.id)

    lines = [
        f"Explain: {entry.id}",
        "",
        "Coverage",
        "- Covered: yes",
        f"- Why: {entry.purpose or 'selected in harness repo registry'}",
        f"- Owner: {entry.owner or 'unknown'}",
        f"- Lifecycle Status: {entry.coverage_status}",
        f"- Active: {str(entry.active).lower()}",
        f"- Pack Ref: {entry.pack_ref or 'none'}",
        "",
        "Cache",
        *[f"- {line}" for line in cache],
        "",
        "Approved Skills",
        *[f"- {line}" for line in skills],
        "",
        "Required Evals",
        *[f"- {line}" for line in evals],
        "",
        "Unresolved Unknowns",
        *[f"- {line}" for line in unknowns],
        "",
        "Boundary Decisions",
        *[f"- {line}" for line in boundary_decisions],
        "",
        "Recent Proposals",
        *[f"- {line}" for line in proposals],
    ]
    return "\n".join(lines) + "\n"


def _render_uncovered_explain(root: Path, repo_id: str) -> str:
    normalized_repo_id = _normalize_repo_id(repo_id)
    event = _record_boundary_decision(root, normalized_repo_id)
    payload = event.get("payload")
    decision = payload.get("decision") if isinstance(payload, dict) else "uncovered repo was not auto-added"
    lines = [
        f"Explain: {normalized_repo_id}",
        "",
        "Coverage",
        "- Covered: no",
        "- Why: repo is not selected in harness repo registry",
        "- Lifecycle Status: uncovered",
        "- Active: false",
        "- Pack Ref: none",
        "",
        "Boundary Decisions",
        f"- {event['event_id']}: {decision}",
        "",
        "Next Actions",
        f"- Run `harness repo add <path-or-url>` to explicitly cover {normalized_repo_id}.",
    ]
    return "\n".join(lines) + "\n"


def _find_repo(root: Path, repo_id: str) -> RepoEntry | None:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ExplainError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == normalized_repo_id:
            return entry
    return None


def _cache_state(root: Path, entry: RepoEntry) -> list[str]:
    if entry.local_path is None:
        return ["Status: unavailable; repo has no local path"]
    repo_path = (root / entry.local_path).resolve()
    cache_root = repo_path / ".agent-harness" / "cache"
    metadata_path = cache_root / "metadata.json"
    pack_ref_path = cache_root / "pack-ref"
    if not metadata_path.is_file():
        return [f"Status: missing; run 'harness cache refresh {entry.id}'"]
    metadata = _load_json(metadata_path)
    if metadata is None:
        return ["Status: malformed metadata"]
    pack_ref = pack_ref_path.read_text(encoding="utf-8").strip() if pack_ref_path.is_file() else "missing"
    exports_root = cache_root / "exports"
    exports = sorted(path.name for path in exports_root.iterdir() if path.is_dir()) if exports_root.is_dir() else []
    return [
        "Status: present",
        f"Pack Ref: {pack_ref}",
        f"Source Pack Ref: {_string(metadata.get('source_pack_ref'), 'unknown')}",
        f"Pack Status: {_string(metadata.get('status'), 'unknown')}",
        f"Exports: {', '.join(exports) if exports else 'none'}",
    ]


def _approved_skills(artifact_root: Path) -> list[str]:
    approval = _load_json(artifact_root / "approval.yml")
    if approval is None or approval.get("decision") != "approved":
        return ["None"]
    approved = approval.get("approved_artifacts")
    if not isinstance(approved, list):
        return ["None"]
    skill_roots = sorted(
        {
            Path(path).parts[3]
            for path in approved
            if isinstance(path, str)
            and len(Path(path).parts) >= 5
            and Path(path).parts[0] == "repos"
            and Path(path).parts[1] == artifact_root.name
            and Path(path).parts[2] == "skills"
        }
    )
    rendered: list[str] = []
    for skill_name in skill_roots:
        skill_path = artifact_root / "skills" / skill_name / "SKILL.md"
        triggers = _skill_triggers(skill_path)
        rendered.append(f"{skill_name}; triggers={triggers or 'unknown'}")
    return rendered or ["None"]


def _skill_triggers(path: Path) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "- The task mentions:"
        if line.startswith(marker):
            return line.removeprefix(marker).strip().rstrip(".")
    return ""


def _eval_state(artifact_root: Path) -> list[str]:
    evals = _load_json(artifact_root / "evals" / "onboarding.yml")
    report = _load_json(artifact_root / "eval-report.yml")
    # Bandit: display placeholder, not a credential.
    last_pass_rate = "unknown"  # nosec B105
    if report is not None:
        value = report.get("skill_pack_pass_rate")
        last_pass_rate = str(value) if isinstance(value, int | float) else "unknown"
    if evals is None:
        return [f"Last Pass Rate: {last_pass_rate}", "Tasks: none"]
    tasks = evals.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return [f"Last Pass Rate: {last_pass_rate}", "Tasks: none"]
    lines = [f"Last Pass Rate: {last_pass_rate}"]
    for task in tasks:
        if isinstance(task, dict):
            task_id = _string(task.get("id"), "unknown")
            category = _string(task.get("category"), "unknown")
            lines.append(f"{task_id} ({category})")
    return lines


def _open_unknowns(path: Path) -> list[str]:
    artifact = _load_json(path)
    if artifact is None:
        return ["None"]
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        return ["None"]
    rendered = []
    for unknown in unknowns:
        if not isinstance(unknown, dict) or unknown.get("status") != "open":
            continue
        rendered.append(
            f"{_string(unknown.get('id'), 'unknown')}: "
            f"{_string(unknown.get('question'), 'unresolved question')} "
            f"[{_string(unknown.get('severity'), 'unknown')}]"
        )
    return rendered or ["None"]


def _boundary_decisions(root: Path, repo_id: str) -> list[str]:
    trace_path = root / "trace-summaries" / "boundary-decisions.jsonl"
    if not trace_path.is_file():
        return ["None"]
    rendered = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if event.get("repo_id") != repo_id and payload.get("referenced_repo_id") != repo_id:
            continue
        rendered.append(
            f"{_string(event.get('event_id'), 'unknown')}: {_string(payload.get('decision'), 'boundary decision')}"
        )
    return rendered[-5:] or ["None"]


def _recent_proposals(root: Path, repo_id: str) -> list[str]:
    proposals_root = root / "proposals"
    if not proposals_root.is_dir():
        return ["None"]
    matches = sorted(path for path in proposals_root.rglob("*") if path.is_file() and repo_id in path.as_posix())
    return [path.relative_to(root).as_posix() for path in matches[-5:]] or ["None"]


def _load_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return artifact if isinstance(artifact, dict) else None


def _string(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return default


def _record_boundary_decision(root: Path, referenced_repo_id: str) -> dict[str, object]:
    trace_root = root / "trace-summaries"
    trace_root.mkdir(parents=True, exist_ok=True)
    trace_path = trace_root / "boundary-decisions.jsonl"
    timestamp = _timestamp()
    existing_count = len(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.is_file() else 0
    event = {
        "schema_version": 1,
        "event_id": f"evt_boundary_{timestamp.replace(':', '').replace('-', '')}_{existing_count + 1:04d}",
        "event_type": "boundary_decision",
        "timestamp": timestamp,
        "repo_id": None,
        "pack_ref": None,
        "actor": "harness",
        "adapter": None,
        "payload": {
            "referenced_repo_id": referenced_repo_id,
            "decision": f"uncovered repo {referenced_repo_id} was not auto-added",
            "reason": "Harness coverage requires explicit repo registration.",
            "registry_mutation": "none",
        },
    }
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def _normalize_repo_id(repo_id: str) -> str:
    normalized = repo_id.strip()
    if not normalized:
        raise ExplainError("repo id cannot be empty")
    return normalized


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
