"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from orgs_ai_harness.config import block_has_field, read_block_scalar, split_top_level_blocks
from orgs_ai_harness.repo_registry import RepoRegistryError, parse_repo_block


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_org_pack(root: Path) -> ValidationResult:
    """Validate the minimum Sprint 01 org pack contract."""

    root = root.resolve()
    errors: list[str] = []

    if not root.exists():
        return ValidationResult((f"org pack root does not exist: {root}",))
    if not root.is_dir():
        return ValidationResult((f"org pack root is not a directory: {root}",))

    required_dirs = [
        "org",
        "org/skills",
        "repos",
        "proposals",
        "trace-summaries",
    ]
    required_files = [
        "harness.yml",
        "org/resolvers.yml",
    ]

    for relative in required_dirs:
        path = root / relative
        if not path.is_dir():
            errors.append(
                f"missing required directory: {relative} "
                f"(create {relative}/ or rerun 'harness org init --name <name>' in a clean directory)"
            )

    for relative in required_files:
        path = root / relative
        if not path.is_file():
            errors.append(
                f"missing required file: {relative} "
                f"(restore {relative} or rerun 'harness org init --name <name>' in a clean directory)"
            )

    config_path = root / "harness.yml"
    if config_path.is_file():
        errors.extend(_validate_minimum_config(config_path.read_text(encoding="utf-8")))

    return ValidationResult(tuple(errors))


def validate_repo_onboarding(root: Path, repo_id: str) -> ValidationResult:
    """Validate scan and generated draft pack artifacts for one repo."""

    root = root.resolve()
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        return ValidationResult(("repo id cannot be empty",))

    errors: list[str] = list(validate_org_pack(root).errors)
    artifact_root = root / "repos" / normalized_repo_id
    summary_path = artifact_root / "onboarding-summary.md"
    unknowns_path = artifact_root / "unknowns.yml"
    manifest_path = artifact_root / "scan" / "scan-manifest.yml"
    hypothesis_map_path = artifact_root / "scan" / "hypothesis-map.yml"
    skills_root = artifact_root / "skills"
    resolvers_path = artifact_root / "resolvers.yml"
    evals_path = artifact_root / "evals" / "onboarding.yml"
    pack_report_path = artifact_root / "pack-report.md"
    script_manifest_path = artifact_root / "scripts" / "manifest.yml"
    approval_path = artifact_root / "approval.yml"

    if not summary_path.is_file():
        errors.append(f"missing onboarding summary: {summary_path.relative_to(root)}")
    elif not summary_path.read_text(encoding="utf-8").strip():
        errors.append(f"onboarding summary is empty: {summary_path.relative_to(root)}")

    unknowns = _load_json_artifact(unknowns_path, "unknowns", errors, root)
    if isinstance(unknowns, dict):
        _validate_unknowns_artifact(unknowns, unknowns_path, root, errors)

    manifest = _load_json_artifact(manifest_path, "scan manifest", errors, root)
    if isinstance(manifest, dict):
        _validate_scan_manifest_artifact(manifest, manifest_path, root, errors)

    hypothesis_map = _load_json_artifact(hypothesis_map_path, "hypothesis map", errors, root)
    if isinstance(hypothesis_map, dict):
        _validate_hypothesis_map_artifact(hypothesis_map, hypothesis_map_path, root, errors)

    generated_markers = (skills_root, resolvers_path, evals_path, pack_report_path, script_manifest_path)
    if any(path.exists() for path in generated_markers):
        generated_skills = _validate_generated_skills(skills_root, root, errors)
        org_skills = _existing_org_skill_names(root)
        _validate_resolvers_artifact(resolvers_path, root, generated_skills | org_skills, errors)
        _validate_evals_artifact(evals_path, root, errors)
        _validate_script_manifest(script_manifest_path, artifact_root, root, errors)
        _validate_pack_report(pack_report_path, root, errors)

    _validate_approval_metadata(root, normalized_repo_id, approval_path, errors)

    return ValidationResult(tuple(errors))


