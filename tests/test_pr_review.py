from __future__ import annotations

from orgs_ai_harness.pr_review import ReviewError, collect_changed_files

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class PrReviewChangedFilesTests(unittest.TestCase):
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

    def test_collect_changed_files_normalizes_explicit_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)

            result = collect_changed_files(
                root,
                "fixture-repo",
                files=("src/app.py", " README.md ", "src/app.py"),
            )

            self.assertEqual(result.repo_id, "fixture-repo")
            self.assertEqual(result.source, "explicit")
            self.assertEqual(result.changed_files, ("README.md", "src/app.py"))

    def test_cli_review_changed_files_accepts_files_from(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_registered_repo(tmp_path)
            files_from = tmp_path / "changed.txt"
            files_from.write_text("tests/test_app.py\n\nsrc/app.py\n", encoding="utf-8")

            result = self.run_cli(
                tmp_path,
                "review",
                "changed-files",
                "--repo-id",
                "fixture-repo",
                "--files-from",
                str(files_from),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Review changed files for repo fixture-repo", result.stdout)
            self.assertIn("src/app.py", result.stdout)
            self.assertIn("tests/test_app.py", result.stdout)

    def test_cli_review_changed_files_resolves_local_git_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_registered_repo(tmp_path)
            repo_path = tmp_path / "fixture-repo"
            base = self.commit_fixture_repo(repo_path, "initial")
            (repo_path / "README.md").write_text("# Fixture Repo\n\nChanged notes.\n", encoding="utf-8")
            (repo_path / "src").mkdir()
            (repo_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            head = self.commit_fixture_repo(repo_path, "change")

            result = self.run_cli(
                tmp_path,
                "review",
                "changed-files",
                "--repo-id",
                "fixture-repo",
                "--base",
                base,
                "--head",
                head,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("source=git-diff", result.stdout)
            self.assertIn("README.md", result.stdout)
            self.assertIn("src/app.py", result.stdout)

    def test_review_changed_files_rejects_invalid_or_empty_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)

            cases = ((), ("../outside.py",), ("/tmp/outside.py",), (".git/config",))
            for files in cases:
                with self.subTest(files=files):
                    with self.assertRaises(ReviewError):
                        collect_changed_files(root, "fixture-repo", files=files)

    def test_cli_review_changed_files_reports_missing_repo_and_bad_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _root, repo_path = self.prepare_registered_repo(tmp_path)
            self.commit_fixture_repo(repo_path, "initial")

            missing_repo = self.run_cli(
                tmp_path,
                "review",
                "changed-files",
                "--repo-id",
                "missing-repo",
                "--files",
                "README.md",
            )
            bad_refs = self.run_cli(
                tmp_path,
                "review",
                "changed-files",
                "--repo-id",
                "fixture-repo",
                "--base",
                "missing-base",
                "--head",
                "missing-head",
            )

            self.assertEqual(missing_repo.returncode, 1)
            self.assertIn("repo id is not registered: missing-repo", missing_repo.stderr)
            self.assertEqual(bad_refs.returncode, 1)
            self.assertIn("cannot resolve changed files", bad_refs.stderr)
