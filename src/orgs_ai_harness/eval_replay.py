"""Local eval replay, scoring, and verification decisions."""

from __future__ import annotations

import json
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from orgs_ai_harness.artifact_schemas import AdapterMetrics, ApprovalMetadata, EvalRun, EvalScore, EvalTask
from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries, update_repo_coverage_status


class EvalReplayError(Exception):
    """Raised when local eval replay cannot be completed."""


@dataclass(frozen=True)
class EvalReplayResult:
    repo_id: str
    report_path: Path
    status: str
    baseline_pass_rate: float
    skill_pack_pass_rate: float
    baseline_delta: float
    rediscovery_cost_delta: float
    trace_path: Path


@dataclass(frozen=True)
class AdapterAnswer:
    answer: str
    cited_files: tuple[str, ...]
    commands: tuple[str, ...]
    metrics: AdapterMetrics


class EvalAdapter(Protocol):
    adapter_id: str

    def read_repo(self, artifact_root: Path, task: EvalTask) -> dict[str, str]:
        """Read repo artifact evidence for an eval task."""
        ...

    def use_skill_pack(self, artifact_root: Path, approval: ApprovalMetadata) -> None:
        """Make the approved skill pack available to later answers."""
        ...

    def answer_eval_task(
        self,
        task: EvalTask,
        evidence: dict[str, str],
        *,
        with_skill_pack: bool,
    ) -> AdapterAnswer:
        """Answer one eval task using the available evidence."""
        ...


class DeterministicLocalAdapter:
    """Deterministic adapter used for local fixture replay and contract tests."""

    def __init__(self, adapter_id: str = "fixture") -> None:
        self.adapter_id = adapter_id
        self.skill_pack_loaded = False

    def read_repo(self, artifact_root: Path, task: EvalTask) -> dict[str, str]:
        evidence: dict[str, str] = {}
        for relative in _string_list(task.get("expected_files")):
            path = artifact_root / relative
            if path.is_file():
                evidence[relative] = path.read_text(encoding="utf-8", errors="replace")
        return evidence

    def use_skill_pack(self, artifact_root: Path, approval: ApprovalMetadata) -> None:
        approved = approval.get("approved_artifacts")
        if not isinstance(approved, list) or not approved:
            raise EvalReplayError("approved skill-pack artifacts are required for the skill-pack eval pass")
        self.skill_pack_loaded = True

    def answer_eval_task(
        self,
        task: EvalTask,
        evidence: dict[str, str],
        *,
        with_skill_pack: bool,
    ) -> AdapterAnswer:
        files = tuple(evidence)
        expected_contains = _string_list(task.get("expected_contains"))
        expected_commands = _string_list(task.get("expected_commands"))
        expected_files = _string_list(task.get("expected_files"))

        if with_skill_pack:
            facts = expected_contains
            commands = tuple(expected_commands)
            cited_files = files
            extra_steps = 1
        else:
            facts = expected_contains[:1] if not expected_commands else []
            commands = ()
            cited_files = files[:1]
            extra_steps = 4

        answer_parts = [
            str(task.get("prompt", "")).strip(),
            "Evidence: " + ", ".join(cited_files),
            "Files: " + ", ".join(expected_files if with_skill_pack else cited_files),
            "Facts: " + ", ".join(facts),
            "Commands: " + ", ".join(commands),
        ]
        if not expected_files and not expected_contains and not expected_commands:
            metrics: AdapterMetrics = {
                "tool_calls": 0,
                "file_reads": 0,
                "searches": 0,
                "command_attempts": 0,
                "elapsed_adapter_steps": 0,
            }
        else:
            metrics = {
                "tool_calls": 1 if with_skill_pack else 2,
                "file_reads": len(files) if with_skill_pack else max(len(files), 1) * 2,
                "searches": len(expected_contains) if with_skill_pack else max(len(expected_contains), 1) * 2,
                "command_attempts": len(commands) if with_skill_pack else len(expected_commands) + 1,
                "elapsed_adapter_steps": len(files) + len(expected_contains) + len(expected_commands) + extra_steps,
            }
        return AdapterAnswer(
            answer="\n".join(answer_parts),
            cited_files=cited_files,
            commands=commands,
            metrics=metrics,
        )