def _validate_minimum_config(config_text: str) -> list[str]:
    errors: list[str] = []
    blocks = {block.key: block for block in split_top_level_blocks(config_text)}
    org_block = blocks.get("org")

    if org_block is None:
        errors.append("harness.yml missing required field: org (add top-level 'org:' mapping)")

    name = read_block_scalar(org_block, "name") if org_block is not None else None
    if name is None or not name.strip():
        errors.append("harness.yml missing required field: org.name (set org.name to a non-empty name)")
    elif not _is_valid_org_name(name):
        errors.append(
            "harness.yml field org.name is invalid "
            "(use letters, numbers, dots, underscores, or hyphens; do not use slashes)"
        )

    skills_version = read_block_scalar(org_block, "skills_version") if org_block is not None else None
    if skills_version != "1":
        errors.append("harness.yml field org.skills_version must be 1 (set 'skills_version: 1')")

    for field_name in ("providers", "repos", "command_permissions"):
        if field_name not in blocks:
            errors.append(f"harness.yml missing required field: {field_name} (add '{field_name}: []')")

    repos_block = blocks.get("repos")
    if repos_block is not None:
        try:
            entries = parse_repo_block(repos_block)
        except RepoRegistryError as exc:
            errors.append(str(exc))
        else:
            seen_repo_ids: set[str] = set()
            for entry in entries:
                if entry.id in seen_repo_ids:
                    errors.append(f"harness.yml contains duplicate repo id: {entry.id}")
                seen_repo_ids.add(entry.id)
                errors.extend(
                    _validate_repo_entry(
                        entry.id,
                        entry.coverage_status,
                        entry.active,
                        entry.local_path,
                        entry.deactivation_reason,
                        entry.external,
                    )
                )

    redaction_block = blocks.get("redaction")
    if redaction_block is None:
        errors.append("harness.yml missing required field: redaction (add top-level 'redaction:' mapping)")
    if redaction_block is None or not block_has_field(redaction_block, "globs"):
        errors.append("harness.yml missing required field: redaction.globs (add '  globs: []')")
    if redaction_block is None or not block_has_field(redaction_block, "regexes"):
        errors.append("harness.yml missing required field: redaction.regexes (add '  regexes: []')")

    return errors


def _load_json_artifact(path: Path, label: str, errors: list[str], root: Path) -> object | None:
    if not path.is_file():
        errors.append(f"missing {label}: {path.relative_to(root)}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{label} is malformed: {path.relative_to(root)} ({exc.msg})")
        return None


def _validate_unknowns_artifact(artifact: dict[str, object], path: Path, root: Path, errors: list[str]) -> None:
    unknowns = artifact.get("unknowns")
    if not isinstance(unknowns, list):
        errors.append(f"unknowns.yml field unknowns must be a list: {path.relative_to(root)}")
        return
    for index, unknown in enumerate(unknowns, start=1):
        if not isinstance(unknown, dict):
            errors.append(f"unknowns.yml item {index} must be an object: {path.relative_to(root)}")
            continue
        for field in ("id", "question", "why_it_matters", "severity", "status", "recommended_investigation"):
            if not isinstance(unknown.get(field), str) or not str(unknown.get(field)).strip():
                errors.append(f"unknowns.yml item {index} field {field} must be a non-empty string")
        if unknown.get("severity") not in {"blocking", "important", "minor"}:
            errors.append(f"unknowns.yml item {index} has invalid severity: {unknown.get('severity')}")
        if unknown.get("status") not in {"open", "closed"}:
            errors.append(f"unknowns.yml item {index} has invalid status: {unknown.get('status')}")
        evidence = unknown.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"unknowns.yml item {index} field evidence must be a list")
    candidates = artifact.get("candidate_org_skills", [])
    if candidates is None:
        return
    if not isinstance(candidates, list):
        errors.append(f"unknowns.yml field candidate_org_skills must be a list: {path.relative_to(root)}")
        return
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            errors.append(f"unknowns.yml candidate_org_skills item {index} must be an object")
            continue
        if not _is_valid_skill_name(str(candidate.get("name", ""))):
            errors.append(f"unknowns.yml candidate_org_skills item {index} has invalid skill name")
        if candidate.get("status") != "candidate":
            errors.append(f"unknowns.yml candidate_org_skills item {index} must have status candidate")


