from __future__ import annotations

from typing import cast

from orgs_ai_harness.dependency_campaign import collect_dependency_campaign_input
from orgs_ai_harness.dependency_context import build_dependency_inventory

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class DependencyContextTests(unittest.TestCase):
    def prepare_registered_repo(self, tmp_path: Path, name: str = "fixture-repo") -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path, name)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, name)
        return root, repo_path

    def write_generated_pack_evidence(self, root: Path, repo_id: str, *, verified: bool = True) -> None:
        artifact_root = root / "repos" / repo_id
        (artifact_root / "skills" / "dependency-workflow").mkdir(parents=True)
        (artifact_root / "skills" / "dependency-workflow" / "SKILL.md").write_text(
            "---\nname: dependency-workflow\ndescription: Use when dependency files change.\n---\n",
            encoding="utf-8",
        )
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "resolvers.yml").write_text(json.dumps({"resolvers": []}), encoding="utf-8")
        (artifact_root / "scan").mkdir(exist_ok=True)
        (artifact_root / "scan" / "scan-manifest.yml").write_text(
            json.dumps({"scanned_paths": [{"path": "package.json", "category": "dependency_manifest"}]}),
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

    def test_builds_complete_dependency_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "package.json").write_text(
                json.dumps(
                    {
                        "name": "fixture-repo",
                        "dependencies": {"fastapi": "^1.0.0"},
                        "devDependencies": {"vitest": "^3.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (repo_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
            (repo_path / "pyproject.toml").write_text(
                '[project]\nname = "fixture-repo"\ndependencies = ["requests>=2"]\n',
                encoding="utf-8",
            )
            (repo_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            self.write_generated_pack_evidence(root, "fixture-repo")

            campaign = collect_dependency_campaign_input(root, name="spring-upgrades", package_filters=("fastapi",))
            inventory = build_dependency_inventory(root, campaign)

            self.assertEqual(inventory.campaign_name, "spring-upgrades")
            self.assertEqual(inventory.package_filters, ("fastapi",))
            self.assertEqual([repo.repo_id for repo in inventory.repos], ["fixture-repo"])
            repo_inventory = inventory.repos[0]
            self.assertEqual(repo_inventory.lifecycle_status, "verified")
            self.assertEqual(
                [(item.path, item.status) for item in repo_inventory.dependency_files],
                [("package.json", "parsed"), ("pyproject.toml", "parsed")],
            )
            package_json = next(item for item in repo_inventory.dependency_files if item.path == "package.json")
            self.assertEqual(package_json.package_name, "fixture-repo")
            self.assertEqual(package_json.dependencies, ("fastapi",))
            self.assertEqual(package_json.dev_dependencies, ("vitest",))
            self.assertEqual([item.path for item in repo_inventory.lockfiles], ["package-lock.json", "uv.lock"])
            self.assertEqual(repo_inventory.generated_pack.approval_status, "verified")
            self.assertEqual(repo_inventory.generated_pack.eval_task_count, 1)
            self.assertEqual(repo_inventory.warnings, ())

    def test_inventory_reports_missing_local_paths_and_unsupported_repos_as_skipped(self) -> None:
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
                    RepoEntry(
                        id="missing-path-repo",
                        name="missing-path-repo",
                        owner=None,
                        purpose=None,
                        url=None,
                        default_branch="main",
                        local_path="../missing",
                        coverage_status="selected",
                        active=True,
                        deactivation_reason=None,
                        pack_ref=None,
                        external=False,
                    ),
                ),
            )

            campaign = collect_dependency_campaign_input(root, name="spring-upgrades")
            inventory = build_dependency_inventory(root, campaign)

            self.assertEqual([repo.repo_id for repo in inventory.repos], ["fixture-repo"])
            self.assertEqual(
                [(repo.repo_id, repo.reason) for repo in inventory.skipped_repos],
                [
                    ("external-repo", "repo is an external dependency reference"),
                    ("missing-path-repo", "repo path does not exist"),
                ],
            )

    def test_inventory_represents_malformed_manifests_and_missing_lockfiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "package.json").write_text("{not-json\n", encoding="utf-8")
            self.write_generated_pack_evidence(root, "fixture-repo", verified=False)

            campaign = collect_dependency_campaign_input(root, name="spring-upgrades")
            inventory = build_dependency_inventory(root, campaign)
            repo_inventory = inventory.repos[0]

            self.assertEqual(
                [(item.path, item.status) for item in repo_inventory.dependency_files], [("package.json", "malformed")]
            )
            self.assertEqual(repo_inventory.lockfiles, ())
            self.assertIn("malformed-manifest", {warning.code for warning in repo_inventory.warnings})
            missing = {(item.kind, item.path) for item in repo_inventory.missing_evidence}
            self.assertIn(("generated-pack", "coverage_status"), missing)
            self.assertIn(("lockfile", "node"), missing)

    def test_inventory_orders_repos_and_dependency_files_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, alpha_path = self.prepare_registered_repo(tmp_path, "zeta-repo")
            beta_path = create_basic_fixture_repo(tmp_path, "alpha-repo")
            add_repo(root, tmp_path, "alpha-repo")
            for repo_id, repo_path in (("zeta-repo", alpha_path), ("alpha-repo", beta_path)):
                (repo_path / "services").mkdir()
                (repo_path / "services" / "package.json").write_text(
                    json.dumps({"dependencies": {"zod": "^3.0.0"}}),
                    encoding="utf-8",
                )
                self.write_generated_pack_evidence(root, repo_id)

            campaign = collect_dependency_campaign_input(root, name="spring-upgrades")
            first = build_dependency_inventory(root, campaign)
            second = build_dependency_inventory(root, campaign)

            self.assertEqual(first, second)
            self.assertEqual([repo.repo_id for repo in first.repos], ["alpha-repo", "zeta-repo"])
            for repo_inventory in first.repos:
                files = cast(list[str], [item.path for item in repo_inventory.dependency_files])
                self.assertEqual(files, sorted(files))
