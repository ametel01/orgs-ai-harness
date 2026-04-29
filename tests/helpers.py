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


__all__ = [
    "ATTACHMENT_FILE",
    "DEFAULT_PACK_DIR",
    "AdapterAnswer",
    "DiscoveredRepo",
    "EvalTask",
    "OrgPackError",
    "Path",
    "RepoEntry",
    "RepoRegistryError",
    "_run_checkbox_selector",
    "add_repo",
    "add_rich_fixture_evidence",
    "attach_org_pack",
    "cli_module",
    "create_basic_fixture_repo",
    "create_sensitive_fixture_files",
    "deactivate_repo",
    "derive_repo_id_from_path",
    "derive_repo_id_from_url",
    "export_cached_pack",
    "hashlib",
    "infer_github_owner",
    "init_org_pack",
    "io",
    "is_sensitive_path",
    "json",
    "load_harness_config",
    "load_repo_entries",
    "os",
    "parse_harness_config",
    "rediscovery_cost",
    "refresh_cache",
    "remove_repo",
    "render_explain",
    "replace",
    "repo_onboarding_module",
    "resolve_default_root",
    "save_harness_config",
    "save_repo_entries",
    "score_answer",
    "select_discovered_repos_interactively",
    "set_repo_path",
    "subprocess",
    "sys",
    "tempfile",
    "unittest",
    "validate_org_pack",
]