def _validate_scan_manifest_artifact(artifact: dict[str, object], path: Path, root: Path, errors: list[str]) -> None:
    if not isinstance(artifact.get("repo_id"), str) or not str(artifact.get("repo_id")).strip():
        errors.append(f"scan manifest field repo_id must be a non-empty string: {path.relative_to(root)}")
    scanned_paths = artifact.get("scanned_paths")
    skipped_paths = artifact.get("skipped_paths")
    if not isinstance(scanned_paths, list):
        errors.append(f"scan manifest field scanned_paths must be a list: {path.relative_to(root)}")
    else:
        for index, record in enumerate(scanned_paths, start=1):
            _validate_path_record(record, f"scan manifest scanned_paths item {index}", require_reason=False, errors=errors)
    if not isinstance(skipped_paths, list):
        errors.append(f"scan manifest field skipped_paths must be a list: {path.relative_to(root)}")
    else:
        for index, record in enumerate(skipped_paths, start=1):
            _validate_path_record(record, f"scan manifest skipped_paths item {index}", require_reason=True, errors=errors)


def _validate_path_record(record: object, label: str, *, require_reason: bool, errors: list[str]) -> None:
    if not isinstance(record, dict):
        errors.append(f"{label} must be an object")
        return
    if not isinstance(record.get("path"), str) or not str(record.get("path")).strip():
        errors.append(f"{label} field path must be a non-empty string")
    if require_reason and (not isinstance(record.get("reason"), str) or not str(record.get("reason")).strip()):
        errors.append(f"{label} field reason must be a non-empty string")


def _validate_hypothesis_map_artifact(
    artifact: dict[str, object],
    path: Path,
    root: Path,
    errors: list[str],
) -> None:
    if not isinstance(artifact.get("repo_id"), str) or not str(artifact.get("repo_id")).strip():
        errors.append(f"hypothesis map field repo_id must be a non-empty string: {path.relative_to(root)}")
    seed_context = artifact.get("seed_context")
    if not isinstance(seed_context, dict):
        errors.append(f"hypothesis map field seed_context must be an object: {path.relative_to(root)}")
    evidence_categories = artifact.get("evidence_categories")
    if not isinstance(evidence_categories, dict):
        errors.append(f"hypothesis map field evidence_categories must be an object: {path.relative_to(root)}")
    hypotheses = artifact.get("hypotheses")
    if not isinstance(hypotheses, list):
        errors.append(f"hypothesis map field hypotheses must be a list: {path.relative_to(root)}")
        return
    for index, hypothesis in enumerate(hypotheses, start=1):
        if not isinstance(hypothesis, dict):
            errors.append(f"hypothesis map item {index} must be an object")
            continue
        if not isinstance(hypothesis.get("name"), str) or not str(hypothesis.get("name")).strip():
            errors.append(f"hypothesis map item {index} field name must be a non-empty string")
        if not isinstance(hypothesis.get("evidence_paths"), list):
            errors.append(f"hypothesis map item {index} field evidence_paths must be a list")
        if not isinstance(hypothesis.get("unknown"), bool):
            errors.append(f"hypothesis map item {index} field unknown must be true or false")


def _validate_generated_skills(skills_root: Path, root: Path, errors: list[str]) -> set[str]:
    if not skills_root.is_dir():
        errors.append(f"missing generated skills directory: {skills_root.relative_to(root)}")
        return set()

    skill_names: set[str] = set()
    for skill_root in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        name = skill_root.name
        if not _is_valid_skill_name(name):
            errors.append(
                f"generated skill directory name is invalid: {skill_root.relative_to(root)} "
                "(use lowercase kebab-case without leading, trailing, or consecutive hyphens)"
            )
        skill_path = skill_root / "SKILL.md"
        if not skill_path.is_file():
            errors.append(f"missing generated skill file: {skill_path.relative_to(root)}")
            continue
        text = skill_path.read_text(encoding="utf-8")
        frontmatter = _parse_skill_frontmatter(text)
        if frontmatter is None:
            errors.append(f"SKILL.md missing frontmatter: {skill_path.relative_to(root)}")
        else:
            frontmatter_name = frontmatter.get("name")
            if frontmatter_name != name:
                errors.append(
                    f"SKILL.md frontmatter name must match directory for {skill_path.relative_to(root)} "
                    f"(expected {name})"
                )
            if not isinstance(frontmatter.get("description"), str) or not str(frontmatter.get("description")).strip():
                errors.append(f"SKILL.md frontmatter description must be non-empty: {skill_path.relative_to(root)}")
        _validate_skill_references(text, skill_root, root, errors)
        skill_names.add(name)

    if not skill_names:
        errors.append(f"generated skills directory is empty: {skills_root.relative_to(root)}")
    return skill_names


