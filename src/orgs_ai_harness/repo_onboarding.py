"""Repository onboarding scans and draft pack generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries, update_repo_coverage_status


class RepoOnboardingError(Exception):
    """Raised when repo onboarding cannot be completed."""


@dataclass(frozen=True)
class OnboardingResult:
    repo_id: str
    artifact_root: Path
    summary_path: Path
    unknowns_path: Path
    scan_manifest_path: Path
    hypothesis_map_path: Path
    resolvers_path: Path | None = None
    evals_path: Path | None = None
    pack_report_path: Path | None = None


SAFE_EVIDENCE_FILES = {
    "README.md": "readme",
    "README": "readme",
    "package.json": "package_manifest",
    "pyproject.toml": "package_manifest",
    "requirements.txt": "dependency_manifest",
    "package-lock.json": "dependency_manifest",
    "pnpm-lock.yaml": "dependency_manifest",
    "yarn.lock": "dependency_manifest",
    "pytest.ini": "test_config",
    "tox.ini": "test_config",
    "tsconfig.json": "important_config",
    "Dockerfile": "important_config",
    "AGENTS.md": "agent_docs",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SENSITIVE_NAME_PARTS = ("credential", "credentials", "secret", "secrets", "token", "tokens")


def scan_repo_only(root: Path, repo_id: str) -> OnboardingResult:
    """Run a read-only scan for one selected local repository."""

    root = root.resolve()
    entry = _find_repo(root, repo_id)
    update_repo_coverage_status(root, entry.id, "onboarding")
    repo_path = _resolve_repo_path(root, entry)

    scanned, skipped = _scan_repo(repo_path)
    unknowns = _default_unknowns(scanned)
    final_status = _status_for_unknowns(unknowns)

    artifact_root = root / "repos" / entry.id
    scan_root = artifact_root / "scan"
    scan_root.mkdir(parents=True, exist_ok=True)

    summary_path = artifact_root / "onboarding-summary.md"
    unknowns_path = artifact_root / "unknowns.yml"
    scan_manifest_path = scan_root / "scan-manifest.yml"
    hypothesis_map_path = scan_root / "hypothesis-map.yml"
    hypothesis_map = _build_hypothesis_map(entry, scanned, unknowns)

    summary_path.write_text(_render_summary(entry, scanned, unknowns, hypothesis_map_path), encoding="utf-8")
    unknowns_path.write_text(json.dumps({"unknowns": unknowns}, indent=2) + "\n", encoding="utf-8")
    hypothesis_map_path.write_text(json.dumps(hypothesis_map, indent=2) + "\n", encoding="utf-8")
    scan_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": entry.id,
                "repo_path": entry.local_path,
                "scanned_paths": scanned,
                "skipped_paths": skipped,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    update_repo_coverage_status(root, entry.id, final_status)

    return OnboardingResult(
        repo_id=entry.id,
        artifact_root=artifact_root,
        summary_path=summary_path,
        unknowns_path=unknowns_path,
        scan_manifest_path=scan_manifest_path,
        hypothesis_map_path=hypothesis_map_path,
    )


def onboard_repo(root: Path, repo_id: str) -> OnboardingResult:
    """Generate a draft skill pack for one selected local repository."""

    scan_result = scan_repo_only(root, repo_id)
    root = root.resolve()
    entry = _find_repo(root, repo_id)
    artifact_root = scan_result.artifact_root
    hypothesis_map = json.loads(scan_result.hypothesis_map_path.read_text(encoding="utf-8"))
    unknowns_artifact = json.loads(scan_result.unknowns_path.read_text(encoding="utf-8"))
    scanned = list(hypothesis_map.get("evidence_categories", {}).items())

    skill_specs = _skill_specs_for(entry, hypothesis_map)
    skills_root = artifact_root / "skills"
    for spec in skill_specs:
        skill_root = skills_root / spec["name"]
        references_root = skill_root / "references"
        references_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(_render_skill(spec), encoding="utf-8")
        (references_root / "repo-evidence.md").write_text(
            _render_skill_reference(entry, spec, hypothesis_map),
            encoding="utf-8",
        )

    resolvers_path = artifact_root / "resolvers.yml"
    evals_root = artifact_root / "evals"
    evals_root.mkdir(parents=True, exist_ok=True)
    evals_path = evals_root / "onboarding.yml"
    scripts_root = artifact_root / "scripts"
    scripts_root.mkdir(parents=True, exist_ok=True)
    script_path = scripts_root / "check-pack-shape.py"
    script_manifest_path = scripts_root / "manifest.yml"
    pack_report_path = artifact_root / "pack-report.md"

    resolvers_path.write_text(
        json.dumps(_build_resolvers(skill_specs), indent=2) + "\n",
        encoding="utf-8",
    )
    evals_path.write_text(
        json.dumps(_build_evals(entry, skill_specs), indent=2) + "\n",
        encoding="utf-8",
    )
    script_path.write_text(_render_check_script(), encoding="utf-8")
    script_path.chmod(0o755)
    script_manifest_path.write_text(
        json.dumps(
            {
                "scripts": [
                    {
                        "path": "scripts/check-pack-shape.py",
                        "review_required": True,
                        "deterministic": True,
                        "local_only": True,
                    }
                ],
                "command_permissions": [
                    {
                        "command": "python scripts/check-pack-shape.py",
                        "reason": "Run the deterministic local draft pack shape check.",
                        "review_required": True,
                        "local_only": True,
                    },
                    {
                        "command": f"harness validate {entry.id}",
                        "reason": "Validate generated repo onboarding and approval metadata locally.",
                        "review_required": True,
                        "local_only": True,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    unknowns = unknowns_artifact.get("unknowns", [])
    unknowns_artifact["candidate_org_skills"] = [
        {
            "name": spec["name"],
            "status": "candidate",
            "reason": f"Inferred only from {entry.id}; requires cross-repo review before org-wide acceptance.",
        }
        for spec in skill_specs
    ]
    scan_result.unknowns_path.write_text(json.dumps(unknowns_artifact, indent=2) + "\n", encoding="utf-8")
    pack_report_path.write_text(
        _render_pack_report(entry, skill_specs, unknowns, scanned),
        encoding="utf-8",
    )
    update_repo_coverage_status(root, entry.id, "draft")

    return OnboardingResult(
        repo_id=entry.id,
        artifact_root=artifact_root,
        summary_path=scan_result.summary_path,
        unknowns_path=scan_result.unknowns_path,
        scan_manifest_path=scan_result.scan_manifest_path,
        hypothesis_map_path=scan_result.hypothesis_map_path,
        resolvers_path=resolvers_path,
        evals_path=evals_path,
        pack_report_path=pack_report_path,
    )


def _find_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise RepoOnboardingError("repo id cannot be empty")

    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == normalized_repo_id:
            if entry.coverage_status == "external" or entry.external:
                raise RepoOnboardingError(f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}")
            if entry.coverage_status == "approved-unverified":
                _raise_if_generation_would_overwrite_protected(root, entry)
            if entry.coverage_status not in {"selected", "onboarding", "needs-investigation", "draft"} or not entry.active:
                raise RepoOnboardingError(f"repo is not active selected coverage: {normalized_repo_id}")
            if entry.local_path is None:
                raise RepoOnboardingError(
                    f"repo {normalized_repo_id} has no local path; run 'harness repo discover --clone' "
                    "or 'harness repo set-path'"
                )
            return entry

    raise RepoOnboardingError(f"repo id is not registered: {normalized_repo_id}")


def _raise_if_generation_would_overwrite_protected(root: Path, entry: RepoEntry) -> None:
    approval_path = root / "repos" / entry.id / "approval.yml"
    if not approval_path.is_file():
        raise RepoOnboardingError(
            f"repo {entry.id} is approved-unverified but missing approval metadata; "
            "refusing generation until approval metadata is repaired"
        )
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepoOnboardingError(
            f"repo {entry.id} approval metadata is malformed; refusing generation until it is repaired"
        ) from exc
    protected = approval.get("protected_artifacts")
    if not isinstance(protected, list) or not protected:
        raise RepoOnboardingError(
            f"repo {entry.id} is approved-unverified but has no protected artifact metadata; "
            "refusing generation until approval metadata is repaired"
        )
    protected_paths = [
        item["path"]
        for item in protected
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip()
    ]
    if protected_paths:
        sample = ", ".join(protected_paths[:3])
        suffix = "" if len(protected_paths) <= 3 else f", and {len(protected_paths) - 3} more"
        raise RepoOnboardingError(
            "generation would overwrite protected artifact(s): "
            f"{sample}{suffix}. Use the Sprint 09 proposal flow for changes to approved artifacts."
        )


def _resolve_repo_path(root: Path, entry: RepoEntry) -> Path:
    assert entry.local_path is not None
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.exists():
        raise RepoOnboardingError(f"repo path does not exist: {repo_path}; repair it with 'harness repo set-path'")
    if not repo_path.is_dir():
        raise RepoOnboardingError(f"repo path is not a directory: {repo_path}; repair it with 'harness repo set-path'")
    return repo_path


def is_sensitive_path(relative_path: str) -> bool:
    """Return whether a repository path must be skipped as sensitive."""

    path = Path(relative_path)
    name = path.name.lower()
    stem = path.stem.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(SENSITIVE_SUFFIXES):
        return True
    if name.endswith(".local") or ".local." in name:
        return True
    if any(part in name for part in SENSITIVE_NAME_PARTS):
        return True
    if stem in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return True
    return False


def _scan_repo(repo_path: Path) -> tuple[list[dict[str, str | int]], list[dict[str, str]]]:
    scanned: list[dict[str, str | int]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_path).as_posix()
        if is_sensitive_path(relative):
            skipped.append({"path": relative, "reason": "sensitive filename policy"})
            continue
        category = _evidence_category(relative)
        if category is None:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        scanned.append(
            {
                "path": relative,
                "category": category,
                "bytes": len(content.encode("utf-8")),
            }
        )
    return scanned, skipped


def _evidence_category(relative_path: str) -> str | None:
    if relative_path.startswith(".github/workflows/") and (
        relative_path.endswith(".yml") or relative_path.endswith(".yaml")
    ):
        return "ci_config"
    if relative_path.startswith("scripts/"):
        return "script"
    return SAFE_EVIDENCE_FILES.get(relative_path)


def _default_unknowns(scanned: list[dict[str, str | int]]) -> list[dict[str, object]]:
    evidence = []
    if any(item["path"] == "package.json" for item in scanned):
        evidence.append({"path": "package.json", "note": "Package manifest found; test script needs confirmation."})
    elif scanned:
        first_path = str(scanned[0]["path"])
        evidence.append({"path": first_path, "note": "Repository evidence found, but test command is unknown."})

    return [
        {
            "id": "unk_001",
            "question": "Which command is the narrowest reliable unit test command?",
            "why_it_matters": "Eval and skill generation need reproducible validation commands.",
            "severity": "blocking",
            "status": "open",
            "evidence": evidence,
            "recommended_investigation": "Inspect package scripts and CI job command usage.",
        }
    ]


def _status_for_unknowns(unknowns: list[dict[str, object]]) -> str:
    for unknown in unknowns:
        if unknown.get("severity") == "blocking" and unknown.get("status") == "open":
            return "needs-investigation"
    return "onboarding"


def _skill_specs_for(entry: RepoEntry, hypothesis_map: dict[str, object]) -> list[dict[str, object]]:
    evidence_categories = hypothesis_map.get("evidence_categories")
    categories = evidence_categories if isinstance(evidence_categories, dict) else {}
    specs: list[dict[str, object]] = [
        {
            "name": "build-test-debug",
            "description": f"Select safe build, test, and debug commands for {entry.id}.",
            "summary": "Use scan evidence to choose the narrowest local validation command before broader checks.",
            "triggers": ["test command", "build failure", "debug repo setup"],
        },
        {
            "name": "repo-architecture",
            "description": f"Navigate the repository structure and architectural evidence for {entry.id}.",
            "summary": "Route agents through README, manifests, CI, scripts, and agent notes before editing.",
            "triggers": ["repo layout", "architecture question", "where is functionality"],
        },
    ]
    if "agent_docs" in categories:
        specs.append(
            {
                "name": "agent-procedure",
                "description": f"Follow repo-local agent procedures for {entry.id}.",
                "summary": "Prefer repo-local AGENTS.md instructions and safe procedures before making changes.",
                "triggers": ["agent instructions", "safe procedure", "repo rules"],
            }
        )
    return specs


def _render_skill(spec: dict[str, object]) -> str:
    name = str(spec["name"])
    triggers = ", ".join(str(trigger) for trigger in spec["triggers"])
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {spec['description']}\n"
        "---\n"
        f"# {name}\n\n"
        f"{spec['summary']}\n\n"
        "## Use When\n\n"
        f"- The task mentions: {triggers}.\n"
        "- You need repo-specific evidence before choosing commands or files.\n\n"
        "## Procedure\n\n"
        "1. Read `references/repo-evidence.md` for generated evidence and open unknowns.\n"
        "2. Prefer the narrowest command or file path supported by scan evidence.\n"
        "3. Treat one-repo conventions as candidates until a human accepts them org-wide.\n"
    )


def _render_skill_reference(entry: RepoEntry, spec: dict[str, object], hypothesis_map: dict[str, object]) -> str:
    lines = [
        f"# Repo Evidence for {spec['name']}",
        "",
        f"- Repo: {entry.id}",
        f"- Source: generated draft from read-only scan evidence",
        f"- Status: candidate, not accepted org-wide",
        "",
        "## Evidence Categories",
        "",
    ]
    evidence_categories = hypothesis_map.get("evidence_categories")
    if isinstance(evidence_categories, dict) and evidence_categories:
        for category, paths in sorted(evidence_categories.items()):
            rendered_paths = ", ".join(f"`{path}`" for path in paths) if isinstance(paths, list) else "`unknown`"
            lines.append(f"- {category}: {rendered_paths}")
    else:
        lines.append("- No safe evidence files were found.")
    return "\n".join(lines) + "\n"


def _build_resolvers(skill_specs: list[dict[str, object]]) -> dict[str, object]:
    skill_names = [str(spec["name"]) for spec in skill_specs]
    resolvers = [
        {
            "intent": "select validation command",
            "skill": "build-test-debug",
            "when": ["tests", "build", "debug"],
        },
        {
            "intent": "answer repository structure question",
            "skill": "repo-architecture",
            "when": ["architecture", "layout", "ownership"],
        },
    ]
    if "agent-procedure" in skill_names:
        resolvers.append(
            {
                "intent": "follow repo-local agent procedure",
                "skill": "agent-procedure",
                "when": ["AGENTS.md", "safe procedure", "instructions"],
            }
        )
    return {"resolvers": resolvers}


def _build_evals(entry: RepoEntry, skill_specs: list[dict[str, object]]) -> dict[str, object]:
    skill_names = [str(spec["name"]) for spec in skill_specs]
    tasks = [
        _eval_task(
            "repo-knowledge-readme",
            "repo knowledge",
            "Summarize the repo purpose from scan evidence.",
            ["onboarding-summary.md"],
            [],
            ["Onboarding Summary"],
            ["secret"],
        ),
        _eval_task(
            "repo-knowledge-manifest",
            "repo knowledge",
            "Find the package manifest evidence.",
            ["scan/scan-manifest.yml"],
            [],
            ["package.json"],
            [],
        ),
        _eval_task(
            "command-selection-tests",
            "command selection",
            "Choose the narrowest likely test command.",
            ["scan/hypothesis-map.yml"],
            ["npm test"],
            ["test_command_candidates"],
            [],
        ),
        _eval_task(
            "safe-procedure-sensitive-files",
            "safe procedure",
            "Confirm sensitive files are not read into generated skills.",
            ["scan/scan-manifest.yml"],
            [],
            ["sensitive filename policy"],
            ["do-not-leak"],
        ),
        _eval_task(
            "resolver-build-test-debug",
            "resolver behavior",
            "Route test-command questions to the build/test skill.",
            ["resolvers.yml"],
            [],
            ["build-test-debug"],
            [],
        ),
        _eval_task(
            "resolver-repo-architecture",
            "resolver behavior",
            "Route repo-layout questions to the architecture skill.",
            ["resolvers.yml"],
            [],
            ["repo-architecture"],
            [],
        ),
        _eval_task(
            "skill-reference-routing",
            "repo knowledge",
            "Use compact skills that route details to references.",
            ["skills/build-test-debug/SKILL.md"],
            [],
            ["references/repo-evidence.md"],
            [],
        ),
        _eval_task(
            "unknowns-preserved",
            "safe procedure",
            "Keep unresolved findings visible for human review.",
            ["unknowns.yml"],
            [],
            ["Which command is the narrowest reliable unit test command?"],
            [],
        ),
    ]
    if "agent-procedure" in skill_names:
        tasks.append(
            _eval_task(
                "resolver-agent-procedure",
                "resolver behavior",
                "Route local agent instruction questions to the procedure skill.",
                ["resolvers.yml"],
                [],
                ["agent-procedure"],
                [],
            )
        )
    tasks.append(
        _eval_task(
            "draft-status-report",
            "safe procedure",
            "Verify generated artifacts remain draft and reviewable.",
            ["pack-report.md"],
            [],
            ["Status: draft", entry.id],
            ["Status: verified", "Status: approved"],
        )
    )
    return {"repo_id": entry.id, "tasks": tasks[:10]}


def _eval_task(
    task_id: str,
    category: str,
    prompt: str,
    expected_files: list[str],
    expected_commands: list[str],
    expected_contains: list[str],
    forbidden_contains: list[str],
) -> dict[str, object]:
    return {
        "id": task_id,
        "category": category,
        "prompt": prompt,
        "expected_files": expected_files,
        "expected_commands": expected_commands,
        "expected_contains": expected_contains,
        "forbidden_contains": forbidden_contains,
    }


def _render_check_script() -> str:
    return (
        "#!/usr/bin/env python3\n"
        "\"\"\"Deterministic local check for generated draft pack shape.\"\"\"\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()\n"
        "required = [\n"
        "    'onboarding-summary.md',\n"
        "    'unknowns.yml',\n"
        "    'resolvers.yml',\n"
        "    'evals/onboarding.yml',\n"
        "    'pack-report.md',\n"
        "]\n"
        "missing = [relative for relative in required if not (root / relative).is_file()]\n"
        "if missing:\n"
        "    print('missing generated artifact(s): ' + ', '.join(missing), file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "print('draft pack shape ok')\n"
    )


def _render_pack_report(
    entry: RepoEntry,
    skill_specs: list[dict[str, object]],
    unknowns: object,
    scanned: list[tuple[object, object]],
) -> str:
    lines = [
        f"# Draft Pack Report: {entry.id}",
        "",
        "- Status: draft",
        "- Approval: not approved",
        "- Verification: not verified",
        "",
        "## Generated Artifacts",
        "",
        "- `onboarding-summary.md`",
        "- `unknowns.yml`",
        "- `resolvers.yml`",
        "- `evals/onboarding.yml`",
        "- `scripts/check-pack-shape.py`",
        "- `scripts/manifest.yml`",
    ]
    for spec in skill_specs:
        lines.append(f"- `skills/{spec['name']}/SKILL.md`")
    lines.extend(["", "## Skill Candidates", ""])
    for spec in skill_specs:
        lines.append(f"- `{spec['name']}`: candidate from one repo only")
    lines.extend(["", "## Scan Evidence", ""])
    if scanned:
        for category, paths in scanned:
            count = len(paths) if isinstance(paths, list) else 0
            lines.append(f"- {category}: {count} path(s)")
    else:
        lines.append("- No scan evidence categories were recorded.")
    lines.extend(["", "## Open Unknowns", ""])
    if isinstance(unknowns, list) and unknowns:
        for unknown in unknowns:
            if isinstance(unknown, dict):
                lines.append(f"- {unknown.get('id')}: {unknown.get('question')}")
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _render_summary(
    entry: RepoEntry,
    scanned: list[dict[str, str | int]],
    unknowns: list[dict[str, object]],
    hypothesis_map_path: Path,
) -> str:
    lines = [
        f"# Onboarding Summary: {entry.id}",
        "",
        f"- Name: {entry.name}",
        f"- Owner: {entry.owner or 'unknown'}",
        f"- Purpose: {entry.purpose or 'not provided'}",
        f"- Local path: {entry.local_path or 'unknown'}",
        "",
        "## Scanned Evidence",
        "",
    ]
    if scanned:
        for item in scanned:
            lines.append(f"- `{item['path']}` ({item['category']}, {item['bytes']} bytes)")
    else:
        lines.append("- No safe evidence files found in the initial scan set.")
    lines.extend(
        [
            "",
            "## Skipped Paths",
            "",
            "- Sensitive paths are recorded in the scan manifest and their contents were not read.",
            "",
            "## Hypothesis Map",
            "",
            f"- See `{hypothesis_map_path.name}` for traceable scan hypotheses.",
        ]
    )
    lines.extend(
        [
            "",
            "## Open Unknowns",
            "",
        ]
    )
    for unknown in unknowns:
        lines.append(f"- {unknown['id']}: {unknown['question']} [{unknown['severity']}]")
    return "\n".join(lines) + "\n"


def _build_hypothesis_map(
    entry: RepoEntry,
    scanned: list[dict[str, str | int]],
    unknowns: list[dict[str, object]],
) -> dict[str, object]:
    by_category: dict[str, list[str]] = {}
    for item in scanned:
        category = str(item["category"])
        by_category.setdefault(category, []).append(str(item["path"]))

    hypotheses = [
        _hypothesis(
            "project_type",
            _project_type(by_category),
            by_category.get("package_manifest") or by_category.get("readme", []),
        ),
        _hypothesis("package_manager", _package_manager(by_category), by_category.get("dependency_manifest", [])),
        _hypothesis("test_command_candidates", _test_commands(by_category), by_category.get("package_manifest", [])),
        _hypothesis("ci_validation", _ci_validation(by_category), by_category.get("ci_config", [])),
        _hypothesis("agent_documentation", _agent_docs(by_category), by_category.get("agent_docs", [])),
        _hypothesis("scripts", _scripts(by_category), by_category.get("script", [])),
    ]

    return {
        "repo_id": entry.id,
        "seed_context": {
            "purpose": {"value": entry.purpose, "source": "manual repo registration"},
            "owner": {"value": entry.owner, "source": "manual repo registration"},
        },
        "evidence_categories": by_category,
        "hypotheses": hypotheses,
        "unknown_refs": [unknown["id"] for unknown in unknowns],
    }


def _hypothesis(name: str, value: object, evidence_paths: list[str]) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "evidence_paths": evidence_paths,
        "unknown": not evidence_paths,
    }


def _project_type(by_category: dict[str, list[str]]) -> str:
    if "package_manifest" in by_category:
        return "application_or_package"
    if "readme" in by_category:
        return "documented_repository"
    return "unknown"


def _package_manager(by_category: dict[str, list[str]]) -> str:
    paths = set(by_category.get("dependency_manifest", [])) | set(by_category.get("package_manifest", []))
    if "package-lock.json" in paths:
        return "npm"
    if "pnpm-lock.yaml" in paths:
        return "pnpm"
    if "yarn.lock" in paths:
        return "yarn"
    if "package.json" in paths:
        return "node"
    if "pyproject.toml" in paths or "requirements.txt" in paths:
        return "python"
    return "unknown"


def _test_commands(by_category: dict[str, list[str]]) -> list[str]:
    paths = set(by_category.get("package_manifest", [])) | set(by_category.get("test_config", []))
    commands: list[str] = []
    if "package.json" in paths:
        commands.append("npm test")
    if "pyproject.toml" in paths or "pytest.ini" in paths:
        commands.append("pytest")
    return commands


def _ci_validation(by_category: dict[str, list[str]]) -> str:
    if "ci_config" in by_category:
        return "ci configuration present"
    return "unknown"


def _agent_docs(by_category: dict[str, list[str]]) -> str:
    if "agent_docs" in by_category:
        return "agent documentation present"
    return "unknown"


def _scripts(by_category: dict[str, list[str]]) -> list[str]:
    return by_category.get("script", [])
