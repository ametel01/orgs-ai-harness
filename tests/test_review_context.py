from __future__ import annotations

from orgs_ai_harness.pr_review import ReviewError, collect_changed_files
from orgs_ai_harness.review_context import ReviewContextError, build_review_context

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


class ReviewContextTests(unittest.TestCase):
    def prepare_registered_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo_path = create_basic_fixture_repo(tmp_path)
        root = init_org_pack(tmp_path, "acme")
        add_repo(root, tmp_path, "fixture-repo")
        return root, repo_path

    def write_artifacts(
        self,
        root: Path,
        *,
        skill_name: str = "frontend-workflow",
        description: str = "Use when editing frontend components and UI tests.",
        skill_body: str = "Use when editing `src/ui/` or `components/Button.tsx`.\n",
        resolver_when: tuple[str, ...] = ("frontend", "components", "src/ui"),
        evidence_paths: tuple[str, ...] = ("src/ui/button.tsx",),
    ) -> None:
        artifact_root = root / "repos" / "fixture-repo"
        skill_root = artifact_root / "skills" / skill_name
        skill_root.mkdir(parents=True)
        skill_root.joinpath("SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: {description}\n---\n# {skill_name}\n\n{skill_body}",
            encoding="utf-8",
        )
        artifact_root.joinpath("resolvers.yml").write_text(
            json.dumps(
                {
                    "resolvers": [
                        {
                            "intent": f"use {skill_name}",
                            "skill": skill_name,
                            "when": list(resolver_when),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        scan_root = artifact_root / "scan"
        scan_root.mkdir()
        scan_root.joinpath("scan-manifest.yml").write_text(
            json.dumps(
                {
                    "repo_id": "fixture-repo",
                    "scanned_paths": [{"path": path, "category": "source", "bytes": 12} for path in evidence_paths],
                    "skipped_paths": [],
                }
            ),
            encoding="utf-8",
        )
        scan_root.joinpath("hypothesis-map.yml").write_text(
            json.dumps(
                {
                    "repo_id": "fixture-repo",
                    "evidence_categories": {"source": list(evidence_paths)},
                    "hypotheses": [],
                }
            ),
            encoding="utf-8",
        )
        artifact_root.joinpath("unknowns.yml").write_text(
            json.dumps(
                {
                    "unknowns": [
                        {
                            "id": "unk_001",
                            "question": "Which frontend check should run?",
                            "severity": "important",
                            "status": "open",
                            "evidence": [{"path": "package.json", "note": "scripts exist"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_context_matches_changed_file_to_skill_resolver_and_scan_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "src" / "ui").mkdir(parents=True)
            (repo_path / "src" / "ui" / "button.tsx").write_text("export {}\n", encoding="utf-8")
            self.write_artifacts(root)

            context = build_review_context(
                root,
                "fixture-repo",
                ("src/ui/button.tsx", " src/ui/button.tsx "),
            )

            self.assertEqual([path.normalized_path for path in context.changed_paths], ["src/ui/button.tsx"])
            self.assertEqual([match.name for match in context.matched_skills], ["frontend-workflow"])
            match = context.matched_skills[0]
            self.assertEqual(match.path, "repos/fixture-repo/skills/frontend-workflow/SKILL.md")
            self.assertEqual(match.description, "Use when editing frontend components and UI tests.")
            self.assertIn("frontend", match.triggers)
            self.assertEqual(match.matched_paths, ("src/ui/button.tsx",))
            self.assertEqual([evidence.category for evidence in context.evidence_matches], ["source"])
            self.assertEqual(context.unknowns[0].id, "unk_001")

    def test_context_reports_no_match_as_missing_changed_path_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "src").mkdir()
            (repo_path / "src" / "payments.py").write_text("PAYMENT = True\n", encoding="utf-8")
            self.write_artifacts(
                root,
                skill_name="docs-workflow",
                description="Use when editing README documentation.",
                skill_body="Use when editing `README.md`.\n",
                resolver_when=("readme", "documentation"),
                evidence_paths=("README.md",),
            )

            context = build_review_context(root, "fixture-repo", ("src/payments.py",))

            self.assertEqual(context.matched_skills, ())
            self.assertEqual(context.evidence_matches, ())
            self.assertIn(
                ("changed_path", "src/payments.py", "no matching skill, resolver, or scan evidence"),
                {(item.kind, item.path, item.reason) for item in context.missing_coverage},
            )

    def test_context_tolerates_missing_and_malformed_artifacts_but_rejects_unknown_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)
            artifact_root = root / "repos" / "fixture-repo"
            artifact_root.mkdir(parents=True)
            artifact_root.joinpath("resolvers.yml").write_text("{not json", encoding="utf-8")

            context = build_review_context(root, "fixture-repo", ("src/app.py",))

            statuses = {(artifact.name, artifact.status) for artifact in context.artifacts}
            self.assertIn(("resolvers", "malformed"), statuses)
            self.assertIn(("skills", "missing"), statuses)
            self.assertIn(("unknowns", "missing"), statuses)
            self.assertTrue(any(item.kind == "artifact" for item in context.missing_coverage))

            with self.assertRaisesRegex(ReviewContextError, "repo id is not registered: missing-repo"):
                build_review_context(root, "missing-repo", ("src/app.py",))

    def test_context_represents_ignored_outside_internal_and_untracked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, repo_path = self.prepare_registered_repo(tmp_path)
            (repo_path / "src").mkdir()
            (repo_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

            context = build_review_context(
                root,
                "fixture-repo",
                (
                    "",
                    ".git/config",
                    "../outside.py",
                    "/tmp/outside.py",
                    "org-agent-skills/repos/fixture-repo/resolvers.yml",
                    "src/new_file.py",
                    "src/app.py",
                    "src/app.py",
                ),
            )

            by_path = {(path.classification, path.normalized_path, path.exists) for path in context.changed_paths}
            self.assertIn(("ignored", None, None), by_path)
            self.assertIn(("ignored", ".git/config", None), by_path)
            self.assertIn(("outside", None, None), by_path)
            self.assertIn(("harness-internal", "org-agent-skills/repos/fixture-repo/resolvers.yml", False), by_path)
            self.assertIn(("repo", "src/app.py", True), by_path)
            self.assertIn(("repo", "src/new_file.py", False), by_path)
            self.assertIn(
                ("changed_path", "src/new_file.py", "changed path is not present in the local checkout"),
                {(item.kind, item.path, item.reason) for item in context.missing_coverage},
            )

    def test_existing_changed_files_lifecycle_remains_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root, _repo_path = self.prepare_registered_repo(tmp_path)

            changed = collect_changed_files(root, "fixture-repo", files=("src/app.py", "src/app.py"))

            self.assertEqual(changed.changed_files, ("src/app.py",))
            with self.assertRaises(ReviewError):
                collect_changed_files(root, "fixture-repo", files=("../outside.py",))