def _parse_skill_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    frontmatter: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return frontmatter
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return None


def _validate_skill_references(text: str, skill_root: Path, root: Path, errors: list[str]) -> None:
    references: set[str] = set()
    references.update(re.findall(r"`(references/[^`]+)`", text))
    references.update(f"references/{match}" for match in re.findall(r"\]\(references/([^)]+)\)", text))
    for reference in sorted(references):
        if not (skill_root / reference).is_file():
            errors.append(f"SKILL.md has broken reference link: {skill_root.relative_to(root)}/{reference}")


def _existing_org_skill_names(root: Path) -> set[str]:
    org_skills_root = root / "org" / "skills"
    if not org_skills_root.is_dir():
        return set()
    return {path.name for path in org_skills_root.iterdir() if path.is_dir()}


def _validate_resolvers_artifact(path: Path, root: Path, known_skills: set[str], errors: list[str]) -> None:
    artifact = _load_json_artifact(path, "resolvers", errors, root)
    if not isinstance(artifact, dict):
        return
    resolvers = artifact.get("resolvers")
    if not isinstance(resolvers, list):
        errors.append(f"resolvers.yml field resolvers must be a list: {path.relative_to(root)}")
        return
    for index, resolver in enumerate(resolvers, start=1):
        if not isinstance(resolver, dict):
            errors.append(f"resolvers.yml item {index} must be an object")
            continue
        skill = resolver.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            errors.append(f"resolvers.yml item {index} field skill must be a non-empty string")
        elif skill not in known_skills:
            errors.append(f"resolvers.yml item {index} references missing skill: {skill}")
        if not isinstance(resolver.get("intent"), str) or not str(resolver.get("intent")).strip():
            errors.append(f"resolvers.yml item {index} field intent must be a non-empty string")


def _validate_evals_artifact(path: Path, root: Path, errors: list[str]) -> None:
    artifact = _load_json_artifact(path, "evals", errors, root)
    if not isinstance(artifact, dict):
        return
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list):
        errors.append(f"evals/onboarding.yml field tasks must be a list: {path.relative_to(root)}")
        return
    if not 8 <= len(tasks) <= 12:
        errors.append(f"evals/onboarding.yml must contain 8-12 tasks: {path.relative_to(root)}")
    categories = {"repo knowledge", "command selection", "safe procedure", "resolver behavior"}
    evidence_fields = ("expected_files", "expected_commands", "expected_contains", "forbidden_contains")
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            errors.append(f"evals/onboarding.yml task {index} must be an object")
            continue
        for field in ("id", "category", "prompt"):
            if not isinstance(task.get(field), str) or not str(task.get(field)).strip():
                errors.append(f"evals/onboarding.yml task {index} field {field} must be a non-empty string")
        if task.get("category") not in categories:
            errors.append(f"evals/onboarding.yml task {index} has invalid category: {task.get('category')}")
        if not any(isinstance(task.get(field), list) and task.get(field) for field in evidence_fields):
            errors.append(f"evals/onboarding.yml task {index} must include objective expected evidence")
        for field in evidence_fields:
            if not isinstance(task.get(field), list):
                errors.append(f"evals/onboarding.yml task {index} field {field} must be a list")


def _validate_script_manifest(path: Path, artifact_root: Path, root: Path, errors: list[str]) -> None:
    artifact = _load_json_artifact(path, "script manifest", errors, root)
    if not isinstance(artifact, dict):
        return
    scripts = artifact.get("scripts")
    if not isinstance(scripts, list):
        errors.append(f"scripts/manifest.yml field scripts must be a list: {path.relative_to(root)}")
        return
    script_paths: list[str] = []
    for index, script in enumerate(scripts, start=1):
        if not isinstance(script, dict):
            errors.append(f"scripts/manifest.yml item {index} must be an object")
            continue
        relative_path = script.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            errors.append(f"scripts/manifest.yml item {index} field path must be a non-empty string")
            continue
        script_paths.append(relative_path)
        script_path = artifact_root / relative_path
        if not script_path.is_file():
            errors.append(f"scripts/manifest.yml item {index} references missing script: {relative_path}")
        for field in ("review_required", "deterministic", "local_only"):
            if script.get(field) is not True:
                errors.append(f"scripts/manifest.yml item {index} field {field} must be true")
    _validate_command_permissions(artifact, script_paths, artifact_root, errors)


