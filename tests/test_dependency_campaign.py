from __future__ import annotations

from orgs_ai_harness.dependency_campaign import DependencyCampaignError, collect_dependency_campaign_input

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class DependencyCampaignCommandTests(unittest.TestCase):
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

    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def test_collects_valid_local_dependency_campaign_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)

            campaign = collect_dependency_campaign_input(
                root,
                name=" spring-upgrades ",
                package_filters=(" fastapi ", "uvicorn", "fastapi"),
            )

            self.assertEqual(campaign.name, "spring-upgrades")
            self.assertEqual(campaign.status, "artifact-only")
            self.assertEqual(campaign.package_filters, ("fastapi", "uvicorn"))
            self.assertEqual([repo.repo_id for repo in campaign.repos], ["fixture-repo"])
            self.assertEqual(campaign.repos[0].repo_path, repo_path.resolve())
            self.assertEqual(campaign.skipped_repos, ())

    def test_cli_dependency_campaign_prints_artifact_only_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_registered_repo(tmp_path)

            result = self.run_cli(
                tmp_path,
                "dependency",
                "campaign",
                "--name",
                "spring-upgrades",
                "--package",
                "fastapi",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Dependency campaign spring-upgrades", result.stdout)
            self.assertIn("status=artifact-only", result.stdout)
            self.assertIn("eligible_repos=1", result.stdout)
            self.assertIn("packages=1", result.stdout)
            self.assertIn("fixture-repo", result.stdout)

    def test_dependency_campaign_rejects_missing_org_pack_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(Path(tmp), "dependency", "campaign", "--name", "spring-upgrades")

            self.assertEqual(result.returncode, 1)
            self.assertIn("no org skill pack found", result.stderr)

    def test_dependency_campaign_rejects_empty_repo_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")

            result = self.run_cli(tmp_path, "dependency", "campaign", "--name", "spring-upgrades")

            self.assertEqual(result.returncode, 1)
            self.assertIn("requires at least one registered repository", result.stderr)

    def test_dependency_campaign_rejects_invalid_local_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)

            with self.assertRaisesRegex(DependencyCampaignError, "name cannot be empty"):
                collect_dependency_campaign_input(root, name=" ")
            with self.assertRaisesRegex(DependencyCampaignError, "package filters cannot be empty"):
                collect_dependency_campaign_input(root, name="spring-upgrades", package_filters=("fastapi", " "))

    def test_dependency_campaign_reports_unsupported_repo_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)
            save_repo_entries(
                root / "harness.yml",
                (
                    RepoEntry(
                        id="external-repo",
                        name="external-repo",
                        owner=None,
                        purpose=None,
                        url="https://example.test/external-repo",
                        default_branch="main",
                        local_path=None,
                        coverage_status="external",
                        active=False,
                        deactivation_reason=None,
                        pack_ref=None,
                        external=True,
                    ),
                    RepoEntry(
                        id="inactive-repo",
                        name="inactive-repo",
                        owner=None,
                        purpose=None,
                        url=None,
                        default_branch="main",
                        local_path="../fixture-repo",
                        coverage_status="deactivated",
                        active=False,
                        deactivation_reason="out of scope",
                        pack_ref=None,
                        external=False,
                    ),
                    RepoEntry(
                        id="missing-path-repo",
                        name="missing-path-repo",
                        owner=None,
                        purpose=None,
                        url=None,
                        default_branch="main",
                        local_path=None,
                        coverage_status="selected",
                        active=True,
                        deactivation_reason=None,
                        pack_ref=None,
                        external=False,
                    ),
                ),
            )

            with self.assertRaisesRegex(DependencyCampaignError, "no eligible active local repositories"):
                collect_dependency_campaign_input(root, name="spring-upgrades")

    def test_dependency_campaign_prints_skipped_repos_with_valid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)
            entries = load_repo_entries(root / "harness.yml")
            save_repo_entries(
                root / "harness.yml",
                (
                    *entries,
                    RepoEntry(
                        id="external-repo",
                        name="external-repo",
                        owner=None,
                        purpose=None,
                        url="https://example.test/external-repo",
                        default_branch="main",
                        local_path=None,
                        coverage_status="external",
                        active=False,
                        deactivation_reason=None,
                        pack_ref=None,
                        external=True,
                    ),
                ),
            )

            result = self.run_cli(tmp_path, "dependency", "campaign", "--name", "spring-upgrades")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("eligible_repos=1", result.stdout)
            self.assertIn("skipped_repos=1", result.stdout)
            self.assertIn("external dependency reference", result.stdout)
