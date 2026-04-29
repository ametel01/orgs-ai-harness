from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_adapter import (
    CodexLocalRuntimeAdapter,
    FinalResponseDecision,
    FixtureRuntimeAdapter,
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeAdapterInput,
    RuntimeAdapterObservation,
    ToolCallDecision,
    adapter_decision_from_json,
    assemble_runtime_prompt,
    build_adapter_skill_catalog,
    build_adapter_tool_catalog,
    parse_adapter_decision_output,
)
from orgs_ai_harness.runtime_context import assemble_runtime_context
from orgs_ai_harness.runtime_events import RuntimeSessionStore
from orgs_ai_harness.runtime_hooks import HookedToolDispatcher, ToolHookContext, ToolHookDecision
from orgs_ai_harness.runtime_permissions import PermissionError, PermissionLevel, classify_command, permission_allows
from orgs_ai_harness.runtime_recovery import summarize_recovery
from orgs_ai_harness.runtime_runner import run_read_only_session
from orgs_ai_harness.runtime_tools import ToolExecutionContext, ToolRegistry, ToolResult, default_tool_registry


class RuntimeAdapterContractTests(unittest.TestCase):
    def test_decision_contracts_serialize_and_parse(self) -> None:
        tool_decision = ToolCallDecision("local.cwd", {"include_workspace": True})
        final_decision = FinalResponseDecision("done")

        self.assertEqual(adapter_decision_from_json(tool_decision.to_json()), tool_decision)
        self.assertEqual(adapter_decision_from_json(final_decision.to_json()), final_decision)
        self.assertEqual(tool_decision.to_json()["tool_id"], "local.cwd")
        self.assertEqual(final_decision.to_json()["summary"], "done")

    def test_malformed_adapter_decisions_fail_clearly(self) -> None:
        cases: list[object] = [
            {"type": "tool_call", "tool_id": "", "tool_input": {}},
            {"type": "tool_call", "tool_id": "local.cwd", "tool_input": []},
            {"type": "final_response", "summary": ""},
            {"type": "unknown"},
            [],
        ]

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(RuntimeAdapterError):
                    adapter_decision_from_json(case)

    def test_strict_adapter_output_parser_accepts_valid_decision_objects(self) -> None:
        tool_decision = parse_adapter_decision_output(
            '{"type":"tool_call","tool_id":"local.search_text","tool_input":{"pattern":"needle"}}'
        )
        final_decision = parse_adapter_decision_output('  {"type":"final_response","summary":"done"}\n')

        self.assertEqual(tool_decision, ToolCallDecision("local.search_text", {"pattern": "needle"}))
        self.assertEqual(final_decision, FinalResponseDecision("done"))

    def test_strict_adapter_output_parser_rejects_ambiguous_or_invalid_text(self) -> None:
        cases = {
            "malformed": "{bad json",
            "extra_prose_before": 'Here: {"type":"final_response","summary":"done"}',
            "extra_prose_after": '{"type":"final_response","summary":"done"} trailing',
            "non_object": '["not", "object"]',
            "unsupported_type": '{"type":"plan","summary":"done"}',
            "missing_fields": '{"type":"tool_call","tool_id":"local.cwd"}',
            "invalid_tool_input": '{"type":"tool_call","tool_id":"local.cwd","tool_input":[]}',
            "non_json_safe": '{"type":"tool_call","tool_id":"local.cwd","tool_input":{"value":NaN}}',
        }

        for name, output in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(RuntimeAdapterError) as raised:
                    parse_adapter_decision_output(output)
                self.assertTrue(str(raised.exception))

    def test_codex_local_adapter_sends_prompt_to_subprocess_and_parses_stdout(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"argv": argv, **kwargs})
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='{"type":"tool_call","tool_id":"local.cwd","tool_input":{}}',
                stderr="",
            )

        adapter = CodexLocalRuntimeAdapter(command_argv=("fake-codex", "run"), runner=fake_runner)
        decision = adapter.decide(RuntimeAdapterInput(goal="inspect", context=[], tools=(), skill_catalog={}))

        self.assertEqual(decision, ToolCallDecision("local.cwd", {}))
        self.assertEqual(calls[0]["argv"], ["fake-codex", "run"])
        self.assertIn("inspect", cast(str, calls[0]["input"]))
        self.assertEqual(calls[0]["timeout"], 30.0)
        self.assertFalse(calls[0]["check"])

    def test_codex_local_adapter_reports_subprocess_failures_clearly(self) -> None:
        def missing(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(argv[0])

        def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(argv, 0.1)

        def nonzero(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="model failed")

        def stderr(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='{"type":"final_response","summary":"done"}',
                stderr="diagnostic",
            )

        def malformed(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

        cases = {
            "missing": (missing, "executable not found"),
            "timeout": (timeout, "timed out"),
            "nonzero": (nonzero, "exited with code 2"),
            "stderr": (stderr, "wrote stderr"),
            "malformed": (malformed, "valid JSON"),
        }

        for name, (runner, expected) in cases.items():
            with self.subTest(name=name):
                adapter = CodexLocalRuntimeAdapter(command_argv=("fake-codex",), timeout_seconds=0.1, runner=runner)
                with self.assertRaises(RuntimeAdapterError) as raised:
                    adapter.decide(RuntimeAdapterInput(goal="inspect", context=[], tools=(), skill_catalog={}))
                self.assertIn(expected, str(raised.exception))

    def test_fixture_adapter_returns_decisions_in_order_and_records_observations(self) -> None:
        adapter = FixtureRuntimeAdapter([ToolCallDecision("local.cwd", {}), FinalResponseDecision("done")])
        first_input = RuntimeAdapterInput(goal="inspect", context=[], tools=(), skill_catalog={"skills": []})
        first_decision = adapter.decide(first_input)
        second_input = RuntimeAdapterInput(goal="inspect", context=[], tools=(), skill_catalog={"skills": []})
        second_decision = adapter.decide(second_input)

        self.assertIsInstance(first_decision, ToolCallDecision)
        self.assertIsInstance(second_decision, FinalResponseDecision)
        self.assertEqual(adapter.input_history, [first_input, second_input])

        with self.assertRaises(RuntimeAdapterError):
            FixtureRuntimeAdapter([])

    def test_prompt_assembly_is_deterministic_and_includes_runtime_contract(self) -> None:
        adapter_input = RuntimeAdapterInput(
            goal="inspect repo",
            context=[{"name": "workspace", "payload": {"cwd": "/tmp/work"}}],
            tools=(
                {
                    "tool_id": "local.cwd",
                    "description": "Return cwd.",
                    "input_schema": {"type": "object"},
                    "required_permission": "read-only",
                },
            ),
            skill_catalog={"skills": [{"path": "org-agent-skills/org/skills/a/SKILL.md", "content": "body"}]},
            observations=(
                RuntimeAdapterObservation(
                    adapter_decision_event_id="s:0001",
                    tool_call_event_id="s:0002",
                    tool_id="local.cwd",
                    result={"ok": True, "tool_id": "local.cwd", "message": "cwd inspected"},
                ),
            ),
            permission_mode="read-only",
        )

        first_prompt = assemble_runtime_prompt(adapter_input)
        second_prompt = assemble_runtime_prompt(adapter_input)

        self.assertEqual(first_prompt, second_prompt)
        self.assertIn("Return exactly one JSON object", first_prompt)
        self.assertIn("inspect repo", first_prompt)
        self.assertIn('"tool_id":"local.cwd"', first_prompt)
        self.assertIn('"required_permission":"read-only"', first_prompt)
        self.assertIn('"path":"org-agent-skills/org/skills/a/SKILL.md"', first_prompt)
        self.assertIn('"adapter_decision_event_id":"s:0001"', first_prompt)
        self.assertLess(first_prompt.index("## tool_catalog"), first_prompt.index("## skill_catalog"))

    def test_prompt_assembly_bounds_large_sections_with_explicit_markers(self) -> None:
        adapter_input = RuntimeAdapterInput(
            goal="inspect repo",
            context=[{"name": "large", "payload": {"content": "x" * 200}}],
            tools=(),
            skill_catalog={"skills": [{"path": "skill.md", "content": "y" * 200}], "resolvers": []},
            observations=(
                RuntimeAdapterObservation(
                    adapter_decision_event_id="s:0001",
                    tool_call_event_id="s:0002",
                    tool_id="local.search_text",
                    result={"stdout": "z" * 200},
                ),
            ),
        )

        prompt = assemble_runtime_prompt(
            adapter_input,
            context_budget_chars=60,
            skill_budget_chars=60,
            observation_budget_chars=60,
        )

        self.assertEqual(prompt.count("[section truncated:"), 3)
        prior_observations = prompt.split("## prior_observations\n", maxsplit=1)[1]
        self.assertLess(
            prior_observations.index('"adapter_decision_event_id"'), prior_observations.index("[section truncated:")
        )


class RuntimeEventStoreTests(unittest.TestCase):
    def test_event_store_writes_reads_and_reports_malformed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeSessionStore(Path(tmp))
            event = store.append_event(
                "session-a",
                "session_started",
                {"goal": "inspect"},
                cwd=Path(tmp),
                workspace=Path(tmp),
                timestamp="2026-04-29T00:00:00Z",
            )
            store.session_path("session-a").write_text(
                store.session_path("session-a").read_text(encoding="utf-8") + "{bad json\n",
                encoding="utf-8",
            )

            session = store.read_session("session-a")

            self.assertEqual(event.event_id, "session-a:0001")
            self.assertEqual(session.events[0].event_type, "session_started")
            self.assertEqual(len(session.malformed), 1)

    def test_recovery_summary_detects_marker_error_pending_call_and_final_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeSessionStore(Path(tmp))
            store.append_event("session-a", "session_started", {})
            store.append_event("session-a", "recovery_marker", {"phase": "resume"})
            store.append_event("session-a", "error", {"message": "boom"})
            pending = store.append_event("session-a", "tool_call", {"tool_id": "local.cwd"})
            session = store.read_session("session-a")

            pending_summary = summarize_recovery(session)

            self.assertEqual(pending_summary.pending_tool_call, pending)
            self.assertIsNotNone(pending_summary.latest_recovery_marker)
            self.assertIsNotNone(pending_summary.latest_error)

            store.append_event("session-a", "tool_result", {"tool_call_event_id": pending.event_id})
            store.append_event("session-a", "final_response", {"summary": "done"})
            complete_summary = summarize_recovery(store.read_session("session-a"))

            self.assertIsNone(complete_summary.pending_tool_call)
            self.assertIsNotNone(complete_summary.final_response)
            self.assertFalse(complete_summary.can_resume_read_only)

    def test_recovery_summary_distinguishes_pending_adapter_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeSessionStore(Path(tmp))
            decision = store.append_event(
                "session-a",
                "adapter_decision",
                {"decision": ToolCallDecision("local.cwd", {}).to_json()},
            )
            pending_summary = summarize_recovery(store.read_session("session-a"))

            self.assertEqual(pending_summary.pending_adapter_decision, decision)
            self.assertFalse(pending_summary.can_resume_read_only)

            call = store.append_event(
                "session-a",
                "tool_call",
                {"tool_id": "local.cwd", "adapter_decision_event_id": decision.event_id},
            )
            store.append_event(
                "session-a",
                "tool_result",
                {"tool_call_event_id": call.event_id, "adapter_decision_event_id": decision.event_id},
            )
            tool_result_only = summarize_recovery(store.read_session("session-a"))
            self.assertIsNotNone(tool_result_only.pending_adapter_decision)
            self.assertIsNone(tool_result_only.pending_tool_call)

            store.append_event(
                "session-a",
                "adapter_observation",
                {"adapter_decision_event_id": decision.event_id, "tool_call_event_id": call.event_id},
            )
            recovered = summarize_recovery(store.read_session("session-a"))

            self.assertIsNone(recovered.pending_adapter_decision)
            self.assertIsNone(recovered.pending_tool_call)
            self.assertTrue(recovered.can_resume_read_only)


class RuntimeToolTests(unittest.TestCase):
    def test_registry_dispatches_tools_and_serializes_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(
                cwd=Path(tmp),
                workspace=Path(tmp),
                permission_mode=PermissionLevel.READ_ONLY,
            )

            result = registry.dispatch("local.cwd", {}, context)

            self.assertTrue(result.ok)
            self.assertEqual(result.to_json()["tool_id"], "local.cwd")
            self.assertIn("local.git_status", {tool.tool_id for tool in registry.list_tools()})

    def test_unknown_tool_and_invalid_inputs_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(cwd=Path(tmp), workspace=Path(tmp))

            with self.assertRaises(Exception) as raised:
                registry.dispatch("missing.tool", {}, context)
            self.assertIn("unknown runtime tool", str(raised.exception))

            with self.assertRaises(Exception) as invalid:
                registry.dispatch("local.search_text", {}, context)
            self.assertIn("pattern", str(invalid.exception))

    def test_permission_denials_are_explicit_and_malformed_permissions_fail(self) -> None:
        decision = permission_allows(PermissionLevel.READ_ONLY, PermissionLevel.WORKSPACE_WRITE)

        self.assertFalse(decision.allowed)
        self.assertIn("workspace-write", decision.reason)
        with self.assertRaises(PermissionError):
            permission_allows("bogus", PermissionLevel.READ_ONLY)

    def test_workspace_write_and_full_access_tools_are_denied_under_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(
                cwd=Path(tmp),
                workspace=Path(tmp),
                permission_mode=PermissionLevel.READ_ONLY,
            )

            result = registry.dispatch("local.write_file", {"path": "x.txt", "content": "x"}, context)

            self.assertTrue(result.denied)
            self.assertFalse((Path(tmp) / "x.txt").exists())

    def test_shell_classification_executes_allowed_commands_and_denies_risky_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(
                cwd=Path(tmp),
                workspace=Path(tmp),
                permission_mode=PermissionLevel.READ_ONLY,
            )

            allowed = registry.dispatch("local.shell", {"argv": ["pwd"]}, context)
            denied = registry.dispatch("local.shell", {"argv": ["rm", "-rf", "x"]}, context)

            self.assertEqual(classify_command(["pwd"]), PermissionLevel.READ_ONLY)
            self.assertTrue(allowed.ok)
            self.assertTrue(denied.denied)

    def test_shell_failure_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(
                cwd=Path(tmp),
                workspace=Path(tmp),
                permission_mode=PermissionLevel.READ_ONLY,
            )

            result = registry.dispatch("local.shell", {"argv": ["ls", "missing"]}, context)

            self.assertFalse(result.ok)
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("missing", result.stderr)

    def test_workspace_write_checks_boundaries_and_protected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_tool_registry()
            context = ToolExecutionContext(
                cwd=Path(tmp),
                workspace=Path(tmp),
                permission_mode=PermissionLevel.WORKSPACE_WRITE,
            )

            written = registry.dispatch("local.write_file", {"path": "notes/run.txt", "content": "ok"}, context)
            outside = registry.dispatch("local.write_file", {"path": "../outside.txt", "content": "bad"}, context)
            protected = registry.dispatch(
                "local.write_file",
                {"path": "org-agent-skills/repos/x/unknowns.yml", "content": "{}"},
                context,
            )

            self.assertTrue(written.ok)
            self.assertEqual(written.changed_files, ("notes/run.txt",))
            self.assertTrue(outside.denied)
            self.assertTrue(protected.denied)


class RuntimeContextAndLoopTests(unittest.TestCase):
    def cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd() / "src")
        return env

    def test_context_assembly_discovers_instructions_cache_and_bounded_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
            (workspace / ".agent-harness").mkdir()
            (workspace / ".agent-harness" / "cache.json").write_text("{}", encoding="utf-8")
            skill = workspace / "org-agent-skills" / "org" / "skills" / "sample"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: sample\n---\n" + ("x" * 500), encoding="utf-8")
            (workspace / "org-agent-skills" / "harness.yml").write_text("org:\n  name: acme\n", encoding="utf-8")

            context = assemble_runtime_context(workspace, budget_chars=120)
            sections = {section.name: section.payload for section in context.sections}

            self.assertTrue(sections["harness"]["org_pack_present"])
            self.assertTrue(sections["harness"]["cache_present"])
            instruction_files = cast(list[dict[str, object]], sections["instructions"]["files"])
            skills = cast(list[dict[str, object]], sections["skills"]["skills"])
            self.assertEqual(instruction_files[0]["path"], "AGENTS.md")
            self.assertLessEqual(len(skills), 20)

    def test_adapter_catalogs_include_sorted_tools_and_bounded_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skill = workspace / "org-agent-skills" / "org" / "skills" / "sample"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: sample\n---\n" + ("x" * 200), encoding="utf-8")
            resolver = workspace / "org-agent-skills" / "repos" / "sample" / "resolvers.yml"
            resolver.parent.mkdir(parents=True)
            resolver.write_text("resolvers:\n  - name: sample\n", encoding="utf-8")

            tool_catalog = build_adapter_tool_catalog(default_tool_registry())
            skill_catalog = build_adapter_skill_catalog(
                assemble_runtime_context(workspace, budget_chars=120), budget_chars=80
            )

            tool_ids = [cast(str, tool["tool_id"]) for tool in tool_catalog]
            self.assertEqual(tool_ids, sorted(tool_ids))
            self.assertIn("required_permission", tool_catalog[0])
            skills = cast(list[dict[str, object]], skill_catalog["skills"])
            resolvers = cast(list[dict[str, object]], skill_catalog["resolvers"])
            self.assertEqual(skills[0]["path"], "org-agent-skills/org/skills/sample/SKILL.md")
            self.assertLessEqual(len(cast(str, skills[0]["content"])), 80)
            self.assertEqual(resolvers[0]["path"], "org-agent-skills/repos/sample/resolvers.yml")

    def test_run_loop_persists_context_tool_result_and_final_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subprocess.run(["git", "init"], cwd=workspace, text=True, capture_output=True, check=True)

            adapter = FixtureRuntimeAdapter([ToolCallDecision("local.cwd", {}), FinalResponseDecision("done")])
            result = run_read_only_session(
                workspace, "summarize this repo state", adapter=adapter, session_id="session-a"
            )
            events = RuntimeSessionStore(workspace / ".agent-harness" / "sessions").read_session("session-a").events
            event_types = [event.event_type for event in events]

            self.assertEqual(result.session_id, "session-a")
            self.assertIn("context_assembled", event_types)
            self.assertIn("adapter_decision", event_types)
            self.assertIn("tool_call", event_types)
            self.assertIn("tool_result", event_types)
            self.assertIn("adapter_observation", event_types)
            self.assertEqual(event_types[-1], "final_response")
            self.assertEqual(len(adapter.received_observations), 1)

    def test_denied_tool_max_steps_malformed_decision_and_adapter_exception_are_logged(self) -> None:
        class MalformedAdapter:
            def decide(self, adapter_input: RuntimeAdapterInput) -> object:
                return {"type": "tool_call", "tool_id": "local.cwd", "tool_input": []}

        class ExplodingAdapter:
            def decide(self, adapter_input: RuntimeAdapterInput) -> object:
                raise RuntimeError("adapter offline")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            denied = run_read_only_session(
                workspace,
                "try write",
                adapter=FixtureRuntimeAdapter([ToolCallDecision("local.write_file", {"path": "x", "content": "x"})]),
                session_id="denied",
            )
            maxed = run_read_only_session(
                workspace,
                "loop",
                adapter=FixtureRuntimeAdapter([ToolCallDecision("local.cwd", {}), ToolCallDecision("local.cwd", {})]),
                max_steps=2,
                session_id="maxed",
            )
            malformed = run_read_only_session(
                workspace,
                "malformed",
                adapter=cast(RuntimeAdapter, MalformedAdapter()),
                session_id="malformed",
            )
            exploded = run_read_only_session(
                workspace,
                "explode",
                adapter=cast(RuntimeAdapter, ExplodingAdapter()),
                session_id="exploded",
            )
            unknown = run_read_only_session(
                workspace,
                "unknown",
                adapter=FixtureRuntimeAdapter([ToolCallDecision("missing.tool", {})]),
                session_id="unknown",
            )
            store = RuntimeSessionStore(workspace / ".agent-harness" / "sessions")

            self.assertFalse(denied.ok)
            self.assertIn("denied", denied.summary)
            self.assertFalse(maxed.ok)
            self.assertIn("max_steps=2", maxed.summary)
            self.assertFalse(malformed.ok)
            self.assertIn("tool_input", malformed.summary)
            self.assertFalse(exploded.ok)
            self.assertIn("adapter offline", exploded.summary)
            self.assertFalse(unknown.ok)
            self.assertIn("unknown runtime tool", unknown.summary)
            self.assertTrue(any(event.event_type == "error" for event in store.read_session("exploded").events))

    def test_cli_run_creates_session_log_and_resume_reports_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subprocess.run(["git", "init"], cwd=workspace, text=True, capture_output=True, check=True)

            run_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "run",
                    "summarize this repo state",
                    "--session-id",
                    "session-a",
                ],
                cwd=workspace,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            resume_result = subprocess.run(
                [sys.executable, "-m", "orgs_ai_harness", "run", "--resume", "--session-id", "session-a"],
                cwd=workspace,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            explicit_fixture_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "run",
                    "summarize this repo state",
                    "--adapter",
                    "fixture",
                    "--session-id",
                    "session-b",
                ],
                cwd=workspace,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            invalid_adapter_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orgs_ai_harness",
                    "run",
                    "summarize this repo state",
                    "--adapter",
                    "missing",
                    "--session-id",
                    "session-c",
                ],
                cwd=workspace,
                env=self.cli_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("Session: session-a", run_result.stdout)
            self.assertTrue((workspace / ".agent-harness" / "sessions" / "session-a.jsonl").is_file())
            self.assertEqual(resume_result.returncode, 0, resume_result.stderr)
            self.assertIn("Resumed session session-a", resume_result.stdout)
            self.assertEqual(explicit_fixture_result.returncode, 0, explicit_fixture_result.stderr)
            self.assertIn("Session: session-b", explicit_fixture_result.stdout)
            self.assertNotEqual(invalid_adapter_result.returncode, 0)
            self.assertIn("unsupported runtime adapter: missing", invalid_adapter_result.stderr)
            self.assertFalse((workspace / ".agent-harness" / "sessions" / "session-c.jsonl").exists())


