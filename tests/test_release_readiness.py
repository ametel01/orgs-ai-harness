from __future__ import annotations

from orgs_ai_harness.release_readiness import ReleaseReadinessError, collect_release_readiness_input

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class ReleaseReadinessCommandTests(unittest.TestCase):
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

    def commit_fixture_repo(self, repo_path: Path, message: str) -> str:
        if not (repo_path / ".git").is_dir():
            subprocess.run(["git", "init"], cwd=repo_path, text=True, capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, text=True, capture_output=True, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Harness Test",
                "-c",
                "user.email=harness@example.test",
                "commit",
                "-m",
                message,
            ],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def test_collects_valid_local_release_readiness_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            base = self.commit_fixture_repo(repo_path, "initial")
            (repo_path / "README.md").write_text("# Fixture Repo\n\nRelease notes.\n", encoding="utf-8")
            head = self.commit_fixture_repo(repo_path, "release")

            readiness = collect_release_readiness_input(
                root,
                "fixture-repo",
                version=" v1.2.3 ",
                base=base,
                head=head,
            )

            self.assertEqual(readiness.repo_id, "fixture-repo")
            self.assertEqual(readiness.repo_path, repo_path.resolve())
            self.assertEqual(readiness.status, "artifact-only")
            self.assertEqual(readiness.version, "v1.2.3")
            self.assertEqual(readiness.base, base)
            self.assertEqual(readiness.head, head)

    def test_cli_release_readiness_prints_artifact_only_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_registered_repo(tmp_path)

            result = self.run_cli(
                tmp_path,
                "release",
                "readiness",
                "--repo-id",
                "fixture-repo",
                "--version",
                "v1.2.3",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Release readiness for repo fixture-repo", result.stdout)
            self.assertIn("status=artifact-only", result.stdout)
            self.assertIn("version=v1.2.3", result.stdout)
            self.assertIn("Repo path:", result.stdout)

    def test_release_readiness_rejects_unsupported_repo_states(self) -> None:
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

            cases = (
                ("missing-repo", "repo id is not registered"),
                ("external-repo", "external dependency reference"),
                ("inactive-repo", "not active selected coverage"),
                ("missing-path-repo", "has no local path"),
            )
            for repo_id, message in cases:
                with self.subTest(repo_id=repo_id):
                    with self.assertRaisesRegex(ReleaseReadinessError, message):
                        collect_release_readiness_input(root, repo_id)

    def test_cli_release_readiness_reports_bad_release_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_registered_repo(tmp_path)

            missing_head = self.run_cli(
                tmp_path,
                "release",
                "readiness",
                "--repo-id",
                "fixture-repo",
                "--base",
                "main",
            )
            bad_refs = self.run_cli(
                tmp_path,
                "release",
                "readiness",
                "--repo-id",
                "fixture-repo",
                "--base",
                "missing-base",
                "--head",
                "missing-head",
            )

            self.assertEqual(missing_head.returncode, 1)
            self.assertIn("requires both --base and --head", missing_head.stderr)
            self.assertEqual(bad_refs.returncode, 1)
            self.assertIn("cannot resolve base ref", bad_refs.stderr)
