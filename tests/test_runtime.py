from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

from orgs_ai_harness.artifact_schemas import JsonValue
from orgs_ai_harness.runtime_context import assemble_runtime_context
from orgs_ai_harness.runtime_events import RuntimeSessionStore
from orgs_ai_harness.runtime_hooks import HookedToolDispatcher, ToolHookContext, ToolHookDecision
from orgs_ai_harness.runtime_permissions import PermissionError, PermissionLevel, classify_command, permission_allows
from orgs_ai_harness.runtime_recovery import summarize_recovery
from orgs_ai_harness.runtime_runner import run_read_only_session
from orgs_ai_harness.runtime_tools import ToolExecutionContext, ToolRegistry, ToolResult, default_tool_registry


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

    def test_run_loop_persists_context_tool_result_and_final_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subprocess.run(["git", "init"], cwd=workspace, text=True, capture_output=True, check=True)

            result = run_read_only_session(workspace, "summarize this repo state", session_id="session-a")
            events = RuntimeSessionStore(workspace / ".agent-harness" / "sessions").read_session("session-a").events
            event_types = [event.event_type for event in events]

            self.assertEqual(result.session_id, "session-a")
            self.assertIn("context_assembled", event_types)
            self.assertIn("tool_call", event_types)
            self.assertIn("tool_result", event_types)
            self.assertEqual(event_types[-1], "final_response")

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

            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("Session: session-a", run_result.stdout)
            self.assertTrue((workspace / ".agent-harness" / "sessions" / "session-a.jsonl").is_file())
            self.assertEqual(resume_result.returncode, 0, resume_result.stderr)
            self.assertIn("Resumed session session-a", resume_result.stdout)


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
