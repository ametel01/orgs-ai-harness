from __future__ import annotations

# ruff: noqa: F403,F405 - split unittest modules share the legacy helper namespace.
from tests.helpers import *


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