def _validate_command_permissions(
    artifact: dict[str, object],
    script_paths: list[str],
    artifact_root: Path,
    errors: list[str],
) -> None:
    permissions = artifact.get("command_permissions")
    if not isinstance(permissions, list) or not permissions:
        errors.append(f"scripts/manifest.yml field command_permissions must be a non-empty list")
        return

    commands: set[str] = set()
    for index, permission in enumerate(permissions, start=1):
        if not isinstance(permission, dict):
            errors.append(f"scripts/manifest.yml command_permissions item {index} must be an object")
            continue
        command = permission.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"scripts/manifest.yml command_permissions item {index} field command must be a non-empty string")
        else:
            commands.add(command)
        if not isinstance(permission.get("reason"), str) or not str(permission.get("reason")).strip():
            errors.append(f"scripts/manifest.yml command_permissions item {index} field reason must be a non-empty string")
        for field in ("review_required", "local_only"):
            if permission.get(field) is not True:
                errors.append(f"scripts/manifest.yml command_permissions item {index} field {field} must be true")

    for script_path in script_paths:
        expected = f"python {script_path}"
        if expected not in commands:
            errors.append(f"scripts/manifest.yml missing command permission record for generated script: {expected}")
    expected_validate = f"harness validate {artifact_root.name}"
    if expected_validate not in commands:
        errors.append(
            "scripts/manifest.yml missing command permission record for local validation command: "
            f"{expected_validate}"
        )