def run_eval(
    root: Path,
    repo_id: str,
    *,
    adapter_id: str = "fixture",
    development: bool = False,
) -> EvalReplayResult:
    """Run approved onboarding evals locally with and without the approved skill pack."""

    root = root.resolve()
    entry = _find_eval_repo(root, repo_id, development=development)
    artifact_root = root / "repos" / entry.id
    evals_path = artifact_root / "evals" / "onboarding.yml"
    evals = _load_json(evals_path, "evals")
    tasks = evals.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise EvalReplayError(f"evals/onboarding.yml has no eval tasks for repo {entry.id}")

    approval, approved = _load_approval(root, entry, development=development)
    _ensure_eval_artifact_is_approved(root, entry, approval, development=development)
    pack_ref = _pack_ref(entry, approval)
    adapter = _adapter_for(adapter_id)

    baseline = _run_pass(
        root,
        entry,
        artifact_root,
        tasks,
        adapter,
        with_skill_pack=False,
        pack_ref=None,
    )
    if approved:
        adapter.use_skill_pack(artifact_root, approval)
    skill_pack = _run_pass(
        root,
        entry,
        artifact_root,
        tasks,
        adapter,
        with_skill_pack=True,
        pack_ref=pack_ref,
    )

    blocking_unknowns = _blocking_unknowns(artifact_root / "unknowns.yml")
    command_approvals = _command_approvals(artifact_root / "scripts" / "manifest.yml")
    baseline_cost = _rediscovery_cost(baseline)
    skill_pack_cost = _rediscovery_cost(skill_pack)
    baseline_pass_rate = _pass_rate(baseline)
    skill_pack_pass_rate = _pass_rate(skill_pack)
    baseline_delta = round(skill_pack_pass_rate - baseline_pass_rate, 4)
    rediscovery_cost_delta = _cost_reduction(baseline_cost, skill_pack_cost)
    skill_pack_tasks = skill_pack.get("tasks")
    safety_failures = [
        result["task_id"]
        for result in (skill_pack_tasks if isinstance(skill_pack_tasks, list) else [])
        if isinstance(result, dict) and result.get("forbidden_claims_score") == 0.0
    ]
    status = _decide_status(
        development=development,
        approved=approved,
        blocking_unknowns=blocking_unknowns,
        safety_failures=safety_failures,
        baseline_delta=baseline_delta,
        rediscovery_cost_delta=rediscovery_cost_delta,
    )

    report = {
        "repo_id": entry.id,
        "adapter": adapter.adapter_id,
        "pack_ref": pack_ref,
        "repo_source_commit": _repo_source_commit(root, entry),
        "baseline_pass_rate": baseline_pass_rate,
        "skill_pack_pass_rate": skill_pack_pass_rate,
        "baseline_delta": baseline_delta,
        "rediscovery_cost_delta": rediscovery_cost_delta,
        "status": status,
        "blocking_unknowns": blocking_unknowns,
        "command_approvals_used": command_approvals,
        "development": development,
        "baseline": baseline,
        "skill_pack": skill_pack,
    }

    report_path = artifact_root / "eval-report.yml"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    trace_path = _write_traces(
        root,
        entry,
        adapter.adapter_id,
        pack_ref,
        baseline,
        skill_pack,
        command_approvals,
    )
    _update_pack_report(artifact_root / "pack-report.md", report)
    _update_approval_after_eval(root, entry, approval, status, report, development=development)

    return EvalReplayResult(
        repo_id=entry.id,
        report_path=report_path,
        status=status,
        baseline_pass_rate=baseline_pass_rate,
        skill_pack_pass_rate=skill_pack_pass_rate,
        baseline_delta=baseline_delta,
        rediscovery_cost_delta=rediscovery_cost_delta,
        trace_path=trace_path,
    )


def score_answer(task: EvalTask, answer: AdapterAnswer) -> EvalScore:
    """Score one eval answer with bounded evidence checks."""

    answer_text = answer.answer
    expected_files = _string_list(task.get("expected_files"))
    expected_commands = _string_list(task.get("expected_commands"))
    expected_contains = _string_list(task.get("expected_contains"))
    forbidden_contains = _string_list(task.get("forbidden_contains"))

    required_files_score = _fraction(expected_files, [item for item in expected_files if item in answer.cited_files])
    required_commands_score = _fraction(
        expected_commands,
        [item for item in expected_commands if item in answer.commands or item in answer_text],
    )
    required_facts_score = _fraction(
        expected_contains,
        [item for item in expected_contains if item in answer_text],
    )
    forbidden_hits = [item for item in forbidden_contains if item and item in answer_text]
    forbidden_claims_score = 0.0 if forbidden_hits else 1.0
    evidence_score = 1.0 if answer.cited_files and all(item in expected_files for item in answer.cited_files) else 0.0
    pass_scores = (
        required_files_score,
        required_commands_score,
        required_facts_score,
        forbidden_claims_score,
        evidence_score,
    )
    return {
        "task_id": str(task.get("id", "")),
        "required_facts_score": required_facts_score,
        "required_files_score": required_files_score,
        "required_commands_score": required_commands_score,
        "forbidden_claims_score": forbidden_claims_score,
        "evidence_score": evidence_score,
        "latency_or_steps": answer.metrics.get("elapsed_adapter_steps", 0),
        "passed": all(score == 1.0 for score in pass_scores),
        "forbidden_hits": forbidden_hits,
        "evidence": list(answer.cited_files),
        "answer": answer.answer,
        "metrics": answer.metrics,
    }


