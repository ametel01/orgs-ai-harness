"""Read-only repository onboarding scans."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from orgs_ai_harness.repo_registry import RepoEntry, load_repo_entries


class RepoOnboardingError(Exception):
    """Raised when repo onboarding cannot be completed."""


@dataclass(frozen=True)
class OnboardingResult:
    repo_id: str
    artifact_root: Path
    summary_path: Path
    unknowns_path: Path
    scan_manifest_path: Path


SAFE_EVIDENCE_FILES = {
    "README.md": "readme",
    "README": "readme",
    "package.json": "package_manifest",
    "pyproject.toml": "package_manifest",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SENSITIVE_NAME_PARTS = ("credential", "credentials", "secret", "secrets", "token", "tokens")


def scan_repo_only(root: Path, repo_id: str) -> OnboardingResult:
    """Run a read-only scan for one selected local repository."""

    root = root.resolve()
    entry = _find_repo(root, repo_id)
    repo_path = _resolve_repo_path(root, entry)

    scanned, skipped = _scan_repo(repo_path)
    unknowns = _default_unknowns(scanned)

    artifact_root = root / "repos" / entry.id
    scan_root = artifact_root / "scan"
    scan_root.mkdir(parents=True, exist_ok=True)

    summary_path = artifact_root / "onboarding-summary.md"
    unknowns_path = artifact_root / "unknowns.yml"
    scan_manifest_path = scan_root / "scan-manifest.yml"

    summary_path.write_text(_render_summary(entry, scanned, unknowns), encoding="utf-8")
    unknowns_path.write_text(json.dumps({"unknowns": unknowns}, indent=2) + "\n", encoding="utf-8")
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

    return OnboardingResult(
        repo_id=entry.id,
        artifact_root=artifact_root,
        summary_path=summary_path,
        unknowns_path=unknowns_path,
        scan_manifest_path=scan_manifest_path,
    )


def _find_repo(root: Path, repo_id: str) -> RepoEntry:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id:
        raise RepoOnboardingError("repo id cannot be empty")

    for entry in load_repo_entries(root / "harness.yml"):
        if entry.id == normalized_repo_id:
            if entry.coverage_status == "external" or entry.external:
                raise RepoOnboardingError(f"repo is an external dependency reference, not selected coverage: {normalized_repo_id}")
            if entry.coverage_status != "selected" or not entry.active:
                raise RepoOnboardingError(f"repo is not active selected coverage: {normalized_repo_id}")
            if entry.local_path is None:
                raise RepoOnboardingError(
                    f"repo {normalized_repo_id} has no local path; run 'harness repo discover --clone' "
                    "or 'harness repo set-path'"
                )
            return entry

    raise RepoOnboardingError(f"repo id is not registered: {normalized_repo_id}")


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
            "severity": "important",
            "status": "open",
            "evidence": evidence,
            "recommended_investigation": "Inspect package scripts and CI job command usage.",
        }
    ]


def _render_summary(
    entry: RepoEntry,
    scanned: list[dict[str, str | int]],
    unknowns: list[dict[str, object]],
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
