from __future__ import annotations

from orgs_ai_harness.release_context import ReleaseContextError, build_release_context

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class ReleaseContextTests(unittest.TestCase):
    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def write_complete_local_release_evidence(self, repo_path: Path) -> None:
        (repo_path / "CHANGELOG.md").write_text("# Changelog\n\n## 1.2.3\n", encoding="utf-8")
        (repo_path / "package.json").write_text(
            json.dumps({"name": "fixture-repo", "version": "1.2.3", "scripts": {"test": "pytest"}}),
            encoding="utf-8",
        )
        (repo_path / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        (repo_path / ".github" / "workflows").mkdir(parents=True)
        (repo_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
        (repo_path / "migrations").mkdir()
        (repo_path / "migrations" / "001_init.sql").write_text("select 1;\n", encoding="utf-8")
        (repo_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    def write_complete_artifacts(self, root: Path) -> None:
        artifact_root = root / "repos" / "fixture-repo"
        skill_root = artifact_root / "skills" / "release-workflow"
        skill_root.mkdir(parents=True)
        skill_root.joinpath("SKILL.md").write_text(
            "---\n"
            "name: release-workflow\n"
            "description: Use when checking release readiness evidence.\n"
            "---\n"
            "# release-workflow\n",
            encoding="utf-8",
        )
        artifact_root.joinpath("resolvers.yml").write_text(
            json.dumps(
                {
                    "resolvers": [
                        {
                            "intent": "release readiness",
                            "skill": "release-workflow",
                            "when": ["release", "changelog"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        artifact_root.joinpath("approval.yml").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repo_id": "fixture-repo",
                    "status": "verified",
                    "decision": "approved",
                    "pack_ref": "repos/fixture-repo/approval.yml",
                    "approved_artifacts": ["repos/fixture-repo/skills/release-workflow/SKILL.md"],
                    "excluded_artifacts": [],
                    "protected_artifacts": [],
                    "verified": True,
                }
            ),
            encoding="utf-8",
        )
        artifact_root.joinpath("eval-report.yml").write_text(
            json.dumps(
                {
                    "repo_id": "fixture-repo",
                    "status": "verified",
                    "skill_pack_pass_rate": 1.0,
                    "baseline_pass_rate": 0.5,
                }
            ),
            encoding="utf-8",
        )
        evals_root = artifact_root / "evals"
        evals_root.mkdir()
        evals_root.joinpath("onboarding.yml").write_text(
            json.dumps({"tasks": [{"id": "release-readiness", "prompt": "Check release readiness."}]}),
            encoding="utf-8",
        )
        artifact_root.joinpath("pack-report.md").write_text(
            "# Eval Pack Report: fixture-repo\n\n- Status: verified\n- Adapter: fixture\n- Skill-Pack Pass Rate: 1.0\n",
            encoding="utf-8",
        )
        artifact_root.joinpath("unknowns.yml").write_text(
            json.dumps(
                {
                    "unknowns": [
                        {
                            "id": "unk_release",
                            "question": "Is the release version final?",
                            "severity": "important",
                            "status": "closed",
                            "evidence": [{"path": "CHANGELOG.md"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        scan_root = artifact_root / "scan"
        scan_root.mkdir()
        scan_root.joinpath("scan-manifest.yml").write_text(
            json.dumps({"scanned_paths": [{"path": "CHANGELOG.md", "category": "release_notes"}]}),
            encoding="utf-8",
        )
        scan_root.joinpath("hypothesis-map.yml").write_text(
            json.dumps({"evidence_categories": {"release_notes": ["CHANGELOG.md"]}}),
            encoding="utf-8",
        )

    def test_builds_complete_release_context_from_artifacts_and_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            self.write_complete_artifacts(root)
            self.write_complete_local_release_evidence(repo_path)

            context = build_release_context(root, "fixture-repo")

            self.assertEqual(context.repo_id, "fixture-repo")
            self.assertEqual(context.local_repo.status, "available")
            self.assertTrue(context.lifecycle.supported)
            self.assertEqual(context.lifecycle.registry_status, "selected")
            self.assertEqual(context.lifecycle.approval_status, "verified")
            self.assertEqual(context.lifecycle.eval_status, "verified")
            self.assertEqual(context.lifecycle.eval_pass_rate, 1.0)
            self.assertEqual(context.lifecycle.eval_task_count, 1)
            self.assertEqual(context.pack_report.status if context.pack_report else None, "verified")
            self.assertEqual([unknown.id for unknown in context.unknowns], ["unk_release"])
            self.assertEqual(context.scan_evidence[0].paths, ("CHANGELOG.md",))
            self.assertEqual([skill.name for skill in context.generated_skills], ["release-workflow"])
            self.assertEqual([resolver.skill for resolver in context.generated_resolvers], ["release-workflow"])
            local_categories = {item.category for item in context.local_release_evidence}
            self.assertEqual(local_categories, {"changelog", "version", "lockfile", "ci", "migration", "deployment"})
            self.assertFalse(
                [item for item in context.missing_evidence if item.kind != "artifact"],
                context.missing_evidence,
            )

    def test_missing_artifacts_and_local_categories_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)

            context = build_release_context(root, "fixture-repo")

            statuses = {(artifact.name, artifact.status) for artifact in context.artifacts}
            self.assertIn(("approval", "missing"), statuses)
            self.assertIn(("eval-report", "missing"), statuses)
            self.assertIn(("evals", "missing"), statuses)
            self.assertIn(("pack-report", "missing"), statuses)
            self.assertIn(("unknowns", "missing"), statuses)
            self.assertIn(("scan-manifest", "missing"), statuses)
            self.assertIn(("hypothesis-map", "missing"), statuses)
            self.assertIn(("skills", "missing"), statuses)
            self.assertIn(("resolvers", "missing"), statuses)
            missing_kinds = {item.kind for item in context.missing_evidence}
            self.assertIn("artifact", missing_kinds)
            self.assertIn("changelog", missing_kinds)
            self.assertIn("version", missing_kinds)
            self.assertIn("ci", missing_kinds)

    def test_missing_changelog_and_version_evidence_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
            (repo_path / ".github" / "workflows").mkdir(parents=True)
            (repo_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
            (repo_path / "migrations").mkdir()
            (repo_path / "migrations" / "001_init.sql").write_text("select 1;\n", encoding="utf-8")
            (repo_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

            context = build_release_context(root, "fixture-repo")

            missing = {(item.kind, item.path) for item in context.missing_evidence}
            self.assertIn(("changelog", "CHANGELOG.md"), missing)
            self.assertIn(("version", "VERSION/package manifests"), missing)
            self.assertNotIn(("lockfile", "known lockfiles"), missing)
            package_manifest = next(item for item in context.local_release_evidence if item.path == "package.json")
            self.assertEqual(package_manifest.status, "present")
            self.assertEqual(package_manifest.detail, "version field missing")

    def test_malformed_artifacts_are_reported_without_failing_context_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)
            artifact_root = root / "repos" / "fixture-repo"
            artifact_root.mkdir(parents=True)
            artifact_root.joinpath("approval.yml").write_text("{not json", encoding="utf-8")
            artifact_root.joinpath("eval-report.yml").write_text("[1, 2, 3]", encoding="utf-8")
            evals_root = artifact_root / "evals"
            evals_root.mkdir()
            evals_root.joinpath("onboarding.yml").write_text("[1, 2, 3]", encoding="utf-8")
            artifact_root.joinpath("unknowns.yml").write_text("{not json", encoding="utf-8")
            artifact_root.joinpath("resolvers.yml").write_text("{not json", encoding="utf-8")
            artifact_root.joinpath("pack-report.md").write_text(
                "# Pack Report\n\n- Adapter: fixture\n",
                encoding="utf-8",
            )
            scan_root = artifact_root / "scan"
            scan_root.mkdir()
            scan_root.joinpath("scan-manifest.yml").write_text("{not json", encoding="utf-8")
            scan_root.joinpath("hypothesis-map.yml").write_text("[1, 2, 3]", encoding="utf-8")
            bad_skill_root = artifact_root / "skills" / "bad-skill"
            bad_skill_root.mkdir(parents=True)
            bad_skill_root.joinpath("SKILL.md").write_text("---\nname\n---\n", encoding="utf-8")

            context = build_release_context(root, "fixture-repo")

            statuses = {(artifact.name, artifact.status) for artifact in context.artifacts}
            self.assertIn(("approval", "malformed"), statuses)
            self.assertIn(("eval-report", "malformed"), statuses)
            self.assertIn(("evals", "malformed"), statuses)
            self.assertIn(("unknowns", "malformed"), statuses)
            self.assertIn(("resolvers", "malformed"), statuses)
            self.assertIn(("scan-manifest", "malformed"), statuses)
            self.assertIn(("hypothesis-map", "malformed"), statuses)
            self.assertEqual(context.pack_report.status if context.pack_report else None, None)
            self.assertEqual(context.generated_skills, ())
            self.assertTrue(
                any(item.reason == "pack report does not state status" for item in context.missing_evidence)
            )

    def test_inactive_and_external_repos_return_unsupported_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            root = init_org_pack(tmp_path, "acme")
            save_repo_entries(
                root / "harness.yml",
                (
                    RepoEntry(
                        id="inactive-repo",
                        name="inactive-repo",
                        owner=None,
                        purpose=None,
                        url=None,
                        default_branch="main",
                        local_path=os.path.relpath(repo_path.resolve(), root.resolve()),
                        coverage_status="deactivated",
                        active=False,
                        deactivation_reason="out of scope",
                        pack_ref=None,
                        external=False,
                    ),
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

            inactive = build_release_context(root, "inactive-repo")
            external = build_release_context(root, "external-repo")

            self.assertFalse(inactive.lifecycle.supported)
            self.assertEqual(inactive.lifecycle.reason, "repo is not active selected coverage")
            self.assertEqual(inactive.local_repo.status, "available")
            self.assertFalse(external.lifecycle.supported)
            self.assertEqual(external.lifecycle.reason, "repo is an external dependency reference")
            self.assertEqual(external.local_repo.status, "missing")
            self.assertTrue(any(item.kind == "changelog" and item.path == "-" for item in external.missing_evidence))

    def test_unknown_repo_still_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = init_org_pack(tmp_path, "acme")

            with self.assertRaisesRegex(ReleaseContextError, "repo id is not registered: missing-repo"):
                build_release_context(root, "missing-repo")
