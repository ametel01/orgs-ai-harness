from __future__ import annotations

from orgs_ai_harness.dependency_campaign import collect_dependency_campaign_input
from orgs_ai_harness.dependency_context import build_dependency_inventory
from orgs_ai_harness.dependency_risk import build_dependency_risk_report

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class DependencyRiskTests(unittest.TestCase):
    def prepare_repo(self, tmp_path: Path, name: str) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path, name)
        root = (
            init_org_pack(tmp_path, "acme")
            if not (tmp_path / "org-agent-skills").exists()
            else tmp_path / "org-agent-skills"
        )
        add_repo(root, tmp_path, name)
        return root, repo_path

    def write_pack_evidence(
        self,
        root: Path,
        repo_id: str,
        *,
        verified: bool = True,
        eval_expected_files: tuple[str, ...] = ("package.json",),
        command: str | None = "make test",
    ) -> None:
        artifact_root = root / "repos" / repo_id
        (artifact_root / "skills" / "dependency-workflow").mkdir(parents=True)
        (artifact_root / "skills" / "dependency-workflow" / "SKILL.md").write_text(
            "---\nname: dependency-workflow\ndescription: Use when dependency files change.\n---\n",
            encoding="utf-8",
        )
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "resolvers.yml").write_text(json.dumps({"resolvers": []}), encoding="utf-8")
        (artifact_root / "scan").mkdir(exist_ok=True)
        (artifact_root / "scan" / "hypothesis-map.yml").write_text(json.dumps({"hypotheses": []}), encoding="utf-8")
        if command is not None:
            (artifact_root / "scripts").mkdir(exist_ok=True)
            (artifact_root / "scripts" / "manifest.yml").write_text(
                json.dumps({"command_permissions": [{"command": command, "reason": "Run local checks."}]}),
                encoding="utf-8",
            )
        (artifact_root / "evals").mkdir()
        (artifact_root / "evals" / "onboarding.yml").write_text(
            json.dumps({"tasks": [{"id": "dependency-files", "expected_files": list(eval_expected_files)}]}),
            encoding="utf-8",
        )
        (artifact_root / "approval.yml").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repo_id": repo_id,
                    "status": "verified" if verified else "approved-unverified",
                    "decision": "approved",
                    "verified": verified,
                }
            ),
            encoding="utf-8",
        )
        entries = load_repo_entries(root / "harness.yml")
        save_repo_entries(
            root / "harness.yml",
            tuple(
                replace(
                    entry,
                    coverage_status="verified" if verified else "approved-unverified",
                    pack_ref=f"repos/{repo_id}/approval.yml",
                )
                if entry.id == repo_id
                else entry
                for entry in entries
            ),
        )

    def risk_report(self, root: Path):
        campaign = collect_dependency_campaign_input(root, name="spring-upgrades")
        inventory = build_dependency_inventory(root, campaign)
        return build_dependency_risk_report(root, inventory)

    def test_low_risk_inventory_suggests_known_commands_and_evals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_repo(tmp_path, "fixture-repo")
            (repo_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
            (repo_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
            self.write_pack_evidence(root, "fixture-repo")

            report = self.risk_report(root)

            self.assertEqual(report.overall_risk, "low")
            repo_report = report.repos[0]
            self.assertEqual(repo_report.overall_risk, "low")
            self.assertIn("make test", [item.command for item in repo_report.validation_suggestions])
            self.assertEqual([item.eval_id for item in repo_report.eval_suggestions], ["dependency-files"])
            self.assertEqual(report.rollout_plan[0].repo_id, "fixture-repo")

    def test_high_risk_multi_repo_dependency_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, low_repo = self.prepare_repo(tmp_path, "low-repo")
            high_repo = create_basic_fixture_repo(tmp_path, "high-repo")
            add_repo(root, tmp_path, "high-repo")
            (low_repo / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
            self.write_pack_evidence(root, "low-repo")
            (high_repo / "package.json").write_text("{not-json\n", encoding="utf-8")
            (high_repo / "migrations").mkdir()
            (high_repo / "migrations" / "001.sql").write_text("select 1;\n", encoding="utf-8")
            (high_repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            self.write_pack_evidence(root, "high-repo", verified=False, eval_expected_files=("README.md",))

            report = self.risk_report(root)

            self.assertEqual(report.overall_risk, "high")
            by_repo = {repo.repo_id: repo for repo in report.repos}
            self.assertEqual(by_repo["high-repo"].overall_risk, "high")
            categories = {item.category for item in by_repo["high-repo"].items}
            self.assertIn("dependency-manifest", categories)
            self.assertIn("migration-coupling", categories)
            self.assertIn("deployment-coupling", categories)
            self.assertIn("pack-verification", categories)
            self.assertEqual([step.repo_id for step in report.rollout_plan], ["low-repo", "high-repo"])

    def test_missing_command_and_eval_evidence_are_conservative_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_repo(tmp_path, "fixture-repo")
            (repo_path / "package.json").write_text(
                json.dumps({"dependencies": {"fastapi": "1.0.0"}}), encoding="utf-8"
            )
            self.write_pack_evidence(root, "fixture-repo", eval_expected_files=("README.md",), command=None)

            report = self.risk_report(root)
            repo_report = report.repos[0]

            self.assertEqual(repo_report.overall_risk, "medium")
            self.assertIn("no-command-evidence", {warning.code for warning in repo_report.warnings})
            self.assertEqual(repo_report.eval_suggestions, ())
            self.assertIn("eval-evidence", {item.category for item in repo_report.items})
            self.assertIn("validation-evidence", {item.category for item in repo_report.items})

    def test_rollout_ordering_is_deterministic_by_risk_then_repo_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, zeta_repo = self.prepare_repo(tmp_path, "zeta-repo")
            alpha_repo = create_basic_fixture_repo(tmp_path, "alpha-repo")
            add_repo(root, tmp_path, "alpha-repo")
            (zeta_repo / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
            (alpha_repo / "package.json").write_text("{not-json\n", encoding="utf-8")
            self.write_pack_evidence(root, "zeta-repo")
            self.write_pack_evidence(root, "alpha-repo", verified=False, eval_expected_files=("README.md",))

            first = self.risk_report(root)
            second = self.risk_report(root)

            self.assertEqual(first, second)
            self.assertEqual([step.position for step in first.rollout_plan], [1, 2])
            self.assertEqual([step.repo_id for step in first.rollout_plan], ["zeta-repo", "alpha-repo"])
