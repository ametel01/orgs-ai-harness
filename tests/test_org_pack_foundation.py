from __future__ import annotations

from dataclasses import replace
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orgs_ai_harness.config import load_harness_config, parse_harness_config, save_harness_config
from orgs_ai_harness.org_pack import (
    ATTACHMENT_FILE,
    DEFAULT_PACK_DIR,
    OrgPackError,
    attach_org_pack,
    init_org_pack,
    resolve_default_root,
)
from orgs_ai_harness.repo_registry import (
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


class OrgPackFoundationTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
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
                "org:\n"
                "  name: bad/name\n"
                "  skills_version: nope\n",
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

    def test_cli_validate_reports_broken_pack_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_org_pack(Path(tmp), "acme")
            (root / "org" / "resolvers.yml").unlink()
            (root / "harness.yml").write_text(
                "org:\n"
                "  name: bad/name\n"
                "  skills_version: 2\n",
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
        return env

    def cli_env_with_fake_gh(self, fake_bin: Path) -> dict[str, str]:
        env = self.cli_env()
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        return env

    def write_fake_gh(self, fake_bin: Path, payload: str) -> None:
        fake_bin.mkdir()
        gh_path = fake_bin / "gh"
        gh_path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"payload = {payload!r}\n"
            "if sys.argv[1:4] != ['repo', 'list', 'acme']:\n"
            "    print('unexpected gh args: ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
            "    raise SystemExit(2)\n"
            "print(payload)\n",
            encoding="utf-8",
        )
        gh_path.chmod(0o755)

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


if __name__ == "__main__":
    unittest.main()
