from __future__ import annotations

from typing import cast

from orgs_ai_harness.pr_artifacts import PR_REVIEW_SCHEMA_VERSION, build_pr_review_artifacts
from orgs_ai_harness.pr_review import ReviewChangedFiles

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class PrReviewArtifactTests(unittest.TestCase):
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

    def prepare_repo_with_artifacts(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        (repo_path / "src").mkdir()
        (repo_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (repo_path / "Makefile").write_text("test:\n\tpytest\nverify:\n\tpytest\n", encoding="utf-8")
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")

        artifact_root = root / "repos" / "fixture-repo"
        skill_root = artifact_root / "skills" / "python-workflow"
        skill_root.mkdir(parents=True)
        skill_root.joinpath("SKILL.md").write_text(
            "---\n"
            "name: python-workflow\n"
            "description: Use when editing Python source files.\n"
            "---\n"
            "# python-workflow\n\n"
            "Use when editing `src/app.py`.\n"
            "- The task mentions: src, python, tests\n",
            encoding="utf-8",
        )
        artifact_root.joinpath("resolvers.yml").write_text(
            json.dumps(
                {"resolvers": [{"intent": "python changes", "skill": "python-workflow", "when": ["src/app.py"]}]}
            ),
            encoding="utf-8",
        )
        (artifact_root / "scripts").mkdir()
        artifact_root.joinpath("scripts", "manifest.yml").write_text(
            json.dumps({"command_permissions": [{"command": "make test", "reason": "Run local tests."}]}),
            encoding="utf-8",
        )
        (artifact_root / "evals").mkdir()
        artifact_root.joinpath("evals", "onboarding.yml").write_text(
            json.dumps(
                {
                    "repo_id": "fixture-repo",
                    "tasks": [
                        {
                            "id": "source-review",
                            "expected_files": ["src/app.py"],
                            "expected_commands": ["make test"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (artifact_root / "scan").mkdir()
        artifact_root.joinpath("scan", "scan-manifest.yml").write_text(
            json.dumps({"scanned_paths": [{"path": "src/app.py", "category": "source"}]}),
            encoding="utf-8",
        )
        artifact_root.joinpath("scan", "hypothesis-map.yml").write_text(
            json.dumps({"evidence_categories": {"source": ["src/app.py"]}, "hypotheses": []}),
            encoding="utf-8",
        )
        artifact_root.joinpath("unknowns.yml").write_text(json.dumps({"unknowns": []}), encoding="utf-8")
        return root, repo_path

    def review(self, repo_path: Path, *changed_files: str) -> ReviewChangedFiles:
        return ReviewChangedFiles(
            repo_id="fixture-repo",
            repo_path=repo_path,
            changed_files=tuple(changed_files),
            source="explicit",
        )

    def test_builds_stable_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_repo_with_artifacts(tmp_path)

            artifacts = build_pr_review_artifacts(root, self.review(repo_path, "src/app.py"))
            payload = artifacts.json_payload

            self.assertEqual(payload["schema_version"], PR_REVIEW_SCHEMA_VERSION)
            self.assertEqual(payload["status"], "artifact-only")
            self.assertEqual(payload["repo_id"], "fixture-repo")
            self.assertEqual(payload["changed_files"], ["src/app.py"])
            risk = cast(dict[str, object], payload["risk"])
            self.assertIsInstance(risk, dict)
            self.assertEqual(risk["overall"], "medium")
            suggested_evals = cast(list[dict[str, object]], risk["suggested_evals"])
            self.assertIn(
                {
                    "eval_id": "source-review",
                    "expected_files": ["src/app.py"],
                    "matched_files": ["src/app.py"],
                    "source": "repos/fixture-repo/evals/onboarding.yml",
                },
                suggested_evals,
            )
            suggested_commands = cast(list[dict[str, object]], risk["suggested_commands"])
            self.assertEqual(
                [item["command"] for item in suggested_commands],
                ["harness validate fixture-repo", "make test", "make verify"],
            )
            context = cast(dict[str, object], payload["context"])
            self.assertIsInstance(context, dict)
            matched_skills = cast(list[dict[str, object]], context["matched_skills"])
            self.assertEqual(matched_skills[0]["name"], "python-workflow")
            self.assertIn("## Suggested Checks", artifacts.markdown)
            self.assertIn("`make test`", artifacts.markdown)
            self.assertIn("## Matched Skills", artifacts.markdown)

    def test_cli_writes_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_repo_with_artifacts(tmp_path)
            json_path = tmp_path / ".agent-harness" / "pr-review" / "review.json"
            markdown_path = tmp_path / ".agent-harness" / "pr-review" / "review.md"

            result = self.run_cli(
                tmp_path,
                "review",
                "changed-files",
                "--repo-id",
                "fixture-repo",
                "--files",
                "src/app.py",
                "--json-path",
                str(json_path),
                "--markdown-path",
                str(markdown_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("JSON artifact:", result.stdout)
            self.assertIn("Markdown artifact:", result.stdout)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], PR_REVIEW_SCHEMA_VERSION)
            self.assertEqual(payload["risk"]["suggested_evals"][0]["eval_id"], "source-review")
            self.assertIn("# PR Review Artifact: fixture-repo", markdown_path.read_text(encoding="utf-8"))

    def test_empty_sections_are_explicit_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "fixture-repo")

            first = build_pr_review_artifacts(root, self.review(repo_path, "README.md"))
            second = build_pr_review_artifacts(root, self.review(repo_path, "README.md"))

            self.assertEqual(first.json_payload, second.json_payload)
            self.assertEqual(first.markdown, second.markdown)
            risk = cast(dict[str, object], first.json_payload["risk"])
            self.assertEqual(risk["suggested_evals"], [])
            self.assertIn("## Suggested Evals\n\n- None.", first.markdown)
            self.assertIn("missing-artifact", json.dumps(first.json_payload, sort_keys=True))
