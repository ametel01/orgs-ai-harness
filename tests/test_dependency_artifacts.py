from __future__ import annotations

from typing import cast

from orgs_ai_harness.dependency_artifacts import (
    DEPENDENCY_CAMPAIGN_SCHEMA_VERSION,
    build_dependency_campaign_artifacts,
)
from orgs_ai_harness.dependency_campaign import collect_dependency_campaign_input
from orgs_ai_harness.dependency_context import build_dependency_inventory
from orgs_ai_harness.dependency_risk import build_dependency_risk_report

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class DependencyArtifactTests(unittest.TestCase):
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

    def prepare_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        (repo_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
        (repo_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        self.write_pack_evidence(root, "fixture-repo")
        return root, repo_path

    def write_pack_evidence(self, root: Path, repo_id: str) -> None:
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
        (artifact_root / "scripts").mkdir(exist_ok=True)
        (artifact_root / "scripts" / "manifest.yml").write_text(
            json.dumps({"command_permissions": [{"command": "make test", "reason": "Run local checks."}]}),
            encoding="utf-8",
        )
        (artifact_root / "evals").mkdir()
        (artifact_root / "evals" / "onboarding.yml").write_text(
            json.dumps({"tasks": [{"id": "dependency-files", "expected_files": ["package.json"]}]}),
            encoding="utf-8",
        )
        (artifact_root / "approval.yml").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repo_id": repo_id,
                    "status": "verified",
                    "decision": "approved",
                    "verified": True,
                }
            ),
            encoding="utf-8",
        )
        entries = load_repo_entries(root / "harness.yml")
        save_repo_entries(
            root / "harness.yml",
            tuple(
                replace(entry, coverage_status="verified", pack_ref=f"repos/{repo_id}/approval.yml")
                if entry.id == repo_id
                else entry
                for entry in entries
            ),
        )

    def build_artifacts(self, root: Path):
        campaign = collect_dependency_campaign_input(root, name="spring-upgrades", package_filters=("fastapi",))
        inventory = build_dependency_inventory(root, campaign)
        risk = build_dependency_risk_report(root, inventory)
        return build_dependency_campaign_artifacts(inventory, risk)

    def test_builds_stable_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_repo(tmp_path)

            artifacts = self.build_artifacts(root)
            payload = artifacts.json_payload

            self.assertEqual(payload["schema_version"], DEPENDENCY_CAMPAIGN_SCHEMA_VERSION)
            self.assertEqual(payload["status"], "artifact-only")
            self.assertEqual(cast(dict[str, object], payload["campaign"])["name"], "spring-upgrades")
            self.assertEqual(cast(dict[str, object], payload["summary"])["overall_risk"], "low")
            repo = cast(list[dict[str, object]], payload["repos"])[0]
            self.assertEqual(repo["repo_id"], "fixture-repo")
            self.assertEqual(cast(dict[str, object], repo["risk"])["overall"], "low")
            self.assertEqual(cast(list[dict[str, object]], repo["dependency_files"])[0]["path"], "package.json")
            self.assertIn("## Rollout Plan", artifacts.markdown)
            self.assertIn("## Suggested Checks", artifacts.markdown)
            self.assertIn("`make test`", artifacts.markdown)

    def test_cli_writes_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.prepare_repo(tmp_path)
            json_path = tmp_path / ".agent-harness" / "dependency-campaign" / "campaign.json"
            markdown_path = tmp_path / ".agent-harness" / "dependency-campaign" / "campaign.md"

            result = self.run_cli(
                tmp_path,
                "dependency",
                "campaign",
                "--name",
                "spring-upgrades",
                "--json-path",
                str(json_path),
                "--markdown-path",
                str(markdown_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("overall_risk=low", result.stdout)
            self.assertIn("JSON artifact:", result.stdout)
            self.assertIn("Markdown artifact:", result.stdout)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], DEPENDENCY_CAMPAIGN_SCHEMA_VERSION)
            self.assertEqual(payload["rollout_plan"][0]["repo_id"], "fixture-repo")
            self.assertIn("# Dependency Campaign Artifact: spring-upgrades", markdown_path.read_text(encoding="utf-8"))

    def test_empty_sections_and_skipped_repos_are_explicit_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_repo(tmp_path)
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

            first = self.build_artifacts(root)
            second = self.build_artifacts(root)

            self.assertEqual(first.json_payload, second.json_payload)
            self.assertEqual(first.markdown, second.markdown)
            skipped_repos = cast(list[dict[str, object]], first.json_payload["skipped_repos"])
            self.assertEqual(skipped_repos[0]["repo_id"], "external-repo")
            self.assertIn("## Missing Evidence\n\n- None.", first.markdown)
            self.assertIn("## Skipped Repos", first.markdown)
