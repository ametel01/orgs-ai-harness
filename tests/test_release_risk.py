from __future__ import annotations

from orgs_ai_harness.pr_risk import RiskLevel
from orgs_ai_harness.release_readiness import ReleaseReadinessInput
from orgs_ai_harness.release_risk import build_release_risk_report

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class ReleaseRiskReportTests(unittest.TestCase):
    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        (repo_path / "package.json").write_text('{"scripts":{}}\n', encoding="utf-8")
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def readiness(self, repo_path: Path, *, version: str | None = "v1.2.3") -> ReleaseReadinessInput:
        return ReleaseReadinessInput(
            repo_id="fixture-repo",
            repo_path=repo_path.resolve(),
            status="artifact-only",
            version=version,
            base=None,
            head=None,
        )

    def write_release_artifacts(
        self,
        root: Path,
        *,
        status: str = "verified",
        verified: bool = True,
        manifest: object | None = None,
        evals: object | None = None,
        unknowns: object | None = None,
        eval_report: object | None = None,
        stale_approval: bool = False,
    ) -> None:
        artifact_root = root / "repos" / "fixture-repo"
        (artifact_root / "scripts").mkdir(parents=True, exist_ok=True)
        (artifact_root / "evals").mkdir(parents=True, exist_ok=True)
        (artifact_root / "scan").mkdir(parents=True, exist_ok=True)
        (artifact_root / "pack-report.md").write_text("# Pack Report\n", encoding="utf-8")
        (artifact_root / "scripts" / "manifest.yml").write_text(
            json.dumps(
                manifest
                if manifest is not None
                else {"command_permissions": [{"command": "make test", "reason": "Run local tests."}]}
            ),
            encoding="utf-8",
        )
        (artifact_root / "evals" / "onboarding.yml").write_text(
            json.dumps(
                evals
                if evals is not None
                else {
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
        (artifact_root / "scan" / "hypothesis-map.yml").write_text(
            json.dumps({"hypotheses": [{"name": "test_command_candidates", "value": ["make test"]}]}),
            encoding="utf-8",
        )
        (artifact_root / "unknowns.yml").write_text(
            json.dumps(unknowns if unknowns is not None else {"unknowns": []}),
            encoding="utf-8",
        )
        (artifact_root / "eval-report.yml").write_text(
            json.dumps(eval_report if eval_report is not None else {"repo_id": "fixture-repo", "status": status}),
            encoding="utf-8",
        )

        entries = load_repo_entries(root / "harness.yml")
        save_repo_entries(
            root / "harness.yml",
            (
                replace(
                    entries[0],
                    coverage_status=status,
                    pack_ref="repos/fixture-repo/approval.yml",
                ),
            ),
        )
        protected_hash = (
            "0" * 64 if stale_approval else hashlib.sha256((artifact_root / "pack-report.md").read_bytes()).hexdigest()
        )
        approval = {
            "schema_version": 1,
            "repo_id": "fixture-repo",
            "status": status,
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
            "verified": verified,
            "warnings": [] if verified else [{"code": "approved-unverified", "message": "Pack has not been verified."}],
            "verification": {"status": status, "report_path": "repos/fixture-repo/eval-report.yml"} if verified else {},
        }
        (artifact_root / "approval.yml").write_text(json.dumps(approval), encoding="utf-8")

    def test_low_risk_docs_only_release_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
            self.write_release_artifacts(root)

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=("CHANGELOG.md", "docs/release-notes.md"),
            )

            self.assertEqual(report.overall_risk, RiskLevel.LOW)
            self.assertEqual(tuple(item.category for item in report.items), ("release-context",))
            self.assertEqual(
                tuple(suggestion.command for suggestion in report.validation_suggestions),
                ("harness validate fixture-repo", "make test"),
            )
            self.assertEqual(report.warnings, ())

    def test_dependency_ci_and_migration_signals_are_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_release_artifacts(root)

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=(
                    ".github/workflows/release.yml",
                    "deploy/k8s/service.yaml",
                    "migrations/001_add_accounts.sql",
                    "package.json",
                ),
            )

            categories = {item.category: item.level for item in report.items}
            self.assertEqual(report.overall_risk, RiskLevel.HIGH)
            self.assertEqual(categories["ci"], RiskLevel.HIGH)
            self.assertEqual(categories["dependency"], RiskLevel.HIGH)
            self.assertEqual(categories["deployment"], RiskLevel.HIGH)
            self.assertEqual(categories["migration"], RiskLevel.HIGH)

    def test_missing_command_evidence_warns_without_inventing_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_release_artifacts(
                root,
                manifest={"command_permissions": []},
                evals={"tasks": [{"id": "docs", "expected_files": ["CHANGELOG.md"], "expected_commands": []}]},
            )
            (root / "repos" / "fixture-repo" / "scan" / "hypothesis-map.yml").unlink()

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=("CHANGELOG.md",),
            )

            self.assertEqual(
                tuple(suggestion.command for suggestion in report.validation_suggestions),
                ("harness validate fixture-repo",),
            )
            self.assertIn("no-command-evidence", {warning.code for warning in report.warnings})

    def test_unverified_pack_is_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_release_artifacts(
                root,
                status="approved-unverified",
                verified=False,
                eval_report={"repo_id": "fixture-repo", "status": "approved-unverified"},
            )

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=("CHANGELOG.md",),
            )

            categories = {item.category: item for item in report.items}
            self.assertEqual(report.overall_risk, RiskLevel.HIGH)
            self.assertEqual(categories["pack-verification"].level, RiskLevel.HIGH)
            self.assertEqual(categories["eval-evidence"].level, RiskLevel.HIGH)

    def test_eval_suggestion_ids_match_onboarding_eval_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_release_artifacts(
                root,
                evals={
                    "tasks": [
                        {
                            "id": "z-docs",
                            "expected_files": ["docs/release-notes.md"],
                            "expected_commands": [],
                        },
                        {
                            "id": "a-changelog",
                            "expected_files": ["CHANGELOG.md"],
                            "expected_commands": [],
                        },
                        {
                            "id": "not-touched",
                            "expected_files": ["src/app.py"],
                            "expected_commands": [],
                        },
                    ]
                },
            )

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=("docs/release-notes.md", "CHANGELOG.md"),
            )

            self.assertEqual(
                tuple(suggestion.eval_id for suggestion in report.eval_suggestions),
                ("a-changelog", "z-docs"),
            )

    def test_stale_approval_metadata_is_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_release_artifacts(root, stale_approval=True)

            report = build_release_risk_report(
                root,
                self.readiness(repo_path),
                changed_files=("CHANGELOG.md",),
            )

            approval_items = [item for item in report.items if item.category == "approval-metadata"]
            self.assertEqual(report.overall_risk, RiskLevel.HIGH)
            self.assertEqual(approval_items[0].level, RiskLevel.HIGH)
            self.assertIn("repos/fixture-repo/pack-report.md", approval_items[0].evidence)
