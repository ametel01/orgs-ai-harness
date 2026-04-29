"""Map changed repo paths to generated review context artifacts."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class ReviewContextError(Exception):
    """Raised when review context cannot resolve the requested repository."""


@dataclass(frozen=True)
class ChangedPath:
    raw_path: str
    normalized_path: str | None
    classification: str
    reason: str
    exists: bool | None = None


@dataclass(frozen=True)
class ArtifactStatus:
    name: str
    path: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class MissingCoverage:
    kind: str
    path: str
    reason: str


@dataclass(frozen=True)
class MatchedSkill:
    name: str
    path: str
    description: str | None
    triggers: tuple[str, ...]
    matched_paths: tuple[str, ...]
    match_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceCategoryMatch:
    category: str
    evidence_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class UnknownCoverage:
    id: str
    question: str | None
    severity: str | None
    status: str | None
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class ReviewContext:
    repo_id: str
    repo_name: str
    repo_path: Path | None
    artifact_root: Path
    changed_paths: tuple[ChangedPath, ...]
    artifacts: tuple[ArtifactStatus, ...]
    matched_skills: tuple[MatchedSkill, ...]
    evidence_matches: tuple[EvidenceCategoryMatch, ...]
    unknowns: tuple[UnknownCoverage, ...]
    missing_coverage: tuple[MissingCoverage, ...]


@dataclass(frozen=True)
class _ResolverArtifact:
    skill: str
    intent: str | None
    when: tuple[str, ...]


@dataclass(frozen=True)
class _SkillArtifact:
    name: str
    path: str
    description: str | None
    triggers: tuple[str, ...]
    text: str


_STOPWORDS = {
    "about",
    "after",
    "and",
    "before",
    "change",
    "changed",
    "changing",
    "code",
    "edit",
    "editing",
    "file",
    "files",
    "fix",
    "fixing",
    "for",
    "from",
    "harness",
    "into",
    "module",
    "modules",
    "orgs",
    "path",
    "paths",
    "repo",
    "repository",
    "skill",
    "this",
    "use",
    "when",
    "with",
}
_HINT_RE = re.compile(
    r"`([^`]+)`|(?<![\w.-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?:/)?)"
    r"|(?<![\w.-])([A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|json|toml|md|yml|yaml|go|rs|sh))"
)


def build_review_context(root: Path, repo_id: str, changed_paths: tuple[str, ...]) -> ReviewContext:
    """Build a deterministic, read-only context map for PR changed files."""

    root = root.resolve()
    entry = _find_repo_entry(root, repo_id)
    repo_path = _resolve_optional_repo_path(root, entry)
    artifact_root = root / "repos" / entry.id

    artifacts: list[ArtifactStatus] = []
    missing: list[MissingCoverage] = []
    normalized_paths = _classify_changed_paths(changed_paths, repo_path)

    skills = _load_skills(root, artifact_root, artifacts, missing)
    resolvers = _load_resolvers(root, artifact_root, artifacts, missing)
    evidence_categories = _load_scan_evidence(root, artifact_root, artifacts, missing)
    unknowns = _load_unknowns(root, artifact_root, artifacts, missing)

    matched_skills = _match_skills(normalized_paths, skills, resolvers)
    evidence_matches = _match_evidence(normalized_paths, evidence_categories)
    missing.extend(_missing_path_coverage(normalized_paths, matched_skills, evidence_matches))

    return ReviewContext(
        repo_id=entry.id,
        repo_name=entry.name,
        repo_path=repo_path,
        artifact_root=artifact_root,
        changed_paths=tuple(normalized_paths),
        artifacts=tuple(artifacts),
        matched_skills=tuple(matched_skills),
        evidence_matches=tuple(evidence_matches),
        unknowns=tuple(unknowns),
        missing_coverage=tuple(missing),
    )


def _find_repo_entry(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise ReviewContextError("repo id cannot be empty")
    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == normalized_repo_id:
            return entry
    raise ReviewContextError(f"repo id is not registered: {normalized_repo_id}")


def _resolve_optional_repo_path(root: Path, entry: RepoEntry) -> Path | None:
    if entry.local_path is None:
        return None
    repo_path = (root / entry.local_path).resolve()
    if not repo_path.is_dir():
        return None
    return repo_path


def _classify_changed_paths(raw_paths: tuple[str, ...], repo_path: Path | None) -> list[ChangedPath]:
    classified: list[ChangedPath] = []
    seen_repo_paths: set[tuple[str, str | None]] = set()
    for raw_path in raw_paths:
        raw_value = raw_path.strip()
        if not raw_value:
            candidate = ChangedPath(raw_path, None, "ignored", "empty path")
            key = (candidate.classification, candidate.normalized_path or candidate.reason)
            if key not in seen_repo_paths:
                classified.append(candidate)
                seen_repo_paths.add(key)
            continue

        normalized = _normalize_repo_relative_path(raw_value)
        if normalized is None:
            candidate = ChangedPath(raw_path, None, "outside", "path is absolute or leaves the repository")
        elif ".git" in normalized.split("/"):
            candidate = ChangedPath(raw_path, normalized, "ignored", "path is inside .git")
        elif _is_harness_internal_path(normalized):
            candidate = ChangedPath(
                raw_path,
                normalized,
                "harness-internal",
                "path belongs to harness-generated metadata",
                _path_exists(repo_path, normalized),
            )
        else:
            candidate = ChangedPath(
                raw_path,
                normalized,
                "repo",
                "repo-relative changed path",
                _path_exists(repo_path, normalized),
            )

        key = (candidate.classification, candidate.normalized_path or candidate.reason)
        if key in seen_repo_paths:
            continue
        classified.append(candidate)
        seen_repo_paths.add(key)
    return sorted(classified, key=lambda item: (item.classification, item.normalized_path or "", item.raw_path))


def _normalize_repo_relative_path(raw_path: str) -> str | None:
    value = raw_path.replace("\\", "/")
    if value.startswith("/"):
        return None
    normalized = posixpath.normpath(value)
    if normalized in ("", "."):
        return "."
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _is_harness_internal_path(path: str) -> bool:
    return path in {".orgs-ai-harness-attachment"} or path.startswith(("org-agent-skills/", ".agent-harness/"))


def _path_exists(repo_path: Path | None, relative_path: str) -> bool | None:
    if repo_path is None:
        return None
    return (repo_path / relative_path).exists()


def _load_skills(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingCoverage],
) -> tuple[_SkillArtifact, ...]:
    skills_root = artifact_root / "skills"
    relative_root = _relative(root, skills_root)
    if not skills_root.is_dir():
        artifacts.append(ArtifactStatus("skills", relative_root, "missing", "skills directory is missing"))
        missing.append(MissingCoverage("artifact", relative_root, "skills directory is missing"))
        return ()

    artifacts.append(ArtifactStatus("skills", relative_root, "loaded"))
    skills: list[_SkillArtifact] = []
    for skill_root in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_path = skill_root / "SKILL.md"
        relative_skill_path = _relative(root, skill_path)
        if not skill_path.is_file():
            missing.append(MissingCoverage("artifact", relative_skill_path, "SKILL.md is missing"))
            continue
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            missing.append(MissingCoverage("artifact", relative_skill_path, f"cannot read SKILL.md: {exc}"))
            continue
        frontmatter = _parse_frontmatter(text)
        if frontmatter is None:
            missing.append(MissingCoverage("artifact", relative_skill_path, "SKILL.md frontmatter is malformed"))
            continue
        name = frontmatter.get("name") or skill_root.name
        description = frontmatter.get("description")
        skills.append(
            _SkillArtifact(
                name=name,
                path=relative_skill_path,
                description=description,
                triggers=_extract_skill_triggers(text, description),
                text=text,
            )
        )
    return tuple(skills)


def _load_resolvers(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingCoverage],
) -> tuple[_ResolverArtifact, ...]:
    path = artifact_root / "resolvers.yml"
    artifact = _load_json_artifact(root, path, "resolvers", artifacts, missing)
    if not isinstance(artifact, dict):
        return ()
    raw_resolvers = artifact.get("resolvers")
    if not isinstance(raw_resolvers, list):
        missing.append(
            MissingCoverage("artifact", _relative(root, path), "resolvers.yml field resolvers must be a list")
        )
        return ()

    resolvers: list[_ResolverArtifact] = []
    for item in raw_resolvers:
        if not isinstance(item, dict):
            continue
        skill = item.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            continue
        when = item.get("when")
        resolvers.append(
            _ResolverArtifact(
                skill=skill,
                intent=item.get("intent") if isinstance(item.get("intent"), str) else None,
                when=tuple(str(value) for value in when if isinstance(value, str)) if isinstance(when, list) else (),
            )
        )
    return tuple(resolvers)


def _load_scan_evidence(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingCoverage],
) -> dict[str, set[str]]:
    evidence: dict[str, set[str]] = {}

    manifest_path = artifact_root / "scan" / "scan-manifest.yml"
    manifest = _load_json_artifact(root, manifest_path, "scan-manifest", artifacts, missing)
    if isinstance(manifest, dict):
        for item in _as_dict_list(manifest.get("scanned_paths")):
            category = item.get("category")
            path = item.get("path")
            if isinstance(category, str) and isinstance(path, str):
                evidence.setdefault(category, set()).add(_normalize_evidence_path(path))

    hypothesis_path = artifact_root / "scan" / "hypothesis-map.yml"
    hypothesis = _load_json_artifact(root, hypothesis_path, "hypothesis-map", artifacts, missing)
    if isinstance(hypothesis, dict):
        categories = hypothesis.get("evidence_categories")
        if isinstance(categories, dict):
            for category, paths in categories.items():
                if not isinstance(category, str) or not isinstance(paths, list):
                    continue
                for path in paths:
                    if isinstance(path, str):
                        evidence.setdefault(category, set()).add(_normalize_evidence_path(path))
    return evidence


def _load_unknowns(
    root: Path,
    artifact_root: Path,
    artifacts: list[ArtifactStatus],
    missing: list[MissingCoverage],
) -> tuple[UnknownCoverage, ...]:
    path = artifact_root / "unknowns.yml"
    artifact = _load_json_artifact(root, path, "unknowns", artifacts, missing)
    if not isinstance(artifact, dict):
        return ()
    unknowns = []
    for item in _as_dict_list(artifact.get("unknowns")):
        unknown_id = item.get("id")
        if not isinstance(unknown_id, str) or not unknown_id.strip():
            continue
        evidence_paths = []
        for evidence in _as_dict_list(item.get("evidence")):
            evidence_path = evidence.get("path")
            if isinstance(evidence_path, str):
                evidence_paths.append(_normalize_evidence_path(evidence_path))
        question = item.get("question")
        severity = item.get("severity")
        status = item.get("status")
        unknowns.append(
            UnknownCoverage(
                id=unknown_id,
                question=question if isinstance(question, str) else None,
                severity=severity if isinstance(severity, str) else None,
                status=status if isinstance(status, str) else None,
                evidence_paths=tuple(sorted(set(evidence_paths))),
            )
        )
    return tuple(unknowns)


def _load_json_artifact(
    root: Path,
    path: Path,
    name: str,
    artifacts: list[ArtifactStatus],
    missing: list[MissingCoverage],
) -> Any:
    relative_path = _relative(root, path)
    if not path.is_file():
        artifacts.append(ArtifactStatus(name, relative_path, "missing", "artifact is missing"))
        missing.append(MissingCoverage("artifact", relative_path, "artifact is missing"))
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        artifacts.append(ArtifactStatus(name, relative_path, "malformed", str(exc)))
        missing.append(MissingCoverage("artifact", relative_path, f"artifact is malformed: {exc}"))
        return None
    artifacts.append(ArtifactStatus(name, relative_path, "loaded"))
    return artifact


def _match_skills(
    changed_paths: list[ChangedPath],
    skills: tuple[_SkillArtifact, ...],
    resolvers: tuple[_ResolverArtifact, ...],
) -> list[MatchedSkill]:
    resolver_by_skill: dict[str, list[_ResolverArtifact]] = {}
    for resolver in resolvers:
        resolver_by_skill.setdefault(resolver.skill, []).append(resolver)

    matches: list[MatchedSkill] = []
    usable_paths = [path for path in changed_paths if path.normalized_path and path.classification == "repo"]
    for skill in skills:
        resolver_triggers = tuple(
            trigger for resolver in resolver_by_skill.get(skill.name, ()) for trigger in resolver.when
        )
        triggers = _dedupe((*skill.triggers, *resolver_triggers))
        reasons_by_path: dict[str, set[str]] = {}
        hints = _path_hints(f"{skill.path}\n{skill.text}\n{' '.join(triggers)}")
        skill_tokens = _significant_tokens(f"{skill.name} {skill.description or ''} {' '.join(triggers)}")

        for changed_path in usable_paths:
            normalized_path = changed_path.normalized_path
            if normalized_path is None:
                continue
            for hint in hints:
                if _path_hint_matches(normalized_path, hint):
                    reasons_by_path.setdefault(normalized_path, set()).add(f"skill-path:{hint}")
            overlap = _path_tokens(normalized_path) & skill_tokens
            if len(overlap) >= 2:
                reasons_by_path.setdefault(normalized_path, set()).add("skill-text:" + ",".join(sorted(overlap)))

            for resolver in resolver_by_skill.get(skill.name, ()):
                resolver_tokens = _significant_tokens(f"{resolver.intent or ''} {' '.join(resolver.when)}")
                resolver_overlap = _path_tokens(normalized_path) & resolver_tokens
                if len(resolver_overlap) >= 2:
                    reasons_by_path.setdefault(normalized_path, set()).add(
                        "resolver:" + ",".join(sorted(resolver_overlap))
                    )

        if reasons_by_path:
            matched_paths = tuple(sorted(reasons_by_path))
            reasons = tuple(sorted({reason for reasons in reasons_by_path.values() for reason in reasons}))
            matches.append(
                MatchedSkill(
                    name=skill.name,
                    path=skill.path,
                    description=skill.description,
                    triggers=triggers,
                    matched_paths=matched_paths,
                    match_reasons=reasons,
                )
            )
    return sorted(matches, key=lambda match: (match.name, match.path))


def _match_evidence(
    changed_paths: list[ChangedPath],
    evidence_categories: dict[str, set[str]],
) -> list[EvidenceCategoryMatch]:
    matches: list[EvidenceCategoryMatch] = []
    usable_paths = [
        path.normalized_path for path in changed_paths if path.normalized_path and path.classification == "repo"
    ]
    for category, evidence_paths in sorted(evidence_categories.items()):
        changed_matches = sorted(
            path for path in usable_paths if any(_evidence_path_matches(path, evidence) for evidence in evidence_paths)
        )
        if not changed_matches:
            continue
        matched_evidence = sorted(
            evidence for evidence in evidence_paths if _matches_any_changed_path(evidence, changed_matches)
        )
        matches.append(
            EvidenceCategoryMatch(
                category=category,
                evidence_paths=tuple(matched_evidence),
                changed_paths=tuple(changed_matches),
            )
        )
    return matches


def _missing_path_coverage(
    changed_paths: list[ChangedPath],
    matched_skills: list[MatchedSkill],
    evidence_matches: list[EvidenceCategoryMatch],
) -> list[MissingCoverage]:
    covered_by_skill = {path for match in matched_skills for path in match.matched_paths}
    covered_by_evidence = {path for match in evidence_matches for path in match.changed_paths}
    missing: list[MissingCoverage] = []
    for changed_path in changed_paths:
        if changed_path.normalized_path is None:
            missing.append(MissingCoverage(changed_path.classification, changed_path.raw_path, changed_path.reason))
            continue
        if changed_path.classification != "repo":
            missing.append(
                MissingCoverage(changed_path.classification, changed_path.normalized_path, changed_path.reason)
            )
            continue
        if changed_path.exists is False:
            missing.append(
                MissingCoverage(
                    "changed_path",
                    changed_path.normalized_path,
                    "changed path is not present in the local checkout",
                )
            )
        if (
            changed_path.normalized_path not in covered_by_skill
            and changed_path.normalized_path not in covered_by_evidence
        ):
            missing.append(
                MissingCoverage(
                    "changed_path",
                    changed_path.normalized_path,
                    "no matching skill, resolver, or scan evidence",
                )
            )
    return missing


def _parse_frontmatter(text: str) -> dict[str, str] | None:
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
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return None


def _extract_skill_triggers(text: str, description: str | None) -> tuple[str, ...]:
    triggers: list[str] = []
    if description:
        triggers.extend(sorted(_significant_tokens(description)))
    for line in text.splitlines():
        if "task mentions:" not in line.lower():
            continue
        _, raw_triggers = line.split(":", 1)
        triggers.extend(trigger.strip(" .`") for trigger in raw_triggers.split(",") if trigger.strip(" .`"))
    return _dedupe(tuple(triggers))


def _path_hints(text: str) -> tuple[str, ...]:
    hints: list[str] = []
    for match in _HINT_RE.finditer(text):
        hint = next(group for group in match.groups() if group)
        normalized = _normalize_evidence_path(hint.strip("`'\" "))
        if normalized and normalized != ".":
            hints.append(normalized)
    return _dedupe(tuple(hints))


def _path_hint_matches(path: str, hint: str) -> bool:
    path = _normalize_evidence_path(path)
    hint = _normalize_evidence_path(hint)
    if not path or not hint:
        return False
    if path == hint:
        return True
    if hint.endswith("/") and path.startswith(hint):
        return True
    if path.startswith(f"{hint}/"):
        return True
    path_parent = path.rsplit("/", 1)[0] if "/" in path else ""
    hint_parent = hint.rsplit("/", 1)[0] if "/" in hint else ""
    if not path_parent or path_parent != hint_parent:
        return False
    return bool(_path_tokens(path.rsplit("/", 1)[-1]) & _path_tokens(hint.rsplit("/", 1)[-1]))


def _evidence_path_matches(path: str, evidence_path: str) -> bool:
    path = _normalize_evidence_path(path)
    evidence_path = _normalize_evidence_path(evidence_path)
    if path == evidence_path:
        return True
    if path.startswith(f"{evidence_path}/") or evidence_path.startswith(f"{path}/"):
        return True
    return False


def _matches_any_changed_path(evidence_path: str, changed_paths: list[str]) -> bool:
    return any(_evidence_path_matches(path, evidence_path) for path in changed_paths)


def _normalize_evidence_path(path: str) -> str:
    normalized = _normalize_repo_relative_path(path.strip().strip("`"))
    return normalized or ""


def _path_tokens(path: str) -> set[str]:
    return _significant_tokens(path.replace("/", " "))


def _significant_tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 4:
            tokens.add(token[:-1])
    return tokens


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    deduped = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return tuple(deduped)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