def _validate_pack_report(path: Path, root: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing pack report: {path.relative_to(root)}")
        return
    text = path.read_text(encoding="utf-8")
    status_markers = (
        "Status: draft",
        "Status: approved-unverified",
        "Status: verified",
        "Status: needs-investigation",
    )
    if not any(marker in text for marker in status_markers):
        errors.append(f"pack-report.md must state pack status: {path.relative_to(root)}")
    if "Status: draft" in text and ("not approved" not in text or "not verified" not in text):
        errors.append(f"pack-report.md must not imply approval or verification: {path.relative_to(root)}")


def _validate_approval_metadata(root: Path, repo_id: str, path: Path, errors: list[str]) -> None:
    entries = parse_repo_block(next(block for block in split_top_level_blocks((root / "harness.yml").read_text(encoding="utf-8")) if block.key == "repos"))
    entry = next((candidate for candidate in entries if candidate.id == repo_id), None)
    if entry is None or entry.coverage_status not in {"approved-unverified", "verified"}:
        return

    artifact = _load_json_artifact(path, "approval metadata", errors, root)
    if not isinstance(artifact, dict):
        return

    if artifact.get("schema_version") != 1:
        errors.append(f"approval.yml field schema_version must be 1: {path.relative_to(root)}")
    if artifact.get("repo_id") != repo_id:
        errors.append(f"approval.yml field repo_id must be {repo_id}: {path.relative_to(root)}")
    if entry.coverage_status == "verified":
        if artifact.get("status") != "verified":
            errors.append(f"approval.yml field status must be verified: {path.relative_to(root)}")
        if artifact.get("verified") is not True:
            errors.append(f"approval.yml field verified must be true for verified packs")
        verification = artifact.get("verification")
        if not isinstance(verification, dict):
            errors.append("approval.yml must include verification metadata for verified packs")
    else:
        if artifact.get("status") != "approved-unverified":
            errors.append(f"approval.yml field status must be approved-unverified: {path.relative_to(root)}")
        if artifact.get("verified") is not False:
            errors.append(f"approval.yml field verified must be false for approved-unverified packs")
        warnings = artifact.get("warnings")
        if not isinstance(warnings, list) or not any(
            isinstance(warning, dict) and warning.get("code") == "approved-unverified" for warning in warnings
        ):
            errors.append("approval.yml must include approved-unverified warning metadata")
    if artifact.get("decision") != "approved":
        errors.append(f"approval.yml field decision must be approved: {path.relative_to(root)}")
    if artifact.get("pack_ref") != entry.pack_ref:
        errors.append(f"approval.yml field pack_ref must match harness.yml repo pack_ref")

    approved_artifacts = artifact.get("approved_artifacts")
    if not isinstance(approved_artifacts, list) or not approved_artifacts:
        errors.append(f"approval.yml field approved_artifacts must be a non-empty list")
        approved_artifacts = []
    excluded_artifacts = artifact.get("excluded_artifacts")
    if not isinstance(excluded_artifacts, list):
        errors.append(f"approval.yml field excluded_artifacts must be a list")
    protected_artifacts = artifact.get("protected_artifacts")
    if not isinstance(protected_artifacts, list) or not protected_artifacts:
        errors.append(f"approval.yml field protected_artifacts must be a non-empty list")
        protected_artifacts = []

    approved_paths = {item for item in approved_artifacts if isinstance(item, str)}
    protected_paths: set[str] = set()
    for index, protected in enumerate(protected_artifacts, start=1):
        if not isinstance(protected, dict):
            errors.append(f"approval.yml protected_artifacts item {index} must be an object")
            continue
        protected_path = protected.get("path")
        if not isinstance(protected_path, str) or not protected_path.strip():
            errors.append(f"approval.yml protected_artifacts item {index} field path must be a non-empty string")
            continue
        protected_paths.add(protected_path)
        if protected.get("protected") is not True:
            errors.append(f"approval.yml protected_artifacts item {index} field protected must be true")
        if not isinstance(protected.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", str(protected.get("sha256"))):
            errors.append(f"approval.yml protected_artifacts item {index} field sha256 must be a hex digest")
        artifact_path = root / protected_path
        if not artifact_path.is_file():
            errors.append(f"approval.yml protected_artifacts item {index} references missing artifact: {protected_path}")

    if approved_paths and protected_paths != approved_paths:
        errors.append("approval.yml protected_artifacts must exactly match approved_artifacts")


def _validate_repo_entry(
    repo_id: str,
    coverage_status: str,
    active: bool,
    local_path: str | None,
    deactivation_reason: str | None,
    external: bool,
) -> list[str]:
    errors: list[str] = []

    if not _is_valid_repo_id(repo_id):
        errors.append(
            f"harness.yml repo id is invalid: {repo_id} "
            "(use letters, numbers, dots, underscores, or hyphens)"
        )
    if coverage_status not in {
        "selected",
        "onboarding",
        "needs-investigation",
        "draft",
        "approved-unverified",
        "verified",
        "deactivated",
        "external",
    }:
        errors.append(
            f"harness.yml repo {repo_id} has invalid coverage_status: {coverage_status} "
            "(supported values: selected, onboarding, needs-investigation, draft, approved-unverified, verified, deactivated, external)"
        )
    if coverage_status in {"selected", "onboarding", "needs-investigation", "draft", "approved-unverified", "verified"}:
        if not active:
            errors.append(f"harness.yml repo {repo_id} with {coverage_status} coverage must be active")
        if external:
            errors.append(f"harness.yml repo {repo_id} cannot be both {coverage_status} coverage and external")
    if coverage_status == "deactivated":
        if active:
            errors.append(f"harness.yml repo {repo_id} with deactivated coverage must be inactive")
        if deactivation_reason is None or not deactivation_reason.strip():
            errors.append(f"harness.yml repo {repo_id} with deactivated coverage must include deactivation_reason")
        if external:
            errors.append(f"harness.yml repo {repo_id} cannot be both deactivated coverage and external")
    if coverage_status == "external":
        if not external:
            errors.append(f"harness.yml repo {repo_id} with external coverage must set external: true")
        if active:
            errors.append(f"harness.yml repo {repo_id} with external coverage must be inactive")
    if local_path is not None and Path(local_path).is_absolute():
        errors.append(f"harness.yml repo {repo_id} local_path must be relative to the org pack root")

    return errors


def _is_valid_org_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None


def _is_valid_repo_id(repo_id: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo_id) is not None


def _is_valid_skill_name(name: str) -> bool:
    return re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None
