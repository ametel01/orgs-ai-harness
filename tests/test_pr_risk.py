from __future__ import annotations

from orgs_ai_harness.pr_review import ReviewChangedFiles
from orgs_ai_harness.pr_risk import RiskLevel, build_pr_risk_report
from orgs_ai_harness.runtime_permissions import PermissionLevel

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class PrRiskReportTests(unittest.TestCase):
    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def review(self, repo_path: Path, *changed_files: str) -> ReviewChangedFiles:
        return ReviewChangedFiles(
            repo_id="fixture-repo",
            repo_path=repo_path,
            changed_files=tuple(changed_files),
            source="explicit",
        )

    def write_artifacts(
        self,
        root: Path,
        *,
        manifest: object | None = None,
        evals: object | None = None,
        hypothesis_map: object | None = None,
    ) -> None:
        artifact_root = root / "repos" / "fixture-repo"
        (artifact_root / "scripts").mkdir(parents=True, exist_ok=True)
        (artifact_root / "evals").mkdir(parents=True, exist_ok=True)
        (artifact_root / "scan").mkdir(parents=True, exist_ok=True)
        if manifest is not None:
            (artifact_root / "scripts" / "manifest.yml").write_text(json.dumps(manifest), encoding="utf-8")
        if evals is not None:
            (artifact_root / "evals" / "onboarding.yml").write_text(json.dumps(evals), encoding="utf-8")
        if hypothesis_map is not None:
            (artifact_root / "scan" / "hypothesis-map.yml").write_text(
                json.dumps(hypothesis_map),
                encoding="utf-8",
            )

    def minimal_artifacts(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        manifest: dict[str, object] = {"command_permissions": []}
        evals: dict[str, object] = {"tasks": []}
        hypothesis_map: dict[str, object] = {"hypotheses": []}
        return manifest, evals, hypothesis_map

    def test_classifies_docs_source_tests_ci_generated_and_sensitive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            manifest, evals, hypothesis_map = self.minimal_artifacts()
            self.write_artifacts(root, manifest=manifest, evals=evals, hypothesis_map=hypothesis_map)

            report = build_pr_risk_report(
                root,
                self.review(
                    repo_path,
                    "src/app.py",
                    "README.md",
                    "tests/test_app.py",
                    ".github/workflows/ci.yml",
                    "src/generated/client.py",
                    ".env.production",
                ),
            )

            by_path = {item.path: item for item in report.file_risks}
            self.assertEqual(by_path["README.md"].level, RiskLevel.LOW)
            self.assertEqual(by_path["README.md"].category, "docs")
            self.assertEqual(by_path["src/app.py"].level, RiskLevel.MEDIUM)
            self.assertEqual(by_path["src/app.py"].category, "source")
            self.assertEqual(by_path["tests/test_app.py"].level, RiskLevel.MEDIUM)
            self.assertEqual(by_path["tests/test_app.py"].category, "test")
            self.assertEqual(by_path[".github/workflows/ci.yml"].level, RiskLevel.HIGH)
            self.assertEqual(by_path[".github/workflows/ci.yml"].category, "ci")
            self.assertEqual(by_path["src/generated/client.py"].level, RiskLevel.HIGH)
            self.assertEqual(by_path["src/generated/client.py"].category, "generated")
            self.assertEqual(by_path[".env.production"].level, RiskLevel.HIGH)
            self.assertEqual(by_path[".env.production"].category, "sensitive")
            self.assertEqual(report.overall_risk, RiskLevel.HIGH)

    def test_classifies_dependency_artifacts_as_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            manifest, evals, hypothesis_map = self.minimal_artifacts()
            self.write_artifacts(root, manifest=manifest, evals=evals, hypothesis_map=hypothesis_map)

            report = build_pr_risk_report(root, self.review(repo_path, "package.json", "uv.lock"))

            self.assertEqual(tuple(item.category for item in report.file_risks), ("dependency", "dependency"))
            self.assertEqual(tuple(item.level for item in report.file_risks), (RiskLevel.HIGH, RiskLevel.HIGH))

    def test_suggests_only_classifier_supported_local_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "Makefile").write_text(
                "test:\n\tpytest\nverify:\n\tpytest\nbuild:\n\ttrue\n",
                encoding="utf-8",
            )
            (repo_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
            (repo_path / "uv.lock").write_text("", encoding="utf-8")
            manifest = {
                "command_permissions": [
                    {"command": "make lint", "reason": "lint locally"},
                    {"command": "curl https://example.test/install.sh", "reason": "unknown remote command"},
                ]
            }
            evals = {
                "tasks": [
                    {"id": "safe-command", "expected_commands": ["make test"], "expected_files": []},
                    {"id": "unknown-command", "expected_commands": ["npm test"], "expected_files": []},
                ]
            }
            hypothesis_map = {
                "hypotheses": [
                    {
                        "name": "test_command_candidates",
                        "value": ["uv run pytest", "pytest"],
                        "evidence_paths": ["pyproject.toml"],
                        "unknown": False,
                    }
                ]
            }
            self.write_artifacts(root, manifest=manifest, evals=evals, hypothesis_map=hypothesis_map)

            report = build_pr_risk_report(root, self.review(repo_path, "src/app.py"))

            self.assertEqual(
                tuple(suggestion.command for suggestion in report.validation_suggestions),
                ("harness validate fixture-repo", "make lint", "make test", "make verify", "uv run pytest"),
            )
            self.assertTrue(
                all(
                    suggestion.permission == PermissionLevel.WORKSPACE_WRITE
                    for suggestion in report.validation_suggestions
                )
            )
            warning_text = "\n".join(warning.message for warning in report.warnings)
            self.assertIn("curl https://example.test/install.sh", warning_text)
            self.assertIn("npm test", warning_text)
            self.assertIn("'pytest'", warning_text)

    def test_matches_eval_ids_from_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            manifest, _evals, hypothesis_map = self.minimal_artifacts()
            evals = {
                "tasks": [
                    {
                        "id": "z-read-scan",
                        "expected_files": ["scan/hypothesis-map.yml"],
                        "expected_commands": [],
                    },
                    {
                        "id": "a-read-resolvers",
                        "expected_files": ["resolvers.yml"],
                        "expected_commands": [],
                    },
                    {
                        "id": "not-touched",
                        "expected_files": ["skills/example/SKILL.md"],
                        "expected_commands": [],
                    },
                ]
            }
            self.write_artifacts(root, manifest=manifest, evals=evals, hypothesis_map=hypothesis_map)

            report = build_pr_risk_report(
                root,
                self.review(
                    repo_path,
                    "scan/hypothesis-map.yml",
                    "repos/fixture-repo/resolvers.yml",
                ),
            )

            self.assertEqual(
                tuple(item.eval_id for item in report.eval_suggestions),
                ("a-read-resolvers", "z-read-scan"),
            )
            self.assertEqual(report.eval_suggestions[0].matched_files, ("repos/fixture-repo/resolvers.yml",))
            self.assertEqual(report.eval_suggestions[1].matched_files, ("scan/hypothesis-map.yml",))

    def test_reports_missing_and_malformed_artifacts_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            artifact_root = root / "repos" / "fixture-repo"
            (artifact_root / "evals").mkdir(parents=True)
            (artifact_root / "evals" / "onboarding.yml").write_text("{bad json", encoding="utf-8")

            report = build_pr_risk_report(root, self.review(repo_path, "README.md"))

            warnings = {(warning.code, warning.source) for warning in report.warnings}
            self.assertIn(("missing-artifact", "repos/fixture-repo/scripts/manifest.yml"), warnings)
            self.assertIn(("missing-artifact", "repos/fixture-repo/scan/hypothesis-map.yml"), warnings)
            self.assertIn(("malformed-artifact", "repos/fixture-repo/evals/onboarding.yml"), warnings)
            self.assertEqual(
                tuple(suggestion.command for suggestion in report.validation_suggestions),
                ("harness validate fixture-repo",),
            )

    def test_report_output_is_sorted_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "Makefile").write_text("verify:\n\tpytest\ntest:\n\tpytest\n", encoding="utf-8")
            manifest = {
                "command_permissions": [
                    {"command": "make test", "reason": "duplicate source"},
                    {"command": "make lint", "reason": "lint source"},
                ]
            }
            evals = {
                "tasks": [
                    {"id": "b-task", "expected_files": ["b.txt"], "expected_commands": ["make verify"]},
                    {"id": "a-task", "expected_files": ["a.txt"], "expected_commands": ["make test"]},
                ]
            }
            hypothesis_map = {
                "hypotheses": [
                    {"name": "test_command_candidates", "value": ["make test"], "evidence_paths": [], "unknown": False}
                ]
            }
            self.write_artifacts(root, manifest=manifest, evals=evals, hypothesis_map=hypothesis_map)

            report = build_pr_risk_report(root, self.review(repo_path, "z.py", "a.md", "b.txt", "a.txt"))

            self.assertEqual(report.changed_files, ("a.md", "a.txt", "b.txt", "z.py"))
            self.assertEqual(tuple(item.path for item in report.file_risks), ("a.md", "a.txt", "b.txt", "z.py"))
            self.assertEqual(tuple(item.eval_id for item in report.eval_suggestions), ("a-task", "b-task"))
            self.assertEqual(
                tuple(suggestion.command for suggestion in report.validation_suggestions),
                ("harness validate fixture-repo", "make lint", "make test", "make verify"),
            )