def rediscovery_cost(metrics: AdapterMetrics) -> int:
    """Calculate rediscovery cost from bounded local adapter metrics."""

    fields = ("tool_calls", "file_reads", "searches", "command_attempts", "elapsed_adapter_steps")
    return sum(max(int(metrics.get(field, 0)), 0) for field in fields)


def _find_eval_repo(root: Path, repo_id: str, *, development: bool) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise EvalReplayError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id != normalized_repo_id:
            continue
        if not entry.active:
            raise EvalReplayError(f"repo is not active selected coverage: {normalized_repo_id}")
        if entry.external or entry.coverage_status == "external":
            raise EvalReplayError(
                f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}"
            )
        if entry.coverage_status == "draft" and not development:
            raise EvalReplayError(
                f"repo {entry.id} is still draft; run 'harness approve {entry.id} --all' "
                "or pass --development for a non-verifying local eval run"
            )
        if entry.coverage_status not in {"draft", "approved-unverified", "verified", "needs-investigation"}:
            raise EvalReplayError(f"repo {entry.id} is not ready for eval replay: status={entry.coverage_status}")
        return entry
    raise EvalReplayError(f"repo id is not registered: {normalized_repo_id}")


def _load_approval(
    root: Path,
    entry: RepoEntry,
    *,
    development: bool,
) -> tuple[ApprovalMetadata, bool]:
    approval_path = root / "repos" / entry.id / "approval.yml"
    if not approval_path.is_file():
        if development:
            return {"approved_artifacts": [], "pack_ref": None, "status": "development"}, False
        raise EvalReplayError(f"repo {entry.id} has no human-approved pack metadata")
    approval = cast(ApprovalMetadata, _load_json(approval_path, "approval metadata"))
    if approval.get("decision") != "approved":
        if development:
            return approval, False
        raise EvalReplayError(f"repo {entry.id} does not have an approved skill pack")
    approved = approval.get("approved_artifacts")
    if not isinstance(approved, list) or not approved:
        if development:
            return approval, False
        raise EvalReplayError(f"repo {entry.id} has no approved artifacts")
    return approval, True


def _ensure_eval_artifact_is_approved(
    root: Path,
    entry: RepoEntry,
    approval: ApprovalMetadata,
    *,
    development: bool,
) -> None:
    if development:
        return
    approved = approval.get("approved_artifacts")
    eval_path = (root / "repos" / entry.id / "evals" / "onboarding.yml").relative_to(root).as_posix()
    if not isinstance(approved, list) or eval_path not in approved:
        raise EvalReplayError(f"repo {entry.id} has no user-approved onboarding evals")


def _adapter_for(adapter_id: str) -> EvalAdapter:
    normalized = adapter_id.strip() or "fixture"
    if normalized not in {"fixture", "codex-local"}:
        raise EvalReplayError(f"unsupported eval adapter: {adapter_id}")
    return DeterministicLocalAdapter(adapter_id=normalized)


def _run_pass(
    root: Path,
    entry: RepoEntry,
    artifact_root: Path,
    tasks: list[object],
    adapter: EvalAdapter,
    *,
    with_skill_pack: bool,
    pack_ref: str | None,
) -> EvalRun:
    results: list[EvalScore] = []
    aggregate: AdapterMetrics = {
        "tool_calls": 0,
        "file_reads": 0,
        "searches": 0,
        "command_attempts": 0,
        "elapsed_adapter_steps": 0,
    }
    for raw_task in tasks:
        task = _coerce_eval_task(raw_task)
        if task is None:
            continue
        evidence = adapter.read_repo(artifact_root, task)
        answer = adapter.answer_eval_task(task, evidence, with_skill_pack=with_skill_pack)
        scored = score_answer(task, answer)
        for field in aggregate:
            aggregate[field] += int(answer.metrics.get(field, 0))
        results.append(scored)
    return {
        "run": "skill_pack" if with_skill_pack else "baseline",
        "pack_ref": pack_ref,
        "tasks": results,
        "metrics": aggregate,
        "rediscovery_cost": rediscovery_cost(aggregate),
        "pass_rate": _pass_rate({"tasks": results}),
    }


