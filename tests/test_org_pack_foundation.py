from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import orgs_ai_harness.cli as cli_module
import orgs_ai_harness.repo_onboarding as repo_onboarding_module
from orgs_ai_harness.artifact_schemas import EvalTask
from orgs_ai_harness.cache_manager import export_cached_pack, refresh_cache
from orgs_ai_harness.config import load_harness_config, parse_harness_config, save_harness_config
from orgs_ai_harness.eval_replay import AdapterAnswer, rediscovery_cost, score_answer
from orgs_ai_harness.explain import render_explain
from orgs_ai_harness.org_pack import (
    ATTACHMENT_FILE,
    DEFAULT_PACK_DIR,
    OrgPackError,
    attach_org_pack,
    init_org_pack,
    resolve_default_root,
)
from orgs_ai_harness.repo_discovery import (
    DiscoveredRepo,
    _run_checkbox_selector,
    infer_github_owner,
    select_discovered_repos_interactively,
)
from orgs_ai_harness.repo_onboarding import is_sensitive_path
from orgs_ai_harness.repo_registry import (
    RepoEntry,
    RepoRegistryError,
    add_repo,
    deactivate_repo,
    derive_repo_id_from_path,
    derive_repo_id_from_url,
    load_repo_entries,
    remove_repo,
    save_repo_entries,
    set_repo_path,
)
from orgs_ai_harness.validation import validate_org_pack


def create_basic_fixture_repo(root: Path, name: str = "fixture-repo") -> Path:
    repo_path = root / name
    repo_path.mkdir()
    (repo_path / "README.md").write_text("# Fixture Repo\n\nService notes.\n", encoding="utf-8")
    (repo_path / "package.json").write_text(
        '{"scripts":{"test":"pytest"},"dependencies":{"fastapi":"latest"}}\n',
        encoding="utf-8",
    )
    return repo_path


def create_sensitive_fixture_files(repo_path: Path) -> None:
    (repo_path / ".env").write_text("SECRET_TOKEN=do-not-leak\n", encoding="utf-8")
    (repo_path / ".env.production").write_text("PROD_SECRET=do-not-leak-prod\n", encoding="utf-8")
    (repo_path / "private.pem").write_text("PRIVATE KEY do-not-leak-key\n", encoding="utf-8")
    (repo_path / "config.local.json").write_text('{"token":"do-not-leak-local"}\n', encoding="utf-8")


def add_rich_fixture_evidence(repo_path: Path) -> None:
    (repo_path / ".github" / "workflows").mkdir(parents=True)
    (repo_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (repo_path / "scripts").mkdir()
    (repo_path / "scripts" / "test.sh").write_text("pytest\n", encoding="utf-8")
    (repo_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo_path / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    (repo_path / "AGENTS.md").write_text("# Agent notes\n", encoding="utf-8")


class OrgPackFoundationTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
        env["ORGS_AI_HARNESS_SKILL_GENERATOR"] = "template"
        return env

    def test_init_creates_pack_that_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")

            self.assertEqual(root, Path(tmp).resolve() / DEFAULT_PACK_DIR)
            self.assertTrue((root / "harness.yml").is_file())
            self.assertTrue((root / "org" / "skills").is_dir())
            self.assertTrue((root / "org" / "resolvers.yml").is_file())
            self.assertTrue((root / "repos").is_dir())
            self.assertTrue((root / "proposals").is_dir())
            self.assertTrue((root / "trace-summaries").is_dir())
            self.assertTrue(validate_org_pack(root).ok)

    def test_init_refuses_to_overwrite_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            resolver_path = root / "org" / "resolvers.yml"
            config_before = config_path.read_bytes()
            resolver_before = resolver_path.read_bytes()

            with self.assertRaises(OrgPackError) as raised:
                init_org_pack(Path(tmp), "different")

            self.assertIn("refusing to initialize", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual(resolver_path.read_bytes(), resolver_before)

    def test_attach_records_existing_local_pack_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as cwd_tmp:
            root = init_org_pack(Path(source_tmp), "acme")
            config_path = root / "harness.yml"
            config_before = config_path.read_bytes()

            attached_root = attach_org_pack(Path(cwd_tmp), str(root))

            self.assertEqual(attached_root, root)
            self.assertEqual(resolve_default_root(Path(cwd_tmp)), root)
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual((Path(cwd_tmp) / ATTACHMENT_FILE).read_text(encoding="utf-8"), f"{root}\n")

    def test_attach_rejects_invalid_local_pack(self) -> None:
        with tempfile.TemporaryDirectory() as invalid_tmp, tempfile.TemporaryDirectory() as cwd_tmp:
            invalid_root = Path(invalid_tmp) / "not-a-pack"
            invalid_root.mkdir()

            with self.assertRaises(OrgPackError) as raised:
                attach_org_pack(Path(cwd_tmp), str(invalid_root))

            self.assertIn("invalid org pack", str(raised.exception))
            self.assertIn("missing required file: harness.yml", str(raised.exception))
            self.assertFalse((Path(cwd_tmp) / ATTACHMENT_FILE).exists())

    def test_attach_records_remote_url_without_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as cwd_tmp:
            attached_root = attach_org_pack(Path(cwd_tmp), "git@github.com:acme/org-agent-skills.git")

            self.assertIsNone(attached_root)
            self.assertEqual(
                (Path(cwd_tmp) / ATTACHMENT_FILE).read_text(encoding="utf-8"),
                "git@github.com:acme/org-agent-skills.git\n",
            )

    def test_validation_reports_missing_harness_file_with_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "harness.yml").unlink()

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("missing required file: harness.yml" in error for error in result.errors))
            self.assertTrue(any("restore harness.yml" in error for error in result.errors))

    def test_validation_reports_missing_resolver_file_with_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "org" / "resolvers.yml").unlink()

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("missing required file: org/resolvers.yml" in error for error in result.errors))
            self.assertTrue(any("restore org/resolvers.yml" in error for error in result.errors))

    def test_validation_reports_invalid_org_name_and_skills_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "harness.yml").write_text(
                "org:\n"
                "  name: bad/name\n"
                "  skills_version: 2\n"
                "\n"
                "providers: []\n"
                "repos: []\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("org.name is invalid" in error for error in result.errors))
            self.assertTrue(any("org.skills_version must be 1" in error for error in result.errors))

    def test_validation_reports_multiple_independent_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "org" / "resolvers.yml").unlink()
            (root / "harness.yml").write_text(
                "org:\n  name: bad/name\n  skills_version: nope\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertGreaterEqual(len(result.errors), 5)
            self.assertTrue(any("org/resolvers.yml" in error for error in result.errors))
            self.assertTrue(any("org.name is invalid" in error for error in result.errors))
            self.assertTrue(any("org.skills_version must be 1" in error for error in result.errors))
            self.assertTrue(any("providers" in error for error in result.errors))

    def test_fresh_config_contains_required_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_text = (root / "harness.yml").read_text(encoding="utf-8")

            self.assertIn("org:\n", config_text)
            self.assertIn("  name: acme\n", config_text)
            self.assertIn("  skills_version: 1\n", config_text)
            self.assertIn("providers: []\n", config_text)
            self.assertIn("repos: []\n", config_text)
            self.assertIn("redaction:\n", config_text)
            self.assertIn("  globs: []\n", config_text)
            self.assertIn("  regexes: []\n", config_text)
            self.assertIn("command_permissions: []\n", config_text)

    def test_config_round_trip_preserves_supported_and_future_sections(self) -> None:
        config_text = (
            "org:\n"
            "  name: acme\n"
            "  skills_version: 1\n"
            "  future_org_field: keep-me\n"
            "\n"
            "providers:\n"
            "  - name: github-gh\n"
            "    enabled: false\n"
            "repos:\n"
            "  - id: api-service\n"
            "    active: true\n"
            "redaction:\n"
            "  globs:\n"
            "    - '*.pem'\n"
            "  regexes:\n"
            "    - 'token_[A-Za-z0-9]+'\n"
            "command_permissions:\n"
            "  - prefix: git status\n"
            "future_section:\n"
            "  enabled: true\n"
        )

        config = parse_harness_config(config_text)
        serialized = config.to_text()

        self.assertIn("future_org_field: keep-me", serialized)
        self.assertIn("providers:\n  - name: github-gh", serialized)
        self.assertIn("repos:\n  - id: api-service", serialized)
        self.assertIn("redaction:\n  globs:\n    - '*.pem'", serialized)
        self.assertIn("command_permissions:\n  - prefix: git status", serialized)
        self.assertIn("future_section:\n  enabled: true", serialized)

    def test_config_round_trip_validates_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers:\n"
                "  - name: github-gh\n"
                "repos: []\n"
                "redaction:\n"
                "  globs:\n"
                "    - '*.pem'\n"
                "  regexes: []\n"
                "command_permissions:\n"
                "  - prefix: git status\n",
                encoding="utf-8",
            )

            config = load_harness_config(config_path)
            save_harness_config(config_path, config)

            self.assertTrue(validate_org_pack(root).ok)
            rewritten = config_path.read_text(encoding="utf-8")
            self.assertIn("providers:\n  - name: github-gh", rewritten)
            self.assertIn("command_permissions:\n  - prefix: git status", rewritten)

    def test_invalid_config_still_fails_validation_after_round_trip_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "harness.yml").write_text(
                "org:\n"
                "  name: bad/name\n"
                "  skills_version: 1\n"
                "\n"
                "providers:\n"
                "  - name: github-gh\n"
                "repos: []\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("org.name is invalid" in error for error in result.errors))

    def test_cli_init_then_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "org", "init", "--name", "acme"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertIn("Validation passed", validate_result.stdout)

    def test_cli_init_github_profile_url_infers_org_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "org",
                    "init",
                    "--github",
                    "https://github.com/ametel01",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(init_result.returncode, 0, init_result.stderr)
            self.assertIn("GitHub owner ametel01", init_result.stdout)
            config = load_harness_config(Path(tmp) / DEFAULT_PACK_DIR / "harness.yml")
            self.assertEqual(config.org_name, "ametel01")

    def test_cli_validate_reports_broken_pack_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "org" / "resolvers.yml").unlink()
            (root / "harness.yml").write_text(
                "org:\n  name: bad/name\n  skills_version: 2\n",
                encoding="utf-8",
            )

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("org/resolvers.yml", validate_result.stderr)
            self.assertIn("org.name is invalid", validate_result.stderr)
            self.assertIn("org.skills_version must be 1", validate_result.stderr)

    def test_cli_attach_existing_pack_then_validate(self) -> None:
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as cwd_tmp:
            root = init_org_pack(Path(source_tmp), "acme")
            config_before = (root / "harness.yml").read_bytes()

            attach_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "org",
                    "init",
                    "--repo",
                    str(root),
                ],
                cwd=cwd_tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(attach_result.returncode, 0, attach_result.stderr)
            self.assertIn("Attached org skill pack", attach_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=cwd_tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertEqual((root / "harness.yml").read_bytes(), config_before)

    def test_cli_attach_invalid_pack_reports_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as invalid_tmp, tempfile.TemporaryDirectory() as cwd_tmp:
            invalid_root = Path(invalid_tmp) / "not-a-pack"
            invalid_root.mkdir()

            attach_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "org",
                    "init",
                    "--repo",
                    str(invalid_root),
                ],
                cwd=cwd_tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(attach_result.returncode, 0)
            self.assertIn("missing required file: harness.yml", attach_result.stderr)
            self.assertFalse((Path(cwd_tmp) / ATTACHMENT_FILE).exists())

    def test_cli_attach_remote_url_does_not_create_hosted_resources(self) -> None:
        with tempfile.TemporaryDirectory() as cwd_tmp:
            attach_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "org",
                    "init",
                    "--repo",
                    "git@github.com:acme/org-agent-skills.git",
                ],
                cwd=cwd_tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(attach_result.returncode, 0, attach_result.stderr)
            self.assertIn("No clone, push, or hosted setup was performed", attach_result.stdout)
            self.assertFalse((Path(cwd_tmp) / DEFAULT_PACK_DIR).exists())

    def test_cli_reinit_fails_without_modifying_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "org", "init", "--name", "acme"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)

            config_path = Path(tmp) / DEFAULT_PACK_DIR / "harness.yml"
            config_before = config_path.read_bytes()

            second_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "org", "init", "--name", "other"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(second_result.returncode, 0)
            self.assertIn("refusing to initialize", second_result.stderr)
            self.assertIn("harness org init --repo <path>", second_result.stderr)
            self.assertEqual(config_path.read_bytes(), config_before)


class RepoRegistryTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
        env["ORGS_AI_HARNESS_SKILL_GENERATOR"] = "template"
        return env

    def cli_env_with_fake_gh(self, fake_bin: Path) -> dict[str, str]:
        env = self.cli_env()
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        return env

    def cli_env_without_provider_tools(self, empty_bin: Path) -> dict[str, str]:
        empty_bin.mkdir(exist_ok=True)
        env = self.cli_env()
        env["PATH"] = str(empty_bin)
        return env

    def write_fake_gh(self, fake_bin: Path, payload: str, target: str = "acme") -> None:
        fake_bin.mkdir(exist_ok=True)
        gh_path = fake_bin / "gh"
        gh_path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"payload = {payload!r}\n"
            f"target = {target!r}\n"
            "if sys.argv[1:4] != ['repo', 'list', target]:\n"
            "    print('unexpected gh args: ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
            "    raise SystemExit(2)\n"
            "print(payload)\n",
            encoding="utf-8",
        )
        gh_path.chmod(0o755)

    def write_fake_gh_failure(self, fake_bin: Path, stderr: str, exit_code: int = 1) -> None:
        fake_bin.mkdir(exist_ok=True)
        gh_path = fake_bin / "gh"
        gh_path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"stderr = {stderr!r}\n"
            f"exit_code = {exit_code!r}\n"
            "print(stderr, file=sys.stderr)\n"
            "raise SystemExit(exit_code)\n",
            encoding="utf-8",
        )
        gh_path.chmod(0o755)

    def write_fake_git(self, fake_bin: Path, log_path: Path) -> None:
        fake_bin.mkdir(exist_ok=True)
        git_path = fake_bin / "git"
        git_path.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"log_path = Path({str(log_path)!r})\n"
            "if len(sys.argv) != 4 or sys.argv[1] != 'clone':\n"
            "    print('unexpected git args: ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
            "    raise SystemExit(2)\n"
            "destination = Path(sys.argv[3])\n"
            "destination.mkdir(parents=True)\n"
            "log_path.write_text(log_path.read_text(encoding='utf-8') if log_path.exists() else '', encoding='utf-8')\n"
            "with log_path.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(sys.argv[2] + ' -> ' + str(destination) + '\\n')\n",
            encoding="utf-8",
        )
        git_path.chmod(0o755)

    def test_add_local_repo_writes_selected_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = tmp_path / "api-service"
            repo_path.mkdir()
            root = init_org_pack(tmp_path, "acme")

            entry = add_repo(root, tmp_path, "api-service", purpose="Core backend API and auth")

            self.assertEqual(entry.id, "api-service")
            self.assertEqual(entry.local_path, "../api-service")
            self.assertEqual(entry.coverage_status, "selected")
            self.assertTrue(entry.active)
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].purpose, "Core backend API and auth")
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_add_list_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            init_org_pack(tmp_path, "acme")

            add_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "api-service",
                    "--purpose",
                    "Core backend API and auth",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            self.assertIn("Registered repo api-service", add_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("api-service", list_result.stdout)
            self.assertIn("../api-service", list_result.stdout)
            self.assertIn("active=true", list_result.stdout)
            self.assertIn("status=selected", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_cli_repo_add_rejects_missing_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            add_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "add", "missing-service"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(add_result.returncode, 0)
            self.assertIn("repo path does not exist", add_result.stderr)

    def test_cli_repo_discover_bootstraps_pack_from_github_profile_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "fake-bin"
            payload = (
                '[{"name":"orgs-ai-harness","owner":{"login":"ametel01"},'
                '"url":"https://github.com/ametel01/orgs-ai-harness",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":"Harness"}]'
            )
            self.write_fake_gh(fake_bin, payload, target="ametel01")

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "https://github.com/ametel01",
                    "--select",
                    "orgs-ai-harness",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            self.assertIn("Initialized org skill pack for GitHub owner ametel01", discover_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            config = load_harness_config(root / "harness.yml")
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(config.org_name, "ametel01")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "orgs-ai-harness")
            self.assertEqual(entries[0].owner, "ametel01")

    def test_cli_repo_discover_org_registers_only_selected_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":"Core API"},'
                '{"name":"web-app","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/web-app",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":"Web app"}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            self.assertIn("Registered repo api-service", discover_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "api-service")
            self.assertEqual(entries[0].owner, "acme")
            self.assertEqual(entries[0].url, "https://github.com/acme/api-service")
            self.assertEqual(entries[0].default_branch, "main")
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_discover_reuses_already_registered_selected_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":"Core API"}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)

            first_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )
            second_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertIn("already registered", second_result.stdout)
            entries = load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")
            self.assertEqual(len(entries), 1)

    def test_cli_repo_discover_user_reuses_selection_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"cli-tools","owner":{"login":"alexmetelli"},'
                '"url":"https://github.com/alexmetelli/cli-tools",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":"CLI helpers"}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload, target="alexmetelli")

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-user",
                    "alexmetelli",
                    "--select",
                    "cli-tools",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "cli-tools")
            self.assertEqual(entries[0].owner, "alexmetelli")
            self.assertEqual(entries[0].url, "https://github.com/alexmetelli/cli-tools")
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_discover_github_profile_url_infers_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"cli-tools","owner":{"login":"ametel01"},'
                '"url":"https://github.com/ametel01/cli-tools",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":"CLI helpers"}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload, target="ametel01")

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "https://github.com/ametel01",
                    "--select",
                    "cli-tools",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "cli-tools")
            self.assertEqual(entries[0].owner, "ametel01")
            self.assertEqual(entries[0].url, "https://github.com/ametel01/cli-tools")

    def test_cli_setup_github_profile_bootstraps_and_generates_global_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = (
                '[{"name":"cli-tools","owner":{"login":"ametel01"},'
                '"url":"https://github.com/ametel01/cli-tools",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":"CLI helpers"}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload, target="ametel01")

            setup_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "setup",
                    "https://github.com/ametel01",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                input="1\nn\nglobal\n",
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(setup_result.returncode, 0, setup_result.stderr)
            self.assertIn("Initialized org skill pack for GitHub owner ametel01", setup_result.stdout)
            self.assertIn("Generated global org skill", setup_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "cli-tools")
            self.assertEqual(entries[0].owner, "ametel01")
            self.assertTrue((root / "org" / "skills" / "org-repository-map" / "SKILL.md").is_file())
            self.assertTrue(
                (root / "org" / "skills" / "org-repository-map" / "references" / "repositories.md").is_file()
            )

    def test_discovery_interactive_selection_accepts_numbers_and_names(self) -> None:
        repos = (
            DiscoveredRepo(
                id="api-service",
                name="api-service",
                owner="acme",
                url="https://github.com/acme/api-service",
                default_branch="main",
                visibility="PRIVATE",
                archived=False,
                fork=False,
                description=None,
            ),
            DiscoveredRepo(
                id="web-app",
                name="web-app",
                owner="acme",
                url="https://github.com/acme/web-app",
                default_branch="main",
                visibility="PUBLIC",
                archived=False,
                fork=False,
                description=None,
            ),
        )
        output = io.StringIO()

        selected = select_discovered_repos_interactively(
            repos,
            input_stream=io.StringIO("1,web-app\n"),
            output_stream=output,
        )

        self.assertEqual([repo.id for repo in selected], ["api-service", "web-app"])
        self.assertIn("Discovered repositories", output.getvalue())
        self.assertIn("1. api-service", output.getvalue())

    def test_discovery_checkbox_selection_uses_space_and_arrows(self) -> None:
        repos = (
            DiscoveredRepo(
                id="api-service",
                name="api-service",
                owner="acme",
                url="https://github.com/acme/api-service",
                default_branch="main",
                visibility="PRIVATE",
                archived=False,
                fork=False,
                description=None,
            ),
            DiscoveredRepo(
                id="web-app",
                name="web-app",
                owner="acme",
                url="https://github.com/acme/web-app",
                default_branch="main",
                visibility="PUBLIC",
                archived=False,
                fork=False,
                description=None,
            ),
        )
        output = io.StringIO()
        keys = iter(("toggle", "down", "toggle", "enter"))

        selected = _run_checkbox_selector(
            repos,
            read_key=lambda: next(keys),
            output_stream=output,
            terminal_lines=12,
        )

        self.assertEqual([repo.id for repo in selected], ["api-service", "web-app"])
        self.assertIn("Space to toggle", output.getvalue())
        self.assertIn("[x] 1. api-service", output.getvalue())

    def test_infer_github_owner_accepts_profile_url_and_bare_owner(self) -> None:
        self.assertEqual(infer_github_owner("https://github.com/ametel01"), "ametel01")
        self.assertEqual(infer_github_owner("github.com/acme"), "acme")
        self.assertEqual(infer_github_owner("acme"), "acme")

    def test_cli_repo_discover_rejects_org_and_user_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--github-user",
                    "alexmetelli",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("only one of --github-org or --github-user", discover_result.stderr)

    def test_cli_repo_discover_hides_archived_and_forks_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"active-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/active-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null},'
                '{"name":"old-tool","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/old-tool",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":true,"isFork":false,"description":null},'
                '{"name":"forked-sdk","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/forked-sdk",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":true,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "old-tool",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("filtered out by default", discover_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            self.assertEqual(load_repo_entries(root / "harness.yml"), ())

    def test_cli_repo_discover_include_flags_make_archived_and_forks_selectable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"old-tool","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/old-tool",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":true,"isFork":false,"description":null},'
                '{"name":"forked-sdk","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/forked-sdk",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":true,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--include-archived",
                    "--include-forks",
                    "--select",
                    "old-tool,forked-sdk",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual([entry.id for entry in entries], ["old-tool", "forked-sdk"])
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_discover_multi_select_writes_all_requested_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null},'
                '{"name":"web-app","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/web-app",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service,web-app",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual([entry.id for entry in entries], ["api-service", "web-app"])
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_discover_missing_selection_fails_without_partial_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, payload)
            config_path = tmp_path / DEFAULT_PACK_DIR / "harness.yml"
            before = config_path.read_bytes()

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service,missing-service,other-missing",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("missing-service, other-missing", discover_result.stderr)
            self.assertEqual(config_path.read_bytes(), before)

    def test_cli_repo_discover_without_select_fails_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh(fake_bin, "[]")

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("requires --select in non-interactive use", discover_result.stderr)

    def test_cli_repo_discover_clone_records_selected_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null},'
                '{"name":"web-app","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/web-app",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            clone_log = tmp_path / "clone.log"
            self.write_fake_gh(fake_bin, payload)
            self.write_fake_git(fake_bin, clone_log)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                    "--clone",
                    "--clone-dir",
                    "./covered-repos",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            self.assertTrue((tmp_path / "covered-repos" / "api-service").is_dir())
            self.assertFalse((tmp_path / "covered-repos" / "web-app").exists())
            self.assertIn("https://github.com/acme/api-service", clone_log.read_text(encoding="utf-8"))
            self.assertNotIn("https://github.com/acme/web-app", clone_log.read_text(encoding="utf-8"))
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].local_path, "../covered-repos/api-service")
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_discover_clone_uses_default_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            clone_log = tmp_path / "clone.log"
            self.write_fake_gh(fake_bin, payload)
            self.write_fake_git(fake_bin, clone_log)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                    "--clone",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            self.assertTrue((tmp_path / "covered-repos" / "api-service").is_dir())
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(entries[0].local_path, "../covered-repos/api-service")

    def test_cli_repo_discover_clone_skips_existing_destination_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            existing_repo = tmp_path / "covered-repos" / "api-service"
            existing_repo.mkdir(parents=True)
            payload = (
                '[{"name":"api-service","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/api-service",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE",'
                '"isArchived":false,"isFork":false,"description":null},'
                '{"name":"web-app","owner":{"login":"acme"},'
                '"url":"https://github.com/acme/web-app",'
                '"defaultBranchRef":{"name":"main"},"visibility":"PUBLIC",'
                '"isArchived":false,"isFork":false,"description":null}]'
            )
            fake_bin = tmp_path / "fake-bin"
            clone_log = tmp_path / "clone.log"
            self.write_fake_gh(fake_bin, payload)
            self.write_fake_git(fake_bin, clone_log)

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service,web-app",
                    "--clone",
                    "--clone-dir",
                    "./covered-repos",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(discover_result.returncode, 0, discover_result.stderr)
            self.assertIn("warning: clone destination already exists for api-service", discover_result.stdout)
            clone_log_text = clone_log.read_text(encoding="utf-8")
            self.assertNotIn("https://github.com/acme/api-service", clone_log_text)
            self.assertIn("https://github.com/acme/web-app", clone_log_text)
            entries = {entry.id: entry for entry in load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")}
            self.assertEqual(entries["api-service"].local_path, "../covered-repos/api-service")
            self.assertEqual(entries["web-app"].local_path, "../covered-repos/web-app")

    def test_cli_repo_discover_missing_gh_reports_setup_message_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            config_path = tmp_path / DEFAULT_PACK_DIR / "harness.yml"
            before = config_path.read_bytes()

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_without_provider_tools(tmp_path / "empty-bin"),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("GitHub CLI 'gh' is required", discover_result.stderr)
            self.assertIn("gh auth login", discover_result.stderr)
            self.assertEqual(config_path.read_bytes(), before)

    def test_cli_repo_discover_unauthenticated_gh_reports_login_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh_failure(
                fake_bin,
                "authentication required\nrun gh auth login to continue\nextra noisy detail",
            )
            config_path = tmp_path / DEFAULT_PACK_DIR / "harness.yml"
            before = config_path.read_bytes()

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("not authenticated", discover_result.stderr)
            self.assertIn("gh auth login", discover_result.stderr)
            self.assertNotIn("extra noisy detail", discover_result.stderr)
            self.assertEqual(config_path.read_bytes(), before)

    def test_cli_repo_discover_provider_failure_reports_concise_error_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            fake_bin = tmp_path / "fake-bin"
            self.write_fake_gh_failure(fake_bin, "first failure line\nsecond noisy line")
            config_path = tmp_path / DEFAULT_PACK_DIR / "harness.yml"
            before = config_path.read_bytes()

            discover_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "discover",
                    "--github-org",
                    "acme",
                    "--select",
                    "api-service",
                ],
                cwd=tmp,
                env=self.cli_env_with_fake_gh(fake_bin),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(discover_result.returncode, 0)
            self.assertIn("gh repo discovery failed: first failure line", discover_result.stderr)
            self.assertNotIn("second noisy line", discover_result.stderr)
            self.assertEqual(config_path.read_bytes(), before)

    def test_add_remote_ssh_url_writes_registry_entry_without_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")

            entry = add_repo(
                root,
                Path(tmp),
                "git@github.com:acme/web-app.git",
                owner="product-engineering",
            )

            self.assertEqual(entry.id, "web-app")
            self.assertEqual(entry.name, "web-app")
            self.assertEqual(entry.owner, "product-engineering")
            self.assertEqual(entry.url, "git@github.com:acme/web-app.git")
            self.assertIsNone(entry.local_path)
            self.assertEqual(entry.coverage_status, "selected")
            self.assertTrue(validate_org_pack(root).ok)

    def test_cli_repo_add_remote_https_url_lists_url_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            add_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "https://github.com/acme/web-app.git",
                    "--owner",
                    "product-engineering",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            self.assertIn("Registered repo web-app", add_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("web-app", list_result.stdout)
            self.assertIn("https://github.com/acme/web-app.git", list_result.stdout)
            self.assertIn("status=selected", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_repo_id_derivation_normalizes_local_and_remote_inputs(self) -> None:
        self.assertEqual(derive_repo_id_from_path(Path("/work/API Service.git")), "api-service")
        self.assertEqual(derive_repo_id_from_url("git@github.com:acme/API Service.git"), "api-service")
        self.assertEqual(derive_repo_id_from_url("https://github.com/acme/API Service.git"), "api-service")

    def test_duplicate_repo_add_fails_without_mutating_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service", purpose="Core backend API and auth")
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                add_repo(root, tmp_path, "git@github.com:acme/api-service.git", owner="platform")

            self.assertIn("repo id already registered: api-service", str(raised.exception))
            self.assertIn("../api-service", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

    def test_cli_duplicate_repo_add_reports_collision_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            init_org_pack(tmp_path, "acme")

            first_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "add", "api-service"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)

            second_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "https://github.com/acme/api-service.git",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(second_result.returncode, 0)
            self.assertIn("repo id already registered: api-service", second_result.stderr)
            self.assertIn("../api-service", second_result.stderr)

    def test_validation_reports_duplicate_repo_ids_in_manual_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers: []\n"
                "repos:\n"
                "  - id: api-service\n"
                "    name: api-service\n"
                "    owner: null\n"
                "    purpose: null\n"
                "    url: null\n"
                "    default_branch: main\n"
                "    local_path: ../api-service\n"
                "    coverage_status: selected\n"
                "    active: true\n"
                "    pack_ref: null\n"
                "    external: false\n"
                "  - id: api-service\n"
                "    name: api-service\n"
                "    owner: null\n"
                "    purpose: null\n"
                "    url: git@github.com:acme/api-service.git\n"
                "    default_branch: main\n"
                "    local_path: null\n"
                "    coverage_status: selected\n"
                "    active: true\n"
                "    pack_ref: null\n"
                "    external: false\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("duplicate repo id: api-service" in error for error in result.errors))

    def test_set_repo_path_updates_only_target_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            (tmp_path / "moved-api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service", purpose="Core backend API and auth", owner="platform")
            add_repo(root, tmp_path, "git@github.com:acme/web-app.git", owner="product-engineering")

            updated = set_repo_path(root, tmp_path, "api-service", "moved-api-service")

            self.assertEqual(updated.local_path, "../moved-api-service")
            entries = load_repo_entries(root / "harness.yml")
            api_entry = next(entry for entry in entries if entry.id == "api-service")
            web_entry = next(entry for entry in entries if entry.id == "web-app")
            self.assertEqual(api_entry.local_path, "../moved-api-service")
            self.assertEqual(api_entry.purpose, "Core backend API and auth")
            self.assertEqual(api_entry.owner, "platform")
            self.assertEqual(api_entry.coverage_status, "selected")
            self.assertEqual(web_entry.url, "git@github.com:acme/web-app.git")
            self.assertIsNone(web_entry.local_path)
            self.assertTrue(validate_org_pack(root).ok)

    def test_set_repo_path_rejects_missing_path_without_mutating_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service")
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                set_repo_path(root, tmp_path, "api-service", "missing-api-service")

            self.assertIn("repo path does not exist", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

    def test_set_repo_path_rejects_unknown_repo_id_without_mutating_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            (tmp_path / "moved-api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service")
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                set_repo_path(root, tmp_path, "missing-service", "moved-api-service")

            self.assertIn("repo id is not registered: missing-service", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

    def test_cli_repo_set_path_lists_repaired_path_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            (tmp_path / "moved-api-service").mkdir()
            init_org_pack(tmp_path, "acme")

            add_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "add", "api-service"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            set_path_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "set-path",
                    "api-service",
                    "moved-api-service",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(set_path_result.returncode, 0, set_path_result.stderr)
            self.assertIn("Updated repo api-service path to ../moved-api-service", set_path_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("../moved-api-service", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_deactivate_local_repo_preserves_metadata_and_records_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service", purpose="Core backend API and auth", owner="platform")

            deactivated = deactivate_repo(root, "api-service", "Temporarily excluded")

            self.assertEqual(deactivated.coverage_status, "deactivated")
            self.assertFalse(deactivated.active)
            self.assertEqual(deactivated.deactivation_reason, "Temporarily excluded")
            self.assertEqual(deactivated.purpose, "Core backend API and auth")
            self.assertEqual(deactivated.owner, "platform")
            self.assertEqual(deactivated.local_path, "../api-service")
            self.assertTrue(validate_org_pack(root).ok)

    def test_deactivate_remote_repo_preserves_url_and_lists_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            add_repo(root, Path(tmp), "git@github.com:acme/web-app.git", owner="product-engineering")

            deactivated = deactivate_repo(root, "web-app", "Temporarily excluded")

            self.assertEqual(deactivated.url, "git@github.com:acme/web-app.git")
            self.assertIsNone(deactivated.local_path)
            self.assertEqual(deactivated.coverage_status, "deactivated")
            self.assertFalse(deactivated.active)
            self.assertTrue(validate_org_pack(root).ok)

    def test_deactivate_repo_requires_reason_without_mutating_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service")
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                deactivate_repo(root, "api-service", " ")

            self.assertIn("deactivation reason cannot be empty", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

    def test_validation_reports_deactivated_repo_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers: []\n"
                "repos:\n"
                "  - id: web-app\n"
                "    name: web-app\n"
                "    owner: product-engineering\n"
                "    purpose: null\n"
                "    url: git@github.com:acme/web-app.git\n"
                "    default_branch: main\n"
                "    local_path: null\n"
                "    coverage_status: deactivated\n"
                "    active: false\n"
                "    deactivation_reason: null\n"
                "    pack_ref: null\n"
                "    external: false\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("must include deactivation_reason" in error for error in result.errors))

    def test_cli_repo_deactivate_lists_inactive_status_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            add_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "git@github.com:acme/web-app.git",
                    "--owner",
                    "product-engineering",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            deactivate_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "deactivate",
                    "web-app",
                    "--reason",
                    "Temporarily excluded",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(deactivate_result.returncode, 0, deactivate_result.stderr)
            self.assertIn("Deactivated repo web-app: Temporarily excluded", deactivate_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("web-app", list_result.stdout)
            self.assertIn("active=false", list_result.stdout)
            self.assertIn("status=deactivated", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_remove_repo_deletes_only_registry_entry_and_preserves_local_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = tmp_path / "api-service"
            repo_path.mkdir()
            marker = repo_path / "README.md"
            marker.write_text("keep me\n", encoding="utf-8")
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service")

            removed = remove_repo(root, "api-service", "Registered by mistake")

            self.assertEqual(removed.id, "api-service")
            self.assertEqual(load_repo_entries(root / "harness.yml"), ())
            self.assertTrue(repo_path.is_dir())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep me\n")
            self.assertTrue(validate_org_pack(root).ok)

    def test_remove_repo_requires_reason_without_mutating_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            add_repo(root, tmp_path, "api-service")
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                remove_repo(root, "api-service", " ")

            self.assertIn("removal reason cannot be empty", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

    def test_remove_repo_rejects_protected_entry_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "api-service").mkdir()
            root = init_org_pack(tmp_path, "acme")
            added = add_repo(root, tmp_path, "api-service")
            save_repo_entries(root / "harness.yml", (replace(added, pack_ref="repos/api-service/pack.yml"),))
            config_path = root / "harness.yml"
            before = config_path.read_bytes()

            with self.assertRaises(RepoRegistryError) as raised:
                remove_repo(root, "api-service", "Registered by mistake")

            self.assertIn("requires --force to remove", str(raised.exception))
            self.assertEqual(config_path.read_bytes(), before)

            removed = remove_repo(root, "api-service", "Registered by mistake", force=True)

            self.assertEqual(removed.pack_ref, "repos/api-service/pack.yml")
            self.assertEqual(load_repo_entries(root / "harness.yml"), ())

    def test_cli_repo_remove_lists_empty_registry_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            add_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "git@github.com:acme/web-app.git",
                    "--owner",
                    "product-engineering",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            remove_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "remove",
                    "web-app",
                    "--reason",
                    "Registered by mistake",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(remove_result.returncode, 0, remove_result.stderr)
            self.assertIn("Removed repo web-app from registry: Registered by mistake", remove_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("No repositories registered.", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_add_external_remote_repo_marks_reference_not_selected_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")

            entry = add_repo(
                root,
                Path(tmp),
                "git@github.com:vendor/sdk.git",
                owner="vendor",
                external=True,
            )

            self.assertEqual(entry.id, "sdk")
            self.assertEqual(entry.coverage_status, "external")
            self.assertFalse(entry.active)
            self.assertTrue(entry.external)
            self.assertEqual(entry.url, "git@github.com:vendor/sdk.git")
            self.assertIsNone(entry.local_path)
            self.assertTrue(validate_org_pack(root).ok)

    def test_add_external_local_repo_keeps_path_but_not_active_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "vendor-sdk").mkdir()
            root = init_org_pack(tmp_path, "acme")

            entry = add_repo(root, tmp_path, "vendor-sdk", owner="vendor", external=True)

            self.assertEqual(entry.id, "vendor-sdk")
            self.assertEqual(entry.local_path, "../vendor-sdk")
            self.assertEqual(entry.coverage_status, "external")
            self.assertFalse(entry.active)
            self.assertTrue(entry.external)
            self.assertTrue(validate_org_pack(root).ok)

    def test_validation_rejects_selected_external_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers: []\n"
                "repos:\n"
                "  - id: sdk\n"
                "    name: sdk\n"
                "    owner: vendor\n"
                "    purpose: null\n"
                "    url: git@github.com:vendor/sdk.git\n"
                "    default_branch: main\n"
                "    local_path: null\n"
                "    coverage_status: selected\n"
                "    active: true\n"
                "    deactivation_reason: null\n"
                "    pack_ref: null\n"
                "    external: true\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("cannot be both selected coverage and external" in error for error in result.errors))

    def test_validation_rejects_active_external_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers: []\n"
                "repos:\n"
                "  - id: sdk\n"
                "    name: sdk\n"
                "    owner: vendor\n"
                "    purpose: null\n"
                "    url: git@github.com:vendor/sdk.git\n"
                "    default_branch: main\n"
                "    local_path: null\n"
                "    coverage_status: external\n"
                "    active: true\n"
                "    deactivation_reason: null\n"
                "    pack_ref: null\n"
                "    external: false\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            result = validate_org_pack(root)

            self.assertFalse(result.ok)
            self.assertTrue(any("external coverage must set external: true" in error for error in result.errors))
            self.assertTrue(any("external coverage must be inactive" in error for error in result.errors))

    def test_cli_repo_add_external_lists_external_status_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            init_org_pack(Path(tmp), "acme")

            add_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "repo",
                    "add",
                    "git@github.com:vendor/sdk.git",
                    "--owner",
                    "vendor",
                    "--external",
                ],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            self.assertIn("Registered repo sdk", add_result.stdout)

            list_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "repo", "list"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("sdk", list_result.stdout)
            self.assertIn("git@github.com:vendor/sdk.git", list_result.stdout)
            self.assertIn("active=false", list_result.stdout)
            self.assertIn("status=external", list_result.stdout)

            validate_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "validate"],
                cwd=tmp,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)


class RepoOnboardingTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
        env["ORGS_AI_HARNESS_SKILL_GENERATOR"] = "template"
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

    def run_cli_with_input(self, cwd: Path, args: tuple[str, ...], input_text: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "orgs_ai_harness", *args],
            cwd=cwd,
            env=self.cli_env(),
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_org_level_prompt_uses_embedded_default_when_local_doc_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = init_org_pack(tmp_path, "acme")
            missing_prompt = tmp_path / "missing-org-prompt.md"
            original_prompt_path = cli_module.ORG_LEVEL_SKILL_PROMPT_PATH
            try:
                cli_module.ORG_LEVEL_SKILL_PROMPT_PATH = missing_prompt

                prompt = cli_module._render_org_level_skill_prompt(
                    root,
                    (root / "org" / "llm-output" / "codex" / "skills",),
                    (tmp_path / "home" / ".agents" / "skills",),
                )
            finally:
                cli_module.ORG_LEVEL_SKILL_PROMPT_PATH = original_prompt_path

            self.assertIn("Create organization-level agent skills", prompt)
            self.assertIn("Generation staging targets:", prompt)

    def test_repo_prompt_uses_embedded_default_when_local_doc_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = init_org_pack(tmp_path, "acme")
            repo_path = create_basic_fixture_repo(tmp_path)
            artifact_root = root / "repos" / "fixture-repo"
            scan_result = repo_onboarding_module.OnboardingResult(
                repo_id="fixture-repo",
                artifact_root=artifact_root,
                summary_path=artifact_root / "onboarding-summary.md",
                unknowns_path=artifact_root / "unknowns.yml",
                scan_manifest_path=artifact_root / "scan" / "scan-manifest.yml",
                hypothesis_map_path=artifact_root / "scan" / "hypothesis-map.yml",
            )
            entry = RepoEntry(
                id="fixture-repo",
                name="fixture-repo",
                local_path="fixture-repo",
                url=None,
                purpose=None,
                owner=None,
                default_branch="main",
                external=False,
                active=True,
                coverage_status="selected",
                deactivation_reason=None,
                pack_ref=None,
            )
            missing_prompt = tmp_path / "missing-repo-prompt.md"
            original_prompt_path = repo_onboarding_module.SINGLE_REPO_SKILL_PROMPT_PATH
            try:
                repo_onboarding_module.SINGLE_REPO_SKILL_PROMPT_PATH = missing_prompt

                prompt = repo_onboarding_module._render_llm_skill_prompt(
                    root,
                    entry,
                    repo_path,
                    artifact_root,
                    scan_result,
                    (artifact_root / "llm-output" / "codex" / "skills",),
                    (repo_path / ".agents" / "skills",),
                    "codex",
                )
            finally:
                repo_onboarding_module.SINGLE_REPO_SKILL_PROMPT_PATH = original_prompt_path

            self.assertIn("Create repository-level agent skills", prompt)
            self.assertIn("Available scan artifacts:", prompt)

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

    def prepare_scanned_fixture(self, tmp_path: Path) -> Path:
        create_basic_fixture_repo(tmp_path)
        init_org_pack(tmp_path, "acme")
        add_result = self.run_cli(tmp_path, "repo", "add", "fixture-repo")
        self.assertEqual(add_result.returncode, 0, add_result.stderr)
        scan_result = self.run_cli(tmp_path, "onboard", "fixture-repo", "--scan-only")
        self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
        return tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"

    def close_blocking_unknown(self, tmp_path: Path, repo_id: str) -> None:
        unknowns_path = tmp_path / DEFAULT_PACK_DIR / "repos" / repo_id / "unknowns.yml"
        unknowns = json.loads(unknowns_path.read_text(encoding="utf-8"))
        for unknown in unknowns["unknowns"]:
            unknown["status"] = "closed"
        unknowns_path.write_text(json.dumps(unknowns), encoding="utf-8")

    def test_cli_onboard_scan_only_writes_summary_unknowns_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")

            add_result = self.run_cli(
                tmp_path,
                "repo",
                "add",
                "fixture-repo",
                "--purpose",
                "Core API service",
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            scan_result = self.run_cli(tmp_path, "onboard", "fixture-repo", "--scan-only")

            self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
            self.assertIn("Scanned repo fixture-repo", scan_result.stdout)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            summary = artifact_root / "onboarding-summary.md"
            unknowns = artifact_root / "unknowns.yml"
            manifest = artifact_root / "scan" / "scan-manifest.yml"
            self.assertTrue(summary.is_file())
            self.assertTrue(unknowns.is_file())
            self.assertTrue(manifest.is_file())
            self.assertIn("Onboarding Summary: fixture-repo", summary.read_text(encoding="utf-8"))
            self.assertIn("package.json", unknowns.read_text(encoding="utf-8"))
            self.assertIn("README.md", manifest.read_text(encoding="utf-8"))
            entries = load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "needs-investigation")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertIn("Validation passed for fixture-repo", validate_result.stdout)

    def test_cli_onboard_marks_only_target_repo_needs_investigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path, "api-service")
            create_basic_fixture_repo(tmp_path, "web-app")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "api-service").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "web-app").returncode, 0)

            scan_result = self.run_cli(tmp_path, "onboard", "api-service", "--scan-only")
            list_result = self.run_cli(tmp_path, "repo", "list")
            validate_result = self.run_cli(tmp_path, "validate", "api-service")

            self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("api-service", list_result.stdout)
            self.assertIn("status=needs-investigation", list_result.stdout)
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            entries = {entry.id: entry for entry in load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")}
            self.assertEqual(entries["api-service"].coverage_status, "needs-investigation")
            self.assertEqual(entries["web-app"].coverage_status, "selected")
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "web-app").exists())

    def test_cli_onboard_writes_hypothesis_map_from_mixed_evidence_and_seed_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            add_rich_fixture_evidence(repo_path)
            init_org_pack(tmp_path, "acme")
            add_result = self.run_cli(
                tmp_path,
                "repo",
                "add",
                "fixture-repo",
                "--purpose",
                "Core API service",
                "--owner",
                "platform",
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            scan_result = self.run_cli(tmp_path, "onboard", "fixture-repo", "--scan-only")

            self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            hypothesis_map_path = artifact_root / "scan" / "hypothesis-map.yml"
            self.assertTrue(hypothesis_map_path.is_file())
            hypothesis_map = json.loads(hypothesis_map_path.read_text(encoding="utf-8"))
            self.assertEqual(hypothesis_map["seed_context"]["purpose"]["value"], "Core API service")
            self.assertEqual(hypothesis_map["seed_context"]["purpose"]["source"], "manual repo registration")
            self.assertEqual(hypothesis_map["seed_context"]["owner"]["value"], "platform")
            self.assertIn("README.md", hypothesis_map["evidence_categories"]["readme"])
            self.assertIn("package.json", hypothesis_map["evidence_categories"]["package_manifest"])
            self.assertIn(".github/workflows/ci.yml", hypothesis_map["evidence_categories"]["ci_config"])
            self.assertIn("scripts/test.sh", hypothesis_map["evidence_categories"]["script"])
            self.assertIn("pytest.ini", hypothesis_map["evidence_categories"]["test_config"])
            self.assertIn("AGENTS.md", hypothesis_map["evidence_categories"]["agent_docs"])
            self.assertIn("hypothesis-map.yml", (artifact_root / "onboarding-summary.md").read_text(encoding="utf-8"))

    def test_cli_onboard_hypothesis_map_marks_absent_evidence_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "empty-repo").mkdir()
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "empty-repo").returncode, 0)

            scan_result = self.run_cli(tmp_path, "onboard", "empty-repo", "--scan-only")

            self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
            hypothesis_map_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "empty-repo" / "scan" / "hypothesis-map.yml"
            hypothesis_map = json.loads(hypothesis_map_path.read_text(encoding="utf-8"))
            unknown_hypotheses = [item for item in hypothesis_map["hypotheses"] if item["unknown"]]
            self.assertTrue(unknown_hypotheses)
            self.assertIn("unk_001", hypothesis_map["unknown_refs"])

    def test_cli_onboard_generates_draft_pack_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)

            onboard_result = self.run_cli(tmp_path, "onboard", "fixture-repo")

            self.assertEqual(onboard_result.returncode, 0, onboard_result.stderr)
            self.assertIn("Generated draft pack for repo fixture-repo", onboard_result.stdout)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            self.assertTrue((artifact_root / "skills" / "build-test-debug" / "SKILL.md").is_file())
            self.assertTrue(
                (artifact_root / "skills" / "repo-architecture" / "references" / "repo-evidence.md").is_file()
            )
            self.assertTrue((artifact_root / "resolvers.yml").is_file())
            self.assertTrue((artifact_root / "evals" / "onboarding.yml").is_file())
            self.assertTrue((artifact_root / "scripts" / "check-pack-shape.py").is_file())
            self.assertTrue((artifact_root / "scripts" / "manifest.yml").is_file())
            self.assertTrue((artifact_root / "pack-report.md").is_file())
            self.assertIn("Status: draft", (artifact_root / "pack-report.md").read_text(encoding="utf-8"))
            entries = load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "draft")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertIn("Validation passed for fixture-repo", validate_result.stdout)

    def test_cli_onboard_can_generate_skills_with_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            fake_bin = tmp_path / "fake-bin"
            fake_bin.mkdir()
            codex_path = fake_bin / "codex"
            codex_path.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                "targets = []\n"
                "for index, value in enumerate(sys.argv):\n"
                "    if value == '--add-dir' and index + 1 < len(sys.argv):\n"
                "        path = Path(sys.argv[index + 1])\n"
                "        if path.name == 'skills':\n"
                "            targets.append(path)\n"
                "for target in targets:\n"
                "    for name in ('repo-test-workflow', 'repo-navigation'):\n"
                "        root = target / name\n"
                "        root.mkdir(parents=True, exist_ok=True)\n"
                "        (root / 'SKILL.md').write_text(\n"
                "            '---\\n'\n"
                "            f'name: {name}\\n'\n"
                "            f'description: Full LLM-generated guidance for {name}. '\n"
                "            'Use when working in this repo.\\n'\n"
                "            '---\\n\\n'\n"
                "            f'# {name}\\n\\nUse repository evidence for this workflow.\\n',\n"
                "            encoding='utf-8',\n"
                "        )\n",
                encoding="utf-8",
            )
            codex_path.chmod(0o755)
            env = self.cli_env()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

            onboard_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "onboard", "fixture-repo", "--llm", "codex"],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(onboard_result.returncode, 0, onboard_result.stderr)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            self.assertTrue((artifact_root / "llm-skill-generation-prompt.md").is_file())
            self.assertIn(
                "Full LLM-generated guidance",
                (artifact_root / "skills" / "repo-test-workflow" / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertTrue(
                (tmp_path / "fixture-repo" / ".agents" / "skills" / "repo-test-workflow" / "SKILL.md").is_file()
            )
            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_cli_onboard_codex_failure_points_to_full_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            fake_bin = tmp_path / "fake-bin"
            fake_bin.mkdir()
            codex_path = fake_bin / "codex"
            codex_path.write_text(
                "#!/usr/bin/env python3\nimport sys\nprint('analyzing repository before failure')\nsys.exit(7)\n",
                encoding="utf-8",
            )
            codex_path.chmod(0o755)
            env = self.cli_env()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

            onboard_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "onboard", "fixture-repo", "--llm", "codex"],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(onboard_result.returncode, 1)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            log_path = artifact_root / "llm-skill-generation.log"
            self.assertIn(str(log_path), onboard_result.stderr)
            self.assertIn("Last output", onboard_result.stderr)
            self.assertIn("analyzing repository before failure", log_path.read_text(encoding="utf-8"))

    def test_cli_setup_local_generates_project_specific_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)

            setup_result = self.run_cli_with_input(
                tmp_path,
                ("setup", "local", "--llm", "template"),
                "acme\nfixture-repo\n\n\n\n\n1\nn\nn\nn\n",
            )

            self.assertEqual(setup_result.returncode, 0, setup_result.stderr)
            self.assertIn("Initialized org skill pack", setup_result.stdout)
            self.assertIn("Generated draft pack for repo fixture-repo", setup_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            artifact_root = root / "repos" / "fixture-repo"
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "draft")
            self.assertTrue((artifact_root / "skills" / "build-test-debug" / "SKILL.md").is_file())
            self.assertTrue((artifact_root / "evals" / "onboarding.yml").is_file())

    def test_cli_setup_global_codex_installs_skills_in_home_not_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_home = tmp_path / "home"
            fake_home.mkdir()
            fake_bin = tmp_path / "fake-bin"
            fake_bin.mkdir()
            codex_path = fake_bin / "codex"
            codex_path.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                "targets = []\n"
                "for index, value in enumerate(sys.argv):\n"
                "    if value == '--add-dir' and index + 1 < len(sys.argv):\n"
                "        path = Path(sys.argv[index + 1])\n"
                "        if path.name == 'skills':\n"
                "            targets.append(path)\n"
                "for target in targets:\n"
                "    root = target / 'org-repository-routing'\n"
                "    root.mkdir(parents=True, exist_ok=True)\n"
                "    (root / 'SKILL.md').write_text(\n"
                "        '---\\n'\n"
                "        'name: org-repository-routing\\n'\n"
                "        'description: Route org-level repository questions to the smallest relevant repo skill.\\n'\n"
                "        '---\\n\\n'\n"
                "        '# org-repository-routing\\n\\nUse registered repository metadata.\\n',\n"
                "        encoding='utf-8',\n"
                "    )\n",
                encoding="utf-8",
            )
            codex_path.chmod(0o755)
            env = self.cli_env()
            env["HOME"] = str(fake_home)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

            setup_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "setup",
                    "local",
                    "--llm",
                    "codex",
                    "--skill-target",
                    "both",
                ],
                cwd=tmp,
                env=env,
                input="acme\n\n2\n",
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(setup_result.returncode, 0, setup_result.stderr)
            self.assertTrue((fake_home / ".agents" / "skills" / "org-repository-routing" / "SKILL.md").is_file())
            self.assertTrue((fake_home / ".claude" / "skills" / "org-repository-routing" / "SKILL.md").is_file())
            self.assertFalse((tmp_path / ".agents" / "skills" / "org-repository-routing" / "SKILL.md").exists())
            self.assertFalse((tmp_path / ".claude" / "skills" / "org-repository-routing" / "SKILL.md").exists())
            self.assertIn("Installed global org skills at", setup_result.stdout)

    def test_cli_approve_all_transitions_to_approved_unverified_and_traces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)

            approve_result = self.run_cli(tmp_path, "approve", "fixture-repo", "--all")

            self.assertEqual(approve_result.returncode, 0, approve_result.stderr)
            self.assertIn("status=approved-unverified", approve_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            artifact_root = root / "repos" / "fixture-repo"
            approval_path = artifact_root / "approval.yml"
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            self.assertEqual(approval["status"], "approved-unverified")
            self.assertEqual(approval["decision"], "approved")
            self.assertFalse(approval["verified"])
            self.assertEqual(approval["excluded_artifacts"], [])
            self.assertIn("repos/fixture-repo/pack-report.md", approval["approved_artifacts"])
            protected_paths = {item["path"] for item in approval["protected_artifacts"]}
            self.assertEqual(protected_paths, set(approval["approved_artifacts"]))
            self.assertTrue(all(item["protected"] for item in approval["protected_artifacts"]))
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "approved-unverified")
            self.assertEqual(entries[0].pack_ref, "repos/fixture-repo/approval.yml")
            trace_path = root / "trace-summaries" / "approval-events.jsonl"
            trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(trace_events[-1]["event_type"], "approval")
            self.assertEqual(trace_events[-1]["payload"]["decision"], "approved")
            self.assertEqual(trace_events[-1]["payload"]["excluded_artifacts"], [])

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_validate_approved_unverified_requires_warning_and_unverified_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            approval_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "approval.yml"
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            approval["warnings"] = []
            approval["verified"] = True
            approval_path.write_text(json.dumps(approval), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("verified must be false", validate_result.stderr)
            self.assertIn("approved-unverified warning metadata", validate_result.stderr)

    def test_cache_refresh_writes_repo_local_pointer_and_pinned_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            protected_path = root / "repos" / "fixture-repo" / "pack-report.md"
            protected_before = protected_path.read_bytes()

            result = refresh_cache(root, "fixture-repo")

            self.assertEqual(result.repo_id, "fixture-repo")
            self.assertEqual(result.repo_path, repo_path.resolve())
            self.assertTrue(result.cache_root.is_dir())
            self.assertEqual((result.cache_root / "pack-ref").read_text(encoding="utf-8").strip(), result.pack_ref)
            pointer = (repo_path / ".agent-harness.yml").read_text(encoding="utf-8")
            self.assertIn(f"org_skill_pack: {root.resolve()}", pointer)
            self.assertIn("repo_id: fixture-repo", pointer)
            self.assertIn(f"pack_ref: {result.pack_ref}", pointer)
            metadata = json.loads((result.cache_root / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "approved-unverified")
            self.assertEqual(metadata["source_pack_ref"], "repos/fixture-repo/approval.yml")
            self.assertTrue(
                (result.cache_root / "repos" / "fixture-repo" / "skills" / "build-test-debug" / "SKILL.md").is_file()
            )
            self.assertTrue((result.cache_root / "org" / "resolvers.yml").is_file())
            self.assertEqual(protected_path.read_bytes(), protected_before)

            cli_result = self.run_cli(tmp_path, "cache", "refresh", "fixture-repo")

            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            self.assertIn("Refreshed cache for fixture-repo", cli_result.stdout)
            self.assertEqual(protected_path.read_bytes(), protected_before)

    def test_cli_cache_refresh_requires_approved_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)

            refresh_result = self.run_cli(tmp_path, "cache", "refresh", "fixture-repo")

            self.assertNotEqual(refresh_result.returncode, 0)
            self.assertIn("must be approved-unverified or verified", refresh_result.stderr)

    def test_export_generic_writes_managed_layout_with_status_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            refresh_cache(root, "fixture-repo")

            result = export_cached_pack(root, "generic", "fixture-repo")

            self.assertEqual(result.target, "generic")
            self.assertEqual(
                result.export_root,
                (repo_path / ".agent-harness" / "cache" / "exports" / "generic").resolve(),
            )
            exported_paths = sorted(
                path.relative_to(result.export_root).as_posix()
                for path in result.export_root.rglob("*")
                if path.is_file()
            )
            self.assertIn("pack-status.json", exported_paths)
            self.assertIn("resolvers.yml", exported_paths)
            self.assertIn("skills/build-test-debug/SKILL.md", exported_paths)
            self.assertIn("skills/repo-architecture/references/repo-evidence.md", exported_paths)
            status = json.loads((result.export_root / "pack-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["target"], "generic")
            self.assertEqual(status["status"], "approved-unverified")
            self.assertTrue(any(warning["code"] == "approved-unverified" for warning in status["warnings"]))

            cli_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo")

            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            self.assertIn("Exported generic pack for fixture-repo", cli_result.stdout)

    def test_export_generic_enforces_draft_and_needs_investigation_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            refresh_cache(root, "fixture-repo")
            metadata_path = tmp_path / "fixture-repo" / ".agent-harness" / "cache" / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["status"] = "draft"
            metadata_path.chmod(0o644)
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            draft_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            allowed_draft_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo", "--allow-draft")

            self.assertNotEqual(draft_result.returncode, 0)
            self.assertIn("pass --allow-draft", draft_result.stderr)
            self.assertEqual(allowed_draft_result.returncode, 0, allowed_draft_result.stderr)
            metadata["status"] = "needs-investigation"
            metadata_path.chmod(0o644)
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            blocked_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            development_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo", "--development")

            self.assertNotEqual(blocked_result.returncode, 0)
            self.assertIn("pass --development", blocked_result.stderr)
            self.assertEqual(development_result.returncode, 0, development_result.stderr)

    def test_export_codex_writes_managed_layout_from_same_cached_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            refresh_cache(root, "fixture-repo")

            cli_result = self.run_cli(tmp_path, "export", "codex", "fixture-repo")

            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            self.assertIn("Exported codex pack for fixture-repo", cli_result.stdout)
            export_root = (repo_path / ".agent-harness" / "cache" / "exports" / "codex").resolve()
            exported_paths = sorted(
                path.relative_to(export_root).as_posix() for path in export_root.rglob("*") if path.is_file()
            )
            self.assertIn("pack-status.json", exported_paths)
            self.assertIn("resolvers.yml", exported_paths)
            self.assertIn("skills/build-test-debug/SKILL.md", exported_paths)
            self.assertIn("skills/repo-architecture/SKILL.md", exported_paths)
            status = json.loads((export_root / "pack-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["target"], "codex")
            self.assertEqual(status["status"], "approved-unverified")
            self.assertTrue(any(warning["code"] == "approved-unverified" for warning in status["warnings"]))

    def test_explain_renders_covered_repo_state_from_cache_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(
                self.run_cli(tmp_path, "repo", "add", "fixture-repo", "--purpose", "Fixture service").returncode,
                0,
            )
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            refresh_cache(root, "fixture-repo")
            self.assertEqual(self.run_cli(tmp_path, "eval", "fixture-repo", "--development").returncode, 0)

            output = render_explain(root, "fixture-repo")

            self.assertIn("Explain: fixture-repo", output)
            self.assertIn("Coverage", output)
            self.assertIn("- Why: Fixture service", output)
            self.assertIn("- Lifecycle Status: approved-unverified", output)
            self.assertIn("Cache", output)
            self.assertIn("- Status: present", output)
            self.assertIn("Approved Skills", output)
            self.assertIn("build-test-debug; triggers=test command, build failure, debug repo setup", output)
            self.assertIn("Required Evals", output)
            self.assertIn("- Last Pass Rate:", output)
            self.assertIn("repo-knowledge-readme (repo knowledge)", output)
            self.assertIn("Unresolved Unknowns", output)
            self.assertIn("unk_001: Which command is the narrowest reliable unit test command? [blocking]", output)
            self.assertIn("Boundary Decisions", output)
            self.assertIn("Recent Proposals", output)

            cli_result = self.run_cli(tmp_path, "explain", "fixture-repo")

            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            self.assertEqual(cli_result.stdout, output)

    def test_explain_renders_explicit_empty_states_before_cache_and_eval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR

            explain_result = self.run_cli(tmp_path, "explain", "fixture-repo")

            self.assertEqual(explain_result.returncode, 0, explain_result.stderr)
            self.assertIn("Explain: fixture-repo", explain_result.stdout)
            self.assertIn("Status: missing; run 'harness cache refresh fixture-repo'", explain_result.stdout)
            self.assertIn("Approved Skills\n- None", explain_result.stdout)
            self.assertIn("Required Evals\n- Last Pass Rate: unknown\n- Tasks: none", explain_result.stdout)
            self.assertEqual(render_explain(root, "fixture-repo"), explain_result.stdout)

    def test_explain_records_boundary_decision_for_uncovered_repo_without_registry_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            before_entries = load_repo_entries(root / "harness.yml")

            explain_result = self.run_cli(tmp_path, "explain", "missing-repo")

            self.assertEqual(explain_result.returncode, 0, explain_result.stderr)
            self.assertIn("Explain: missing-repo", explain_result.stdout)
            self.assertIn("- Covered: no", explain_result.stdout)
            self.assertIn("uncovered repo missing-repo was not auto-added", explain_result.stdout)
            after_entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(after_entries, before_entries)
            trace_path = root / "trace-summaries" / "boundary-decisions.jsonl"
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["event_type"], "boundary_decision")
            self.assertIsNone(events[-1]["repo_id"])
            self.assertEqual(events[-1]["payload"]["referenced_repo_id"], "missing-repo")
            self.assertEqual(events[-1]["payload"]["registry_mutation"], "none")

    def test_cli_approve_with_exclusion_protects_only_accepted_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            excluded = "repos/fixture-repo/skills/build-test-debug/SKILL.md"

            approve_result = self.run_cli(tmp_path, "approve", "fixture-repo", "--exclude", excluded)

            self.assertEqual(approve_result.returncode, 0, approve_result.stderr)
            self.assertIn("excluded=1", approve_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            approval = json.loads((root / "repos" / "fixture-repo" / "approval.yml").read_text(encoding="utf-8"))
            self.assertIn(excluded, approval["excluded_artifacts"])
            self.assertNotIn(excluded, approval["approved_artifacts"])
            protected_paths = {item["path"] for item in approval["protected_artifacts"]}
            self.assertNotIn(excluded, protected_paths)
            self.assertEqual(protected_paths, set(approval["approved_artifacts"]))
            trace_path = root / "trace-summaries" / "approval-events.jsonl"
            trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(trace_events[-1]["payload"]["excluded_artifacts"], [excluded])

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_cli_approve_rejects_invalid_exclusion_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            config_before = (root / "harness.yml").read_bytes()

            approve_result = self.run_cli(tmp_path, "approve", "fixture-repo", "--exclude", "missing-artifact.md")

            self.assertNotEqual(approve_result.returncode, 0)
            self.assertIn("does not match a generated artifact", approve_result.stderr)
            self.assertEqual((root / "harness.yml").read_bytes(), config_before)
            self.assertFalse((root / "repos" / "fixture-repo" / "approval.yml").exists())

    def test_cli_reject_records_trace_and_preserves_draft_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            artifact_root = root / "repos" / "fixture-repo"
            draft_file = artifact_root / "skills" / "build-test-debug" / "SKILL.md"
            draft_before = draft_file.read_bytes()

            reject_result = self.run_cli(tmp_path, "reject", "fixture-repo", "--reason", "Needs manual review")

            self.assertEqual(reject_result.returncode, 0, reject_result.stderr)
            self.assertIn("status=needs-investigation", reject_result.stdout)
            self.assertEqual(draft_file.read_bytes(), draft_before)
            rejection = json.loads((artifact_root / "approval.yml").read_text(encoding="utf-8"))
            self.assertEqual(rejection["decision"], "rejected")
            self.assertEqual(rejection["status"], "rejected")
            self.assertEqual(rejection["rationale"], "Needs manual review")
            self.assertEqual(rejection["approved_artifacts"], [])
            self.assertIn("repos/fixture-repo/pack-report.md", rejection["excluded_artifacts"])
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "needs-investigation")
            trace_path = root / "trace-summaries" / "approval-events.jsonl"
            trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(trace_events[-1]["payload"]["decision"], "rejected")
            self.assertEqual(trace_events[-1]["payload"]["rationale"], "Needs manual review")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_cli_reject_approved_pack_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            approval_before = (root / "repos" / "fixture-repo" / "approval.yml").read_bytes()

            reject_result = self.run_cli(tmp_path, "reject", "fixture-repo")

            self.assertNotEqual(reject_result.returncode, 0)
            self.assertIn("is not in draft status", reject_result.stderr)
            self.assertEqual((root / "repos" / "fixture-repo" / "approval.yml").read_bytes(), approval_before)

    def test_cli_onboard_refuses_to_overwrite_protected_approved_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            protected_path = root / "repos" / "fixture-repo" / "pack-report.md"
            protected_before = protected_path.read_bytes()
            config_before = (root / "harness.yml").read_bytes()

            onboard_result = self.run_cli(tmp_path, "onboard", "fixture-repo")

            self.assertNotEqual(onboard_result.returncode, 0)
            self.assertIn("generation would overwrite protected artifact", onboard_result.stderr)
            self.assertIn("Sprint 09 proposal flow", onboard_result.stderr)
            self.assertEqual(protected_path.read_bytes(), protected_before)
            self.assertEqual((root / "harness.yml").read_bytes(), config_before)

    def test_sprint_06_review_approval_acceptance_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path, "approve-flow")
            create_basic_fixture_repo(tmp_path, "exclude-flow")
            create_basic_fixture_repo(tmp_path, "reject-flow")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "approve-flow").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "exclude-flow").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "reject-flow").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR

            self.assertEqual(self.run_cli(tmp_path, "onboard", "approve-flow").returncode, 0)
            review_result = self.run_cli(tmp_path, "approve", "approve-flow")
            self.assertEqual(review_result.returncode, 0, review_result.stderr)
            self.assertIn("Generated Artifacts", review_result.stdout)
            self.assertIn("Command Permissions Requested", review_result.stdout)
            self.assertIn("Risk Notes", review_result.stdout)
            self.assertIn("Unresolved Unknowns", review_result.stdout)
            self.assertFalse((root / "repos" / "approve-flow" / "approval.yml").exists())

            approve_result = self.run_cli(tmp_path, "approve", "approve-flow", "--all")
            self.assertEqual(approve_result.returncode, 0, approve_result.stderr)
            validate_approved = self.run_cli(tmp_path, "validate", "approve-flow")
            self.assertEqual(validate_approved.returncode, 0, validate_approved.stderr)
            protected_file = root / "repos" / "approve-flow" / "pack-report.md"
            protected_before = protected_file.read_bytes()
            overwrite_result = self.run_cli(tmp_path, "onboard", "approve-flow")
            self.assertNotEqual(overwrite_result.returncode, 0)
            self.assertIn("generation would overwrite protected artifact", overwrite_result.stderr)
            self.assertEqual(protected_file.read_bytes(), protected_before)

            self.assertEqual(self.run_cli(tmp_path, "onboard", "exclude-flow").returncode, 0)
            excluded = "repos/exclude-flow/skills/build-test-debug/SKILL.md"
            exclude_result = self.run_cli(tmp_path, "approve", "exclude-flow", "--exclude", excluded)
            self.assertEqual(exclude_result.returncode, 0, exclude_result.stderr)
            validate_excluded = self.run_cli(tmp_path, "validate", "exclude-flow")
            self.assertEqual(validate_excluded.returncode, 0, validate_excluded.stderr)
            excluded_approval = json.loads(
                (root / "repos" / "exclude-flow" / "approval.yml").read_text(encoding="utf-8")
            )
            self.assertIn(excluded, excluded_approval["excluded_artifacts"])
            self.assertNotIn(excluded, {item["path"] for item in excluded_approval["protected_artifacts"]})

            self.assertEqual(self.run_cli(tmp_path, "onboard", "reject-flow").returncode, 0)
            reject_result = self.run_cli(tmp_path, "reject", "reject-flow", "--reason", "Acceptance smoke rejection")
            self.assertEqual(reject_result.returncode, 0, reject_result.stderr)
            validate_rejected = self.run_cli(tmp_path, "validate", "reject-flow")
            self.assertEqual(validate_rejected.returncode, 0, validate_rejected.stderr)
            rejected = json.loads((root / "repos" / "reject-flow" / "approval.yml").read_text(encoding="utf-8"))
            self.assertEqual(rejected["decision"], "rejected")

            entries = {entry.id: entry for entry in load_repo_entries(root / "harness.yml")}
            self.assertEqual(entries["approve-flow"].coverage_status, "approved-unverified")
            self.assertEqual(entries["exclude-flow"].coverage_status, "approved-unverified")
            self.assertEqual(entries["reject-flow"].coverage_status, "needs-investigation")
            trace_events = [
                json.loads(line)
                for line in (root / "trace-summaries" / "approval-events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            decisions = [event["payload"]["decision"] for event in trace_events]
            self.assertGreaterEqual(decisions.count("approved"), 2)
            self.assertIn("rejected", decisions)

    def test_cli_approve_without_all_renders_review_without_mutating_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            config_before = (root / "harness.yml").read_bytes()

            approve_result = self.run_cli(tmp_path, "approve", "fixture-repo")

            self.assertEqual(approve_result.returncode, 0, approve_result.stderr)
            self.assertIn("Approval Review: fixture-repo", approve_result.stdout)
            self.assertIn("Generated Artifacts", approve_result.stdout)
            self.assertIn("Command Permissions Requested", approve_result.stdout)
            self.assertIn("Risk Notes", approve_result.stdout)
            self.assertIn("Unresolved Unknowns", approve_result.stdout)
            self.assertIn("Prior Approved Diff", approve_result.stdout)
            self.assertIn("No prior approved pack found", approve_result.stdout)
            self.assertIn("harness approve fixture-repo --all", approve_result.stdout)
            self.assertEqual((root / "harness.yml").read_bytes(), config_before)
            self.assertFalse((root / "repos" / "fixture-repo" / "approval.yml").exists())

    def test_cli_approve_review_shows_prior_approved_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            entries = load_repo_entries(root / "harness.yml")
            save_repo_entries(
                root / "harness.yml",
                (replace(entries[0], coverage_status="draft"),),
            )
            pack_report = root / "repos" / "fixture-repo" / "pack-report.md"
            pack_report.write_text(
                pack_report.read_text(encoding="utf-8") + "\nLocal draft change.\n",
                encoding="utf-8",
            )

            review_result = self.run_cli(tmp_path, "approve", "fixture-repo")

            self.assertEqual(review_result.returncode, 0, review_result.stderr)
            self.assertIn("Prior Approved Diff", review_result.stdout)
            self.assertIn("Changed: 1", review_result.stdout)
            self.assertIn("Added: 0", review_result.stdout)
            self.assertIn("Removed: 0", review_result.stdout)

    def test_cli_eval_replays_baseline_and_skill_pack_with_fixture_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo")

            self.assertEqual(eval_result.returncode, 0, eval_result.stderr)
            self.assertIn("baseline_pass_rate=", eval_result.stdout)
            self.assertIn("skill_pack_pass_rate=", eval_result.stdout)
            self.assertIn("status=needs-investigation", eval_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            report = json.loads((root / "repos" / "fixture-repo" / "eval-report.yml").read_text(encoding="utf-8"))
            self.assertEqual(report["repo_id"], "fixture-repo")
            self.assertEqual(report["adapter"], "fixture")
            self.assertIn("baseline", report)
            self.assertIn("skill_pack", report)
            self.assertIsInstance(report["baseline_pass_rate"], float)
            self.assertIsInstance(report["skill_pack_pass_rate"], float)
            self.assertIsInstance(report["baseline_delta"], float)
            self.assertIsInstance(report["rediscovery_cost_delta"], float)
            self.assertEqual(report["status"], "needs-investigation")
            self.assertIn("unk_001", report["blocking_unknowns"])
            self.assertIn("harness validate fixture-repo", report["command_approvals_used"])
            self.assertTrue((root / "trace-summaries" / "eval-events.jsonl").is_file())

    def test_cli_eval_refuses_draft_pack_unless_development_flag_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo")
            development_result = self.run_cli(tmp_path, "eval", "fixture-repo", "--development")

            self.assertNotEqual(eval_result.returncode, 0)
            self.assertIn("is still draft", eval_result.stderr)
            self.assertEqual(development_result.returncode, 0, development_result.stderr)
            self.assertIn("status=development", development_result.stdout)
            entries = load_repo_entries(tmp_path / DEFAULT_PACK_DIR / "harness.yml")
            self.assertEqual(entries[0].coverage_status, "draft")

    def test_cli_eval_requires_user_approved_eval_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            excluded = "repos/fixture-repo/evals/onboarding.yml"
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--exclude", excluded).returncode, 0)

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo")

            self.assertNotEqual(eval_result.returncode, 0)
            self.assertIn("no user-approved onboarding evals", eval_result.stderr)

    def test_score_answer_requires_evidence_and_forbidden_claims_fail(self) -> None:
        task: EvalTask = {
            "id": "safe-procedure",
            "expected_files": ["scan/scan-manifest.yml"],
            "expected_commands": ["npm test"],
            "expected_contains": ["sensitive filename policy"],
            "forbidden_contains": ["do-not-leak"],
        }
        passing_answer = AdapterAnswer(
            answer="sensitive filename policy\nnpm test",
            cited_files=("scan/scan-manifest.yml",),
            commands=("npm test",),
            metrics={"elapsed_adapter_steps": 3},
        )
        forbidden_answer = AdapterAnswer(
            answer="sensitive filename policy\nnpm test\ndo-not-leak",
            cited_files=("scan/scan-manifest.yml",),
            commands=("npm test",),
            metrics={"elapsed_adapter_steps": 3},
        )
        missing_evidence_answer = AdapterAnswer(
            answer="sensitive filename policy\nnpm test",
            cited_files=(),
            commands=("npm test",),
            metrics={"elapsed_adapter_steps": 3},
        )

        self.assertTrue(score_answer(task, passing_answer)["passed"])
        self.assertFalse(score_answer(task, forbidden_answer)["passed"])
        self.assertEqual(score_answer(task, forbidden_answer)["forbidden_claims_score"], 0.0)
        self.assertFalse(score_answer(task, missing_evidence_answer)["passed"])
        self.assertEqual(score_answer(task, missing_evidence_answer)["evidence_score"], 0.0)

    def test_rediscovery_cost_sums_bounded_adapter_metrics(self) -> None:
        self.assertEqual(
            rediscovery_cost(
                {
                    "tool_calls": 2,
                    "file_reads": 3,
                    "searches": 4,
                    "command_attempts": 1,
                    "elapsed_adapter_steps": 5,
                }
            ),
            15,
        )

    def test_cli_eval_codex_local_uses_adapter_contract_and_records_trace_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo", "--adapter", "codex-local")

            self.assertEqual(eval_result.returncode, 0, eval_result.stderr)
            root = tmp_path / DEFAULT_PACK_DIR
            report = json.loads((root / "repos" / "fixture-repo" / "eval-report.yml").read_text(encoding="utf-8"))
            self.assertEqual(report["adapter"], "codex-local")
            trace_events = [
                json.loads(line)
                for line in (root / "trace-summaries" / "eval-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(trace_events)
            first = trace_events[0]
            for field in (
                "schema_version",
                "event_id",
                "event_type",
                "timestamp",
                "repo_id",
                "pack_ref",
                "actor",
                "adapter",
                "payload",
            ):
                self.assertIn(field, first)
            event_types = {event["event_type"] for event in trace_events}
            self.assertIn("adapter_run", event_types)
            self.assertIn("eval_answer", event_types)
            self.assertIn("scoring", event_types)
            self.assertIn("command_approval", event_types)

    def test_cli_eval_verifies_pack_when_thresholds_pass_without_blocking_unknowns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.close_blocking_unknown(tmp_path, "fixture-repo")
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo")

            self.assertEqual(eval_result.returncode, 0, eval_result.stderr)
            self.assertIn("status=verified", eval_result.stdout)
            root = tmp_path / DEFAULT_PACK_DIR
            report = json.loads((root / "repos" / "fixture-repo" / "eval-report.yml").read_text(encoding="utf-8"))
            approval = json.loads((root / "repos" / "fixture-repo" / "approval.yml").read_text(encoding="utf-8"))
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(report["status"], "verified")
            self.assertGreaterEqual(report["rediscovery_cost_delta"], 0.30)
            self.assertEqual(entries[0].coverage_status, "verified")
            self.assertTrue(approval["verified"])
            self.assertEqual(approval["status"], "verified")
            self.assertEqual(self.run_cli(tmp_path, "validate", "fixture-repo").returncode, 0)

    def test_cli_eval_keeps_approved_unverified_when_thresholds_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.close_blocking_unknown(tmp_path, "fixture-repo")
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            evals_path = root / "repos" / "fixture-repo" / "evals" / "onboarding.yml"
            evals = json.loads(evals_path.read_text(encoding="utf-8"))
            for task in evals["tasks"]:
                task["expected_files"] = []
                task["expected_commands"] = []
                task["expected_contains"] = []
                task["forbidden_contains"] = []
            evals_path.write_text(json.dumps(evals), encoding="utf-8")

            eval_result = self.run_cli(tmp_path, "eval", "fixture-repo")

            self.assertEqual(eval_result.returncode, 0, eval_result.stderr)
            self.assertIn("status=approved-unverified", eval_result.stdout)
            report = json.loads((root / "repos" / "fixture-repo" / "eval-report.yml").read_text(encoding="utf-8"))
            entries = load_repo_entries(root / "harness.yml")
            self.assertEqual(report["baseline_delta"], 0.0)
            self.assertEqual(report["rediscovery_cost_delta"], 0.0)
            self.assertEqual(entries[0].coverage_status, "approved-unverified")

    def test_sprint_07_local_eval_replay_acceptance_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path, "verified-flow")
            create_basic_fixture_repo(tmp_path, "blocked-flow")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "verified-flow").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "blocked-flow").returncode, 0)

            self.assertEqual(self.run_cli(tmp_path, "onboard", "verified-flow").returncode, 0)
            draft_eval = self.run_cli(tmp_path, "eval", "verified-flow")
            self.assertNotEqual(draft_eval.returncode, 0)
            development_eval = self.run_cli(tmp_path, "eval", "verified-flow", "--development")
            self.assertEqual(development_eval.returncode, 0, development_eval.stderr)
            self.assertIn("status=development", development_eval.stdout)
            self.close_blocking_unknown(tmp_path, "verified-flow")
            self.assertEqual(self.run_cli(tmp_path, "approve", "verified-flow", "--all").returncode, 0)
            verified_eval = self.run_cli(tmp_path, "eval", "verified-flow")
            self.assertEqual(verified_eval.returncode, 0, verified_eval.stderr)
            self.assertIn("status=verified", verified_eval.stdout)

            self.assertEqual(self.run_cli(tmp_path, "onboard", "blocked-flow").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "blocked-flow", "--all").returncode, 0)
            blocked_eval = self.run_cli(tmp_path, "eval", "blocked-flow")
            self.assertEqual(blocked_eval.returncode, 0, blocked_eval.stderr)
            self.assertIn("status=needs-investigation", blocked_eval.stdout)

            root = tmp_path / DEFAULT_PACK_DIR
            verified_report = json.loads(
                (root / "repos" / "verified-flow" / "eval-report.yml").read_text(encoding="utf-8")
            )
            blocked_report = json.loads(
                (root / "repos" / "blocked-flow" / "eval-report.yml").read_text(encoding="utf-8")
            )
            self.assertEqual(verified_report["status"], "verified")
            self.assertEqual(blocked_report["status"], "needs-investigation")
            self.assertIn("baseline", verified_report)
            self.assertIn("skill_pack", verified_report)
            self.assertIn("baseline_pass_rate", verified_report)
            self.assertIn("skill_pack_pass_rate", verified_report)
            self.assertIn("baseline_delta", verified_report)
            self.assertIn("rediscovery_cost_delta", verified_report)
            self.assertIn("command_approvals_used", verified_report)
            self.assertIn("unk_001", blocked_report["blocking_unknowns"])
            entries = {entry.id: entry for entry in load_repo_entries(root / "harness.yml")}
            self.assertEqual(entries["verified-flow"].coverage_status, "verified")
            self.assertEqual(entries["blocked-flow"].coverage_status, "needs-investigation")

    def test_sprint_08_refresh_export_explain_acceptance_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            artifact_root = root / "repos" / "fixture-repo"
            approval = json.loads((artifact_root / "approval.yml").read_text(encoding="utf-8"))
            protected_before = {
                path: (root / path).read_bytes() for path in approval["approved_artifacts"] if isinstance(path, str)
            }

            refresh_result = self.run_cli(tmp_path, "cache", "refresh", "fixture-repo")
            generic_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            codex_result = self.run_cli(tmp_path, "export", "codex", "fixture-repo")
            explain_result = self.run_cli(tmp_path, "explain", "fixture-repo")

            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertEqual(generic_result.returncode, 0, generic_result.stderr)
            self.assertEqual(codex_result.returncode, 0, codex_result.stderr)
            self.assertEqual(explain_result.returncode, 0, explain_result.stderr)
            self.assertIn("Refreshed cache for fixture-repo", refresh_result.stdout)
            self.assertIn("Exported generic pack for fixture-repo", generic_result.stdout)
            self.assertIn("Exported codex pack for fixture-repo", codex_result.stdout)
            self.assertIn("Explain: fixture-repo", explain_result.stdout)
            self.assertIn("Approved Skills", explain_result.stdout)
            self.assertIn("Cache", explain_result.stdout)

            cache_root = repo_path / ".agent-harness" / "cache"
            self.assertTrue((repo_path / ".agent-harness.yml").is_file())
            self.assertTrue((cache_root / "pack-ref").is_file())
            self.assertTrue((cache_root / "exports" / "generic" / "pack-status.json").is_file())
            self.assertTrue((cache_root / "exports" / "codex" / "pack-status.json").is_file())
            self.assertTrue((cache_root / "exports" / "generic" / "skills" / "build-test-debug" / "SKILL.md").is_file())
            self.assertTrue((cache_root / "exports" / "codex" / "skills" / "repo-architecture" / "SKILL.md").is_file())
            for path, before in protected_before.items():
                self.assertEqual((root / path).read_bytes(), before, path)

    def test_sprint_09_improve_proposal_refresh_acceptance_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            self.commit_fixture_repo(repo_path, "initial")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {
                            "run": "skill_pack",
                            "task_id": "command-selection-tests",
                            "passed": False,
                            "answer": "token=do-not-leak",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            improve_result = self.run_cli(tmp_path, "improve", "fixture-repo")
            list_result = self.run_cli(tmp_path, "proposals", "list")
            show_result = self.run_cli(tmp_path, "proposals", "show", "prop_001")
            apply_result = self.run_cli(tmp_path, "proposals", "apply", "prop_001", "--yes")
            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")
            cache_refresh_result = self.run_cli(tmp_path, "cache", "refresh", "fixture-repo")
            export_result = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            (repo_path / "README.md").write_text("# Fixture Repo\n\nChanged for Sprint 09 refresh.\n", encoding="utf-8")
            self.commit_fixture_repo(repo_path, "refresh source change")
            refresh_result = self.run_cli(tmp_path, "refresh", "fixture-repo")

            self.assertEqual(improve_result.returncode, 0, improve_result.stderr)
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertEqual(show_result.returncode, 0, show_result.stderr)
            self.assertEqual(apply_result.returncode, 0, apply_result.stderr)
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertEqual(cache_refresh_result.returncode, 0, cache_refresh_result.stderr)
            self.assertEqual(export_result.returncode, 0, export_result.stderr)
            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertIn("Created proposal prop_001 for fixture-repo", improve_result.stdout)
            self.assertIn("prop_001\tfixture-repo\tstatus=open\trisk=medium", list_result.stdout)
            self.assertIn("Proposal: prop_001", show_result.stdout)
            self.assertIn("Applied proposal prop_001 for fixture-repo", apply_result.stdout)
            self.assertIn("Validation passed for fixture-repo", validate_result.stdout)
            self.assertIn("Created refresh proposal prop_002 for fixture-repo", refresh_result.stdout)
            first_metadata = json.loads((root / "proposals" / "prop_001" / "metadata.yml").read_text(encoding="utf-8"))
            refresh_metadata = json.loads(
                (root / "proposals" / "prop_002" / "metadata.yml").read_text(encoding="utf-8")
            )
            self.assertEqual(first_metadata["status"], "applied")
            self.assertEqual(refresh_metadata["status"], "open")
            self.assertEqual(refresh_metadata["proposal_type"], "onboarding summary updates")
            evidence_text = (root / "proposals" / "prop_001" / "evidence.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("do-not-leak", evidence_text)
            exported_skill = (
                repo_path
                / ".agent-harness"
                / "cache"
                / "exports"
                / "generic"
                / "skills"
                / "build-test-debug"
                / "SKILL.md"
            )
            self.assertIn("Proposal note: review recent trace evidence", exported_skill.read_text(encoding="utf-8"))

    def test_cli_improve_reports_no_proposal_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)

            improve_result = self.run_cli(tmp_path, "improve", "fixture-repo")
            list_result = self.run_cli(tmp_path, "proposals", "list")

            self.assertEqual(improve_result.returncode, 0, improve_result.stderr)
            self.assertIn("No proposal for fixture-repo; insufficient evidence.", improve_result.stdout)
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("No proposals.", list_result.stdout)

    def test_cli_improve_creates_proposal_and_list_show_review_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {
                            "run": "skill_pack",
                            "task_id": "command-selection-tests",
                            "passed": False,
                            "answer": "token=do-not-leak",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            improve_result = self.run_cli(tmp_path, "improve", "fixture-repo")
            list_result = self.run_cli(tmp_path, "proposals", "list")
            show_result = self.run_cli(tmp_path, "proposals", "show", "prop_001")

            self.assertEqual(improve_result.returncode, 0, improve_result.stderr)
            self.assertIn("Created proposal prop_001 for fixture-repo", improve_result.stdout)
            proposal_root = root / "proposals" / "prop_001"
            self.assertTrue((proposal_root / "summary.md").is_file())
            self.assertTrue((proposal_root / "evidence.jsonl").is_file())
            self.assertTrue((proposal_root / "patch.diff").is_file())
            metadata = json.loads((proposal_root / "metadata.yml").read_text(encoding="utf-8"))
            self.assertEqual(metadata["repo_id"], "fixture-repo")
            self.assertEqual(metadata["status"], "open")
            self.assertEqual(metadata["risk"], "medium")
            self.assertEqual(metadata["proposal_type"], "skill edits")
            self.assertEqual(metadata["affected_evals"], ["command-selection-tests"])
            self.assertIn("trace-summaries/eval-events.jsonl:1", metadata["evidence"])
            evidence_text = (proposal_root / "evidence.jsonl").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", evidence_text)
            self.assertNotIn("do-not-leak", evidence_text)
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertIn("prop_001\tfixture-repo\tstatus=open\trisk=medium", list_result.stdout)
            self.assertEqual(show_result.returncode, 0, show_result.stderr)
            self.assertIn("Proposal: prop_001", show_result.stdout)
            self.assertIn("Evidence References", show_result.stdout)
            self.assertIn("Compact Diff", show_result.stdout)

    def test_cli_improve_uses_repo_redaction_config_and_suppresses_sensitive_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            root = tmp_path / DEFAULT_PACK_DIR
            config_path = root / "harness.yml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "redaction:\n  globs: []\n  regexes: []\n",
                    "redaction:\n  globs: []\n  regexes:\n    - 'CUSTOM-[0-9]+'\n",
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": None,
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {
                            "task_id": "safe-procedure",
                            "passed": False,
                            "answer": "custom secret CUSTOM-123",
                            "file": {"path": ".env", "content": "SECRET_TOKEN=do-not-leak"},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            improve_result = self.run_cli(tmp_path, "improve", "fixture-repo")

            self.assertEqual(improve_result.returncode, 0, improve_result.stderr)
            evidence_text = (root / "proposals" / "prop_001" / "evidence.jsonl").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", evidence_text)
            self.assertIn("[REDACTED SENSITIVE FILE CONTENT]", evidence_text)
            self.assertNotIn("CUSTOM-123", evidence_text)
            self.assertNotIn("do-not-leak", evidence_text)

    def test_cli_proposals_apply_requires_confirmation_and_updates_protected_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {"task_id": "command-selection-tests", "passed": False},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "improve", "fixture-repo").returncode, 0)
            target = root / "repos" / "fixture-repo" / "skills" / "build-test-debug" / "SKILL.md"
            target_before = target.read_bytes()

            unconfirmed = self.run_cli(tmp_path, "proposals", "apply", "prop_001")

            self.assertNotEqual(unconfirmed.returncode, 0)
            self.assertIn("requires explicit approval", unconfirmed.stderr)
            self.assertEqual(target.read_bytes(), target_before)
            metadata_path = root / "proposals" / "prop_001" / "metadata.yml"
            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["status"], "open")

            confirmed = self.run_cli(tmp_path, "proposals", "apply", "prop_001", "--yes")

            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            self.assertIn("Applied proposal prop_001 for fixture-repo", confirmed.stdout)
            target_after = target.read_text(encoding="utf-8")
            self.assertIn("Proposal note: review recent trace evidence", target_after)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "applied")
            self.assertEqual(metadata["applied_artifacts"], ["repos/fixture-repo/skills/build-test-debug/SKILL.md"])
            approval = json.loads((root / "repos" / "fixture-repo" / "approval.yml").read_text(encoding="utf-8"))
            protected = {
                item["path"]: item["sha256"] for item in approval["protected_artifacts"] if isinstance(item, dict)
            }
            self.assertEqual(
                protected["repos/fixture-repo/skills/build-test-debug/SKILL.md"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )

    def test_cli_proposals_apply_rejects_invalid_metadata_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": None,
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {"task_id": "command-selection-tests", "passed": False},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "improve", "fixture-repo").returncode, 0)
            metadata_path = root / "proposals" / "prop_001" / "metadata.yml"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["proposal_type"] = "surprise mutation"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            target = root / "repos" / "fixture-repo" / "skills" / "build-test-debug" / "SKILL.md"
            target_before = target.read_bytes()

            apply_result = self.run_cli(tmp_path, "proposals", "apply", "prop_001", "--yes")

            self.assertNotEqual(apply_result.returncode, 0)
            self.assertIn("proposal_type has unsupported value", apply_result.stderr)
            self.assertEqual(target.read_bytes(), target_before)

    def test_cli_proposals_reject_records_reason_without_mutating_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            trace_path = root / "trace-summaries" / "approval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_approval_fixture_0001",
                        "event_type": "approval",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "user",
                        "adapter": None,
                        "payload": {"decision": "approved", "rationale": "Need clearer test command guidance"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "improve", "fixture-repo").returncode, 0)
            target = root / "repos" / "fixture-repo" / "skills" / "build-test-debug" / "SKILL.md"
            target_before = target.read_bytes()

            reject_result = self.run_cli(
                tmp_path,
                "proposals",
                "reject",
                "prop_001",
                "--reason",
                "Insufficient evidence",
            )

            self.assertEqual(reject_result.returncode, 0, reject_result.stderr)
            self.assertIn("Rejected proposal prop_001 for fixture-repo", reject_result.stdout)
            self.assertEqual(target.read_bytes(), target_before)
            metadata = json.loads((root / "proposals" / "prop_001" / "metadata.yml").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "rejected")
            self.assertEqual(metadata["rejection_reason"], "Insufficient evidence")

    def test_cli_refresh_noops_when_source_commit_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            source_commit = self.commit_fixture_repo(repo_path, "initial")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            manifest = json.loads(
                (root / "repos" / "fixture-repo" / "scan" / "scan-manifest.yml").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["repo_source_commit"], source_commit)

            refresh_result = self.run_cli(tmp_path, "refresh", "fixture-repo")

            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertIn("No proposal for fixture-repo; source unchanged.", refresh_result.stdout)
            self.assertFalse((root / "proposals" / "prop_001").exists())

    def test_cli_refresh_changed_source_creates_proposal_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            previous_commit = self.commit_fixture_repo(repo_path, "initial")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            approval = json.loads((root / "repos" / "fixture-repo" / "approval.yml").read_text(encoding="utf-8"))
            protected_before = {
                path: (root / path).read_bytes() for path in approval["approved_artifacts"] if isinstance(path, str)
            }
            (repo_path / "README.md").write_text("# Fixture Repo\n\nChanged service notes.\n", encoding="utf-8")
            current_commit = self.commit_fixture_repo(repo_path, "change readme")

            refresh_result = self.run_cli(tmp_path, "refresh", "fixture-repo")

            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertIn("Created refresh proposal prop_001 for fixture-repo", refresh_result.stdout)
            proposal_root = root / "proposals" / "prop_001"
            metadata = json.loads((proposal_root / "metadata.yml").read_text(encoding="utf-8"))
            self.assertEqual(metadata["repo_id"], "fixture-repo")
            self.assertEqual(metadata["status"], "open")
            self.assertEqual(metadata["proposal_type"], "onboarding summary updates")
            self.assertEqual(metadata["previous_source_commit"], previous_commit)
            self.assertEqual(metadata["current_source_commit"], current_commit)
            self.assertTrue(metadata["affected_evals"])
            self.assertTrue((proposal_root / "evidence.jsonl").is_file())
            for path, before in protected_before.items():
                self.assertEqual((root / path).read_bytes(), before, path)

    def test_cache_export_ignores_open_and_rejected_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            self.assertEqual(self.run_cli(tmp_path, "cache", "refresh", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "export", "generic", "fixture-repo").returncode, 0)
            export_skill = (
                repo_path
                / ".agent-harness"
                / "cache"
                / "exports"
                / "generic"
                / "skills"
                / "build-test-debug"
                / "SKILL.md"
            )
            exported_before = export_skill.read_bytes()
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {"task_id": "command-selection-tests", "passed": False},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "improve", "fixture-repo").returncode, 0)

            open_export = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            reject_result = self.run_cli(
                tmp_path,
                "proposals",
                "reject",
                "prop_001",
                "--reason",
                "Not enough evidence",
            )
            rejected_export = self.run_cli(tmp_path, "export", "generic", "fixture-repo")

            self.assertEqual(open_export.returncode, 0, open_export.stderr)
            self.assertEqual(reject_result.returncode, 0, reject_result.stderr)
            self.assertEqual(rejected_export.returncode, 0, rejected_export.stderr)
            self.assertEqual(export_skill.read_bytes(), exported_before)

    def test_applied_proposal_requires_cache_refresh_before_export_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "approve", "fixture-repo", "--all").returncode, 0)
            root = tmp_path / DEFAULT_PACK_DIR
            self.assertEqual(self.run_cli(tmp_path, "cache", "refresh", "fixture-repo").returncode, 0)
            before_pack_ref = (repo_path / ".agent-harness" / "cache" / "pack-ref").read_text(encoding="utf-8").strip()
            trace_path = root / "trace-summaries" / "eval-events.jsonl"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt_eval_fixture_0001",
                        "event_type": "scoring",
                        "timestamp": "2026-04-28T00:00:00+00:00",
                        "repo_id": "fixture-repo",
                        "pack_ref": "repos/fixture-repo/approval.yml",
                        "actor": "adapter",
                        "adapter": "fixture",
                        "payload": {"task_id": "command-selection-tests", "passed": False},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(tmp_path, "improve", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "proposals", "apply", "prop_001", "--yes").returncode, 0)

            stale_export = self.run_cli(tmp_path, "export", "generic", "fixture-repo")
            refresh_result = self.run_cli(tmp_path, "cache", "refresh", "fixture-repo")
            updated_export = self.run_cli(tmp_path, "export", "generic", "fixture-repo")

            self.assertNotEqual(stale_export.returncode, 0)
            self.assertIn("cache is stale", stale_export.stderr)
            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertEqual(updated_export.returncode, 0, updated_export.stderr)
            cache_root = repo_path / ".agent-harness" / "cache"
            after_pack_ref = (cache_root / "pack-ref").read_text(encoding="utf-8").strip()
            self.assertNotEqual(after_pack_ref, before_pack_ref)
            metadata = json.loads((cache_root / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["applied_proposals"], ["prop_001"])
            exported_skill = cache_root / "exports" / "generic" / "skills" / "build-test-debug" / "SKILL.md"
            self.assertIn("Proposal note: review recent trace evidence", exported_skill.read_text(encoding="utf-8"))

    def test_cli_onboard_rejects_unknown_repo_without_artifact_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")

            scan_result = self.run_cli(tmp_path, "onboard", "missing-repo", "--scan-only")

            self.assertNotEqual(scan_result.returncode, 0)
            self.assertIn("repo id is not registered: missing-repo", scan_result.stderr)
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "missing-repo").exists())

    def test_cli_onboard_rejects_remote_only_repo_with_path_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_org_pack(tmp_path, "acme")
            add_result = self.run_cli(tmp_path, "repo", "add", "https://github.com/acme/web-app.git")
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            scan_result = self.run_cli(tmp_path, "onboard", "web-app", "--scan-only")

            self.assertNotEqual(scan_result.returncode, 0)
            self.assertIn("has no local path", scan_result.stderr)
            self.assertIn("repo discover --clone", scan_result.stderr)
            self.assertIn("repo set-path", scan_result.stderr)
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "web-app").exists())

    def test_cli_onboard_rejects_deactivated_and_external_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path, "api-service")
            create_basic_fixture_repo(tmp_path, "vendor-sdk")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "api-service").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "vendor-sdk", "--external").returncode, 0)
            self.assertEqual(
                self.run_cli(
                    tmp_path, "repo", "deactivate", "api-service", "--reason", "Temporarily excluded"
                ).returncode,
                0,
            )

            deactivated_result = self.run_cli(tmp_path, "onboard", "api-service", "--scan-only")
            external_result = self.run_cli(tmp_path, "onboard", "vendor-sdk", "--scan-only")

            self.assertNotEqual(deactivated_result.returncode, 0)
            self.assertIn("not active selected coverage", deactivated_result.stderr)
            self.assertNotEqual(external_result.returncode, 0)
            self.assertIn("external dependency reference", external_result.stderr)
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "api-service").exists())
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "vendor-sdk").exists())

    def test_cli_onboard_rejects_missing_local_path_with_repair_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            for child in repo_path.iterdir():
                child.unlink()
            repo_path.rmdir()

            scan_result = self.run_cli(tmp_path, "onboard", "fixture-repo", "--scan-only")

            self.assertNotEqual(scan_result.returncode, 0)
            self.assertIn("repo path does not exist", scan_result.stderr)
            self.assertIn("repo set-path", scan_result.stderr)
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo").exists())

    def test_cli_onboard_rejects_batch_repo_id_without_artifact_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path, "api-service")
            create_basic_fixture_repo(tmp_path, "web-app")
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "api-service").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "web-app").returncode, 0)

            scan_result = self.run_cli(tmp_path, "onboard", "api-service,web-app", "--scan-only")

            self.assertNotEqual(scan_result.returncode, 0)
            self.assertIn("repo id is not registered: api-service,web-app", scan_result.stderr)
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "api-service").exists())
            self.assertFalse((tmp_path / DEFAULT_PACK_DIR / "repos" / "web-app").exists())

    def test_sensitive_path_detection_policy(self) -> None:
        self.assertTrue(is_sensitive_path(".env"))
        self.assertTrue(is_sensitive_path(".env.production"))
        self.assertTrue(is_sensitive_path("private.pem"))
        self.assertTrue(is_sensitive_path("config.local.json"))
        self.assertTrue(is_sensitive_path("secrets/api-token.txt"))
        self.assertTrue(is_sensitive_path("id_rsa"))
        self.assertFalse(is_sensitive_path("README.md"))
        self.assertFalse(is_sensitive_path("package.json"))

    def test_cli_onboard_skips_sensitive_files_without_leaking_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = create_basic_fixture_repo(tmp_path)
            create_sensitive_fixture_files(repo_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)

            scan_result = self.run_cli(tmp_path, "onboard", "fixture-repo", "--scan-only")

            self.assertEqual(scan_result.returncode, 0, scan_result.stderr)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            artifact_text = "\n".join(
                path.read_text(encoding="utf-8") for path in artifact_root.rglob("*") if path.is_file()
            )
            self.assertIn('"path": ".env"', artifact_text)
            self.assertIn('"path": ".env.production"', artifact_text)
            self.assertIn('"path": "private.pem"', artifact_text)
            self.assertIn('"path": "config.local.json"', artifact_text)
            self.assertIn('"reason": "sensitive filename policy"', artifact_text)
            self.assertIn('"path": "README.md"', artifact_text)
            self.assertNotIn("do-not-leak", artifact_text)

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_validate_repo_reports_missing_onboarding_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_root = self.prepare_scanned_fixture(tmp_path)
            (artifact_root / "onboarding-summary.md").unlink()

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("missing onboarding summary", validate_result.stderr)

    def test_validate_repo_reports_invalid_unknown_severity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_root = self.prepare_scanned_fixture(tmp_path)
            unknowns_path = artifact_root / "unknowns.yml"
            unknowns = json.loads(unknowns_path.read_text(encoding="utf-8"))
            unknowns["unknowns"][0]["severity"] = "urgent"
            unknowns_path.write_text(json.dumps(unknowns), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("invalid severity", validate_result.stderr)

    def test_validate_repo_reports_missing_scan_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_root = self.prepare_scanned_fixture(tmp_path)
            (artifact_root / "scan" / "scan-manifest.yml").unlink()

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("missing scan manifest", validate_result.stderr)

    def test_validate_repo_reports_malformed_skipped_path_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_root = self.prepare_scanned_fixture(tmp_path)
            manifest_path = artifact_root / "scan" / "scan-manifest.yml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skipped_paths"] = [{"reason": "missing path"}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("skipped_paths item 1 field path", validate_result.stderr)

    def test_validate_repo_reports_malformed_hypothesis_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_root = self.prepare_scanned_fixture(tmp_path)
            hypothesis_path = artifact_root / "scan" / "hypothesis-map.yml"
            hypothesis_map = json.loads(hypothesis_path.read_text(encoding="utf-8"))
            hypothesis_map["hypotheses"][0]["evidence_paths"] = "README.md"
            hypothesis_path.write_text(json.dumps(hypothesis_map), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("field evidence_paths must be a list", validate_result.stderr)

    def test_validate_repo_reports_broken_generated_skill_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            artifact_root = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo"
            bad_root = artifact_root / "skills" / "Bad--Skill"
            (artifact_root / "skills" / "build-test-debug").rename(bad_root)

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("generated skill directory name is invalid", validate_result.stderr)
            self.assertIn("frontmatter name must match directory", validate_result.stderr)

    def test_validate_repo_reports_broken_skill_reference_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            skill_path = (
                tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "skills" / "build-test-debug" / "SKILL.md"
            )
            skill_path.write_text(
                skill_path.read_text(encoding="utf-8").replace(
                    "references/repo-evidence.md",
                    "references/missing.md",
                ),
                encoding="utf-8",
            )

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("broken reference link", validate_result.stderr)

    def test_validate_repo_reports_broken_resolver_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            resolvers_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "resolvers.yml"
            resolvers = json.loads(resolvers_path.read_text(encoding="utf-8"))
            resolvers["resolvers"][0]["skill"] = "missing-skill"
            resolvers_path.write_text(json.dumps(resolvers), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("references missing skill: missing-skill", validate_result.stderr)

    def test_validate_repo_reports_invalid_eval_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            evals_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "evals" / "onboarding.yml"
            evals = json.loads(evals_path.read_text(encoding="utf-8"))
            evals["tasks"][0]["expected_files"] = "onboarding-summary.md"
            evals_path.write_text(json.dumps(evals), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("field expected_files must be a list", validate_result.stderr)

    def test_validate_repo_reports_invalid_script_policy_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            manifest_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "scripts" / "manifest.yml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scripts"][0]["review_required"] = False
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("field review_required must be true", validate_result.stderr)

    def test_validate_repo_reports_missing_generated_script_command_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            manifest_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "scripts" / "manifest.yml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["command_permissions"] = [
                permission
                for permission in manifest["command_permissions"]
                if permission["command"] != "python scripts/check-pack-shape.py"
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("missing command permission record for generated script", validate_result.stderr)

    def test_validate_repo_reports_malformed_command_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            manifest_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "scripts" / "manifest.yml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["command_permissions"][0]["local_only"] = False
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            validate_result = self.run_cli(tmp_path, "validate", "fixture-repo")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("command_permissions item 1 field local_only must be true", validate_result.stderr)

    def test_onboard_marks_one_repo_org_skills_as_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            create_basic_fixture_repo(tmp_path)
            init_org_pack(tmp_path, "acme")
            self.assertEqual(self.run_cli(tmp_path, "repo", "add", "fixture-repo").returncode, 0)
            self.assertEqual(self.run_cli(tmp_path, "onboard", "fixture-repo").returncode, 0)
            unknowns_path = tmp_path / DEFAULT_PACK_DIR / "repos" / "fixture-repo" / "unknowns.yml"
            unknowns = json.loads(unknowns_path.read_text(encoding="utf-8"))

            self.assertEqual(unknowns["candidate_org_skills"][0]["status"], "candidate")
            self.assertIn("cross-repo review", unknowns["candidate_org_skills"][0]["reason"])

    def test_validate_rejects_inactive_needs_investigation_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = init_org_pack(tmp_path, "acme")
            config_path = root / "harness.yml"
            config_path.write_text(
                "org:\n"
                "  name: acme\n"
                "  skills_version: 1\n"
                "\n"
                "providers: []\n"
                "repos:\n"
                "  - id: api-service\n"
                "    name: api-service\n"
                "    owner: null\n"
                "    purpose: null\n"
                "    url: null\n"
                "    default_branch: main\n"
                "    local_path: ../api-service\n"
                "    coverage_status: needs-investigation\n"
                "    active: false\n"
                "    deactivation_reason: null\n"
                "    pack_ref: null\n"
                "    external: false\n"
                "redaction:\n"
                "  globs: []\n"
                "  regexes: []\n"
                "command_permissions: []\n",
                encoding="utf-8",
            )

            validate_result = self.run_cli(tmp_path, "validate")

            self.assertNotEqual(validate_result.returncode, 0)
            self.assertIn("needs-investigation coverage must be active", validate_result.stderr)


if __name__ == "__main__":
    unittest.main()
