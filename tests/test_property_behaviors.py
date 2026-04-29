from __future__ import annotations

import re
import string
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from orgs_ai_harness.config import parse_harness_config
from orgs_ai_harness.proposals import ProposalError, _redact_jsonable, _validate_relative_artifact_path
from orgs_ai_harness.repo_onboarding import is_sensitive_path
from orgs_ai_harness.repo_registry import derive_repo_id_from_path, derive_repo_id_from_url

SAFE_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + "-_",
    min_size=1,
    max_size=24,
).filter(lambda value: value.strip("-_"))


@settings(max_examples=40, deadline=None)
@given(org_name=SAFE_TEXT, version=SAFE_TEXT, future_key=SAFE_TEXT, future_value=SAFE_TEXT)
def test_harness_config_round_trip_preserves_future_top_level_blocks(
    org_name: str,
    version: str,
    future_key: str,
    future_value: str,
) -> None:
    future_key = f"x_{future_key.lower()}"
    text = f"org:\n  name: {org_name}\n  skills_version: {version}\n\n{future_key}:\n  value: {future_value}\n"

    parsed = parse_harness_config(text)
    rendered = parsed.to_text()
    reparsed = parse_harness_config(rendered)

    assert reparsed.org_name == org_name
    assert reparsed.skills_version == version
    assert f"{future_key}:\n  value: {future_value}\n" in rendered
    assert "providers: []\n" in rendered
    assert "repos: []\n" in rendered
    assert "command_permissions: []\n" in rendered


@settings(max_examples=40)
@given(repo_name=SAFE_TEXT)
def test_repo_id_derivation_matches_path_and_common_remote_url_forms(repo_name: str) -> None:
    expected = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name.strip().lower()).strip("-._")

    assert derive_repo_id_from_path(Path(f"{repo_name}.git")) == expected
    assert derive_repo_id_from_url(f"https://github.com/acme/{repo_name}.git") == expected
    assert derive_repo_id_from_url(f"git@github.com:acme/{repo_name}.git") == expected


@settings(max_examples=40)
@given(secret_value=SAFE_TEXT)
def test_redaction_replaces_secret_values_without_changing_shape(secret_value: str) -> None:
    payload = {
        "path": "src/settings.py",
        "content": f"api_key={secret_value}",
        "nested": [{"token": f"Bearer {secret_value}"}],
    }

    redacted = _redact_jsonable(
        payload,
        (
            re.compile(r"(?i)(api[_-]?key)(=)([^,\n]+)"),
            re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
        ),
    )

    assert isinstance(redacted, dict)
    assert set(redacted) == set(payload)
    assert redacted["content"] == "api_key=[REDACTED]"
    nested = redacted["nested"]
    assert isinstance(nested, list)
    assert nested == [{"token": "Bearer [REDACTED]"}]
    assert f"api_key={secret_value}" not in str(redacted["content"])
    assert f"Bearer {secret_value}" not in str(nested)


@settings(max_examples=40)
@given(st.sampled_from([".env", ".env.local", "private.pem", "service.key", "credentials.json", "token.txt"]))
def test_sensitive_path_policy_recognizes_secret_like_filenames(filename: str) -> None:
    assert is_sensitive_path(str(Path("config") / filename))


@settings(max_examples=40)
@given(path_part=SAFE_TEXT)
def test_relative_artifact_path_validation_rejects_escape_attempts(path_part: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        proposal_root = root / "proposals" / "prop_001"
        proposal_root.mkdir(parents=True)

        _validate_relative_artifact_path(root, proposal_root, f"repos/{path_part}/summary.md")
        for escaping_path in (f"../{path_part}", f"repos/../{path_part}", str(root.parent / path_part)):
            try:
                _validate_relative_artifact_path(root, proposal_root, escaping_path)
            except ProposalError:
                continue
            raise AssertionError(f"accepted escaping artifact path: {escaping_path}")