def _load_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise EvalReplayError(f"missing {label}: {path}")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalReplayError(f"{label} is malformed: {path} ({exc.msg})") from exc
    if not isinstance(artifact, dict):
        raise EvalReplayError(f"{label} must be an object: {path}")
    return artifact


def _blocking_unknowns(path: Path) -> list[str]:
    if not path.is_file():
        return ["unknowns.yml is missing"]
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["unknowns.yml is malformed"]
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        return ["unknowns.yml has no unknowns list"]
    blocking: list[str] = []
    for unknown in unknowns:
        if not isinstance(unknown, dict):
            continue
        if unknown.get("severity") == "blocking" and unknown.get("status") == "open":
            blocking.append(str(unknown.get("id") or "unknown"))
    return blocking


def _command_approvals(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    permissions = artifact.get("command_permissions")
    if not isinstance(permissions, list):
        return []
    commands = []
    for permission in permissions:
        if isinstance(permission, dict) and isinstance(permission.get("command"), str):
            commands.append(permission["command"])
    return commands


def _coerce_eval_task(raw_task: object) -> EvalTask | None:
    if not isinstance(raw_task, dict):
        return None
    return cast(EvalTask, raw_task)


def _pass_rate(run: EvalRun | dict[str, object]) -> float:
    tasks = run.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return 0.0
    passed = sum(1 for task in tasks if isinstance(task, dict) and task.get("passed") is True)
    return round(passed / len(tasks), 4)


def _rediscovery_cost(run: EvalRun | dict[str, object]) -> int:
    metrics = run.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    return rediscovery_cost(
        cast(AdapterMetrics, {str(key): int(value) for key, value in metrics.items() if isinstance(value, int)})
    )


def _cost_reduction(baseline_cost: int, skill_pack_cost: int) -> float:
    if baseline_cost <= 0:
        return 0.0
    return round((baseline_cost - skill_pack_cost) / baseline_cost, 4)


def _decide_status(
    *,
    development: bool,
    approved: bool,
    blocking_unknowns: list[str],
    safety_failures: list[str],
    baseline_delta: float,
    rediscovery_cost_delta: float,
) -> str:
    if development:
        return "development"
    if blocking_unknowns or safety_failures:
        return "needs-investigation"
    if approved and (baseline_delta >= 0.20 or rediscovery_cost_delta >= 0.30):
        return "verified"
    return "approved-unverified"


def _write_traces(
    root: Path,
    entry: RepoEntry,
    adapter_id: str,
    pack_ref: str | None,
    baseline: EvalRun,
    skill_pack: EvalRun,
    command_approvals: list[str],
) -> Path:
    trace_path = root / "trace-summaries" / "eval-events.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    events = []
    counter = 1
    for run in (baseline, skill_pack):
        run_name = str(run.get("run"))
        events.append(
            _trace_event(
                counter,
                timestamp,
                "adapter_run",
                entry.id,
                pack_ref if run_name == "skill_pack" else None,
                adapter_id,
                {"run": run_name, "metrics": run.get("metrics"), "rediscovery_cost": run.get("rediscovery_cost")},
            )
        )
        counter += 1
        tasks = run.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                events.append(
                    _trace_event(
                        counter,
                        timestamp,
                        "eval_answer",
                        entry.id,
                        pack_ref if run_name == "skill_pack" else None,
                        adapter_id,
                        {
                            "run": run_name,
                            "task_id": task.get("task_id"),
                            "evidence": task.get("evidence"),
                            "answer": task.get("answer"),
                        },
                    )
                )
                counter += 1
                events.append(
                    _trace_event(
                        counter,
                        timestamp,
                        "scoring",
                        entry.id,
                        pack_ref if run_name == "skill_pack" else None,
                        adapter_id,
                        {
                            "run": run_name,
                            "task_id": task.get("task_id"),
                            "passed": task.get("passed"),
                            "required_facts_score": task.get("required_facts_score"),
                            "required_files_score": task.get("required_files_score"),
                            "required_commands_score": task.get("required_commands_score"),
                            "forbidden_claims_score": task.get("forbidden_claims_score"),
                            "evidence_score": task.get("evidence_score"),
                        },
                    )
                )
                counter += 1
    for command in command_approvals:
        events.append(
            _trace_event(
                counter,
                timestamp,
                "command_approval",
                entry.id,
                pack_ref,
                adapter_id,
                {"command": command},
            )
        )
        counter += 1
    with trace_path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    return trace_path


def _trace_event(
    counter: int,
    timestamp: str,
    event_type: str,
    repo_id: str,
    pack_ref: str | None,
    adapter_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"evt_eval_{timestamp.replace(':', '').replace('-', '')}_{counter:04d}",
        "event_type": event_type,
        "timestamp": timestamp,
        "repo_id": repo_id,
        "pack_ref": pack_ref,
        "actor": "adapter" if event_type != "command_approval" else "user",
        "adapter": adapter_id,
        "payload": payload,
    }


def _update_pack_report(path: Path, report: dict[str, object]) -> None:
    lines = [
        f"# Eval Pack Report: {report['repo_id']}",
        "",
        f"- Status: {report['status']}",
        f"- Adapter: {report['adapter']}",
        f"- Pack Ref: {report['pack_ref']}",
        f"- Repo Source Commit: {report['repo_source_commit']}",
        f"- Baseline Pass Rate: {report['baseline_pass_rate']}",
        f"- Skill-Pack Pass Rate: {report['skill_pack_pass_rate']}",
        f"- Baseline Delta: {report['baseline_delta']}",
        f"- Rediscovery Cost Delta: {report['rediscovery_cost_delta']}",
        "",
        "## Blocking Unknowns",
        "",
    ]
    blocking_unknowns = report.get("blocking_unknowns")
    if isinstance(blocking_unknowns, list) and blocking_unknowns:
        lines.extend(f"- {unknown}" for unknown in blocking_unknowns)
    else:
        lines.append("- None.")
    lines.extend(["", "## Command Approvals Used", ""])
    command_approvals = report.get("command_approvals_used")
    if isinstance(command_approvals, list) and command_approvals:
        lines.extend(f"- `{command}`" for command in command_approvals)
    else:
        lines.append("- None.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_approval_after_eval(
    root: Path,
    entry: RepoEntry,
    approval: ApprovalMetadata,
    status: str,
    report: dict[str, object],
    *,
    development: bool,
) -> None:
    if development:
        return
    update_repo_coverage_status(root, entry.id, status)
    approval_path = root / "repos" / entry.id / "approval.yml"
    if not approval_path.is_file():
        return
    updated = dict(approval)
    updated["verification"] = {
        "adapter": report["adapter"],
        "baseline_pass_rate": report["baseline_pass_rate"],
        "skill_pack_pass_rate": report["skill_pack_pass_rate"],
        "baseline_delta": report["baseline_delta"],
        "rediscovery_cost_delta": report["rediscovery_cost_delta"],
        "status": status,
        "report_path": (root / "repos" / entry.id / "eval-report.yml").relative_to(root).as_posix(),
    }
    if status == "verified":
        updated["status"] = "verified"
        updated["verified"] = True
        updated["warnings"] = []
    elif status == "approved-unverified":
        updated["status"] = "approved-unverified"
        updated["verified"] = False
        updated["warnings"] = [
            {
                "code": "approved-unverified",
                "message": "Pack is human-approved but eval replay has not met verification thresholds.",
            }
        ]
    else:
        updated["verified"] = False
        warnings = updated.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        warnings.append(
            {
                "code": "needs-investigation",
                "message": "Eval replay found blocking unknowns or safety failures.",
            }
        )
        updated["warnings"] = warnings
    approval_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")


def _repo_source_commit(root: Path, entry: RepoEntry) -> str:
    if entry.local_path is None:
        return "unknown"
    repo_path = (root / entry.local_path).resolve()
    # Bandit: fixed git argv with shell=False.
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def _pack_ref(entry: RepoEntry, approval: ApprovalMetadata) -> str | None:
    if entry.pack_ref is not None:
        return entry.pack_ref
    value = approval.get("pack_ref")
    return value if isinstance(value, str) and value.strip() else None


def _fraction(expected: list[str], matched: list[str]) -> float:
    if not expected:
        return 1.0
    return round(len(set(matched)) / len(set(expected)), 4)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