class RuntimeHookTests(unittest.TestCase):
    def test_pre_hook_denial_prevents_dispatch(self) -> None:
        def deny(context: ToolHookContext) -> ToolHookDecision:
            return ToolHookDecision(allowed=False, reason="blocked by test hook", metadata={"policy": "test"})

        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = HookedToolDispatcher(default_tool_registry(), pre_hooks=(deny,))
            context = ToolExecutionContext(cwd=Path(tmp), workspace=Path(tmp))

            result = dispatcher.dispatch("session-a", "local.cwd", {}, context)

            self.assertTrue(result.denied)
            self.assertEqual(result.payload["policy"], "test")

    def test_post_hook_adds_warnings_and_hook_failures_are_explicit(self) -> None:
        def warn(context: ToolHookContext, result: ToolResult) -> dict[str, JsonValue]:
            return {"warning": result.tool_id}

        def fail(context: ToolHookContext) -> ToolHookDecision:
            raise RuntimeError("policy unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            context = ToolExecutionContext(cwd=Path(tmp), workspace=Path(tmp))
            warned = HookedToolDispatcher(default_tool_registry(), post_hooks=(warn,)).dispatch(
                "session-a",
                "local.cwd",
                {},
                context,
            )
            failed = HookedToolDispatcher(default_tool_registry(), pre_hooks=(fail,)).dispatch(
                "session-a",
                "local.cwd",
                {},
                context,
            )

            hook_warnings = cast(list[dict[str, object]], warned.payload["hook_warnings"])
            self.assertEqual(hook_warnings[0]["warning"], "local.cwd")
            self.assertTrue(failed.denied)
            self.assertIn("failed closed", failed.message)

    def test_registry_can_host_full_access_tool_denied_by_read_only(self) -> None:
        registry = ToolRegistry()
        registry.register(
            runtime_tool := default_tool_registry()
            .get("local.cwd")
            .__class__(
                tool_id="local.full_access_fixture",
                description="fixture",
                input_schema={},
                required_permission=PermissionLevel.FULL_ACCESS,
                handler=lambda tool_input, context: ToolResult(
                    ok=True,
                    tool_id="local.full_access_fixture",
                    message="ok",
                ),
            )
        )
        self.assertEqual(runtime_tool.required_permission, PermissionLevel.FULL_ACCESS)
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.dispatch(
                "local.full_access_fixture",
                {},
                ToolExecutionContext(
                    cwd=Path(tmp),
                    workspace=Path(tmp),
                    permission_mode=PermissionLevel.READ_ONLY,
                ),
            )
        self.assertTrue(result.denied)


if __name__ == "__main__":
    unittest.main()
