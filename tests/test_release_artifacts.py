from __future__ import annotations

from typing import cast

from orgs_ai_harness.release_artifacts import (
    RELEASE_READINESS_SCHEMA_VERSION,
    build_release_readiness_artifacts,
)
from orgs_ai_harness.release_readiness import ReleaseReadinessInput

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class ReleaseReadinessArtifactTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
        return env

    def run_cli(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "orgs_ai_harness", *args],
            cwd=cwd,
            env=self.cli_env(),
            text=True,
            capture_output=True,
            check=False,
        )

    def prepare_repo_with_release_artifacts(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        (repo_path / "CHANGELOG.md").write_text("# Changelog\n\n## 1.2.3\n", encoding="utf-8")
        (repo_path / "docs").mkdir()
        (repo_path / "docs" / "release-notes.md").write_text("# Notes\n", encoding="utf-8")
        (repo_path / "package.json").write_text(
            json.dumps({"name": "fixture-repo", "version": "1.2.3", "scripts": {"test": "pytest"}}),
            encoding="utf-8",
        )
        (repo_path / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        (repo_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
        (repo_path / ".github" / "workflows").mkdir(parents=True)
        (repo_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
        (repo_path / "migrations").mkdir()
        (repo_path / "migrations" / "001_init.sql").write_text("select 1;\n", encoding="utf-8")
        (repo_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")

        entries = load_repo_entries(root / "harness.yml")
        save_repo_entries(
            root / "harness.yml",
            (
                replace(
                    entries[0],
                    coverage_status="verified",
                    pack_ref="repos/fixture-repo/approval.yml",
                ),
            ),
        )

        artifact_root = root / "repos" / "fixture-repo"
        artifact_root.mkdir(parents=True)
        (artifact_root / "scripts").mkdir()
        (artifact_root / "scripts" / "manifest.yml").write_text(
            json.dumps({"command_permissions": [{"command": "make test", "reason": "Run local tests."}]}),
            encoding="utf-8",
        )
        (artifact_root / "evals").mkdir()
        (artifact_root / "evals" / "onboarding.yml").write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "release-docs",
                            "expected_files": ["CHANGELOG.md"],
                            "expected_commands": ["make test"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (artifact_root / "eval-report.yml").write_text(
            json.dumps({"repo_id": "fixture-repo", "status": "verified", "skill_pack_pass_rate": 1.0}),
            encoding="utf-8",
        )
        (artifact_root / "unknowns.yml").write_text(json.dumps({"unknowns": []}), encoding="utf-8")
        (artifact_root / "scan").mkdir()
        (artifact_root / "scan" / "scan-manifest.yml").write_text(
            json.dumps({"scanned_paths": [{"path": "CHANGELOG.md", "category": "release_notes"}]}),
            encoding="utf-8",
        )
        (artifact_root / "scan" / "hypothesis-map.yml").write_text(
            json.dumps({"evidence_categories": {"release_notes": ["CHANGELOG.md"]}, "hypotheses": []}),
            encoding="utf-8",
        )
        skill_root = artifact_root / "skills" / "release-workflow"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            "---\nname: release-workflow\ndescription: Use when checking release readiness.\n---\n# release-workflow\n",
            encoding="utf-8",
        )
        (artifact_root / "resolvers.yml").write_text(
            json.dumps({"resolvers": [{"intent": "release", "skill": "release-workflow", "when": ["CHANGELOG.md"]}]}),
            encoding="utf-8",
        )
        (artifact_root / "pack-report.md").write_text(
            "# Eval Pack Report: fixture-repo\n\n- Status: verified\n",
            encoding="utf-8",
        )
        protected_hash = hashlib.sha256((artifact_root / "pack-report.md").read_bytes()).hexdigest()
        (artifact_root / "approval.yml").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repo_id": "fixture-repo",
                    "status": "verified",
                    "decision": "approved",
                    "pack_ref": "repos/fixture-repo/approval.yml",
                    "approved_artifacts": ["repos/fixture-repo/pack-report.md"],
                    "excluded_artifacts": [],
                    "protected_artifacts": [
                        {
                            "path": "repos/fixture-repo/pack-report.md",
                            "sha256": protected_hash,
                            "protected": True,
                        }
                    ],
                    "verified": True,
                    "verification": {"status": "verified", "report_path": "repos/fixture-repo/eval-report.yml"},
                }
            ),
            encoding="utf-8",
        )
        return root, repo_path

    def readiness(self, repo_path: Path) -> ReleaseReadinessInput:
        return ReleaseReadinessInput(
            repo_id="fixture-repo",
            repo_path=repo_path.resolve(),
            status="artifact-only",
            version="v1.2.3",
        )

    def test_builds_stable_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_repo_with_release_artifacts(tmp_path)

            artifacts = build_release_readiness_artifacts(
                root,
                self.readiness(repo_path),
                changed_files=("CHANGELOG.md", "docs/release-notes.md"),
            )
            payload = artifacts.json_payload

            self.assertEqual(payload["schema_version"], RELEASE_READINESS_SCHEMA_VERSION)
            self.assertEqual(payload["status"], "artifact-only")
            self.assertEqual(payload["repo_id"], "fixture-repo")
            release = cast(dict[str, object], payload["release"])
            self.assertEqual(release["version"], "v1.2.3")
            self.assertEqual(release["changed_files"], ["CHANGELOG.md", "docs/release-notes.md"])
            lifecycle = cast(dict[str, object], payload["lifecycle"])
            self.assertEqual(lifecycle["registry_status"], "verified")
            self.assertTrue(lifecycle["supported"])
            risk = cast(dict[str, object], payload["risk"])
            self.assertEqual(risk["overall"], "low")
            self.assertEqual(
                [item["command"] for item in cast(list[dict[str, object]], risk["suggested_commands"])],
                [
                    "harness validate fixture-repo",
                    "make test",
                ],
            )
            release_evidence = cast(list[dict[str, object]], payload["release_evidence"])
            self.assertIn("changelog", {item["category"] for item in release_evidence})
            self.assertIn("## Suggested Checks", artifacts.markdown)
            self.assertIn("`make test`", artifacts.markdown)
            self.assertIn("## Release Evidence", artifacts.markdown)

    def test_cli_writes_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_repo_with_release_artifacts(tmp_path)
            json_path = tmp_path / ".agent-harness" / "release-readiness" / "readiness.json"
            markdown_path = tmp_path / ".agent-harness" / "release-readiness" / "readiness.md"

            result = self.run_cli(
                tmp_path,
                "release",
                "readiness",
                "--repo-id",
                "fixture-repo",
                "--version",
                "v1.2.3",
                "--files",
                "CHANGELOG.md",
                "docs/release-notes.md",
                "--json-path",
                str(json_path),
                "--markdown-path",
                str(markdown_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("changed_files=2", result.stdout)
            self.assertIn("JSON artifact:", result.stdout)
            self.assertIn("Markdown artifact:", result.stdout)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], RELEASE_READINESS_SCHEMA_VERSION)
            self.assertEqual(payload["risk"]["overall"], "low")
            self.assertIn("# Release Readiness Artifact: fixture-repo", markdown_path.read_text(encoding="utf-8"))

    def test_empty_sections_are_explicit_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "fixture-repo")
            readiness = self.readiness(repo_path)

            first = build_release_readiness_artifacts(root, readiness)
            second = build_release_readiness_artifacts(root, readiness)

            self.assertEqual(first.json_payload, second.json_payload)
            self.assertEqual(first.markdown, second.markdown)
            risk = cast(dict[str, object], first.json_payload["risk"])
            self.assertEqual(risk["suggested_evals"], [])
            self.assertIn("## Changed Files\n\n- None.", first.markdown)
            self.assertIn("## Suggested Evals\n\n- None.", first.markdown)
            self.assertIn("missing-artifact", json.dumps(first.json_payload, sort_keys=True))
