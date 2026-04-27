from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orgs_ai_harness.org_pack import (
    ATTACHMENT_FILE,
    DEFAULT_PACK_DIR,
    OrgPackError,
    attach_org_pack,
    init_org_pack,
    resolve_default_root,
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


if __name__ == "__main__":
    unittest.main()
