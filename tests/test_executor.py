"""
Tests for agent.executor — TaskExecutor and _build_prompt
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.executor import ExecutionResult, TaskExecutor, _build_prompt
from agent.identifier import MaintenanceTask, TaskKind


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "agent": {
            "model": "claude-opus-4-6",
            "max_turns": 10,
            "max_budget_usd": 1.0,
        }
    }


def _task(kind=TaskKind.ADD_README, file_path="README.md", line=0) -> MaintenanceTask:
    return MaintenanceTask(
        id="task-1",
        kind=kind,
        title="Test task",
        description="Do the thing",
        file_path=file_path,
        line=line,
    )


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_contains_repo_root(self):
        task = _task()
        prompt = _build_prompt(task, "/repo/root")
        assert "/repo/root" in prompt

    def test_prompt_contains_task_title(self):
        task = _task()
        task.title = "Unique Task Title XYZ"
        prompt = _build_prompt(task, "/repo")
        assert "Unique Task Title XYZ" in prompt

    def test_prompt_contains_description(self):
        task = _task()
        task.description = "Special unique description ABC"
        prompt = _build_prompt(task, "/repo")
        assert "Special unique description ABC" in prompt

    def test_prompt_contains_file_path(self):
        task = _task(file_path="special/file.py")
        prompt = _build_prompt(task, "/repo")
        assert "special/file.py" in prompt

    def test_prompt_contains_line_when_nonzero(self):
        task = _task(line=42)
        prompt = _build_prompt(task, "/repo")
        assert "42" in prompt

    def test_prompt_no_line_section_when_zero(self):
        task = _task(line=0)
        prompt = _build_prompt(task, "/repo")
        assert "Starting at line: 0" not in prompt

    def test_readme_guidance_added(self):
        task = _task(kind=TaskKind.ADD_README)
        prompt = _build_prompt(task, "/repo")
        assert "README" in prompt

    def test_docstring_guidance_added(self):
        task = _task(kind=TaskKind.ADD_FUNCTION_DOCSTRING)
        prompt = _build_prompt(task, "/repo")
        assert "Google-style" in prompt.lower() or "docstring" in prompt.lower()

    def test_type_hint_guidance_added(self):
        task = _task(kind=TaskKind.ADD_TYPE_HINTS)
        prompt = _build_prompt(task, "/repo")
        assert "type hint" in prompt.lower() or "annotation" in prompt.lower()

    def test_dependency_audit_guidance_added(self):
        task = _task(kind=TaskKind.AUDIT_DEPENDENCIES)
        prompt = _build_prompt(task, "/repo")
        assert "audit" in prompt.lower() or "manifest" in prompt.lower()

    def test_prompt_is_string(self):
        task = _task()
        result = _build_prompt(task, "/repo")
        assert isinstance(result, str)
        assert len(result) > 50


# ---------------------------------------------------------------------------
# ExecutionResult dataclass
# ---------------------------------------------------------------------------

class TestExecutionResult:
    def test_default_fields(self):
        r = ExecutionResult(task_id="t1", success=True)
        assert r.files_modified == []
        assert r.summary == ""
        assert r.error == ""
        assert isinstance(r.started_at, datetime)
        assert r.finished_at is None

    def test_failed_result(self):
        r = ExecutionResult(task_id="t2", success=False, error="boom")
        assert not r.success
        assert r.error == "boom"


# ---------------------------------------------------------------------------
# TaskExecutor — SDK not available
# ---------------------------------------------------------------------------

class TestExecutorNoSdk:
    def test_returns_failure_when_sdk_missing(self, config, tmp_path):
        with patch("agent.executor.AGENT_SDK_AVAILABLE", False):
            executor = TaskExecutor(config)
            result = executor.execute(_task(), str(tmp_path))
        assert not result.success
        assert "claude-agent-sdk" in result.error.lower() or "not installed" in result.error.lower()

    def test_task_id_preserved_in_result(self, config, tmp_path):
        task = _task()
        task.id = "my-unique-id"
        with patch("agent.executor.AGENT_SDK_AVAILABLE", False):
            executor = TaskExecutor(config)
            result = executor.execute(task, str(tmp_path))
        assert result.task_id == "my-unique-id"


# ---------------------------------------------------------------------------
# TaskExecutor — SDK available, mocked
# ---------------------------------------------------------------------------

class TestExecutorWithSdk:
    @pytest.fixture(autouse=True)
    def _patch_sdk(self):
        """Patch the Agent SDK so tests don't require real API calls."""
        from agent.identifier import MaintenanceTask

        async def mock_query(prompt, options):
            from claude_agent_sdk import ResultMessage  # type: ignore
            msg = MagicMock(spec=ResultMessage)
            msg.result = "Task completed successfully."
            # yield the mock result
            yield msg

        with patch("agent.executor.AGENT_SDK_AVAILABLE", True), \
             patch("agent.executor.query", mock_query):
            yield

    def test_successful_execution(self, config, tmp_path):
        executor = TaskExecutor(config)
        result = executor.execute(_task(), str(tmp_path))
        assert result.success is True

    def test_summary_populated(self, config, tmp_path):
        executor = TaskExecutor(config)
        result = executor.execute(_task(), str(tmp_path))
        assert isinstance(result.summary, str)

    def test_finished_at_set_on_success(self, config, tmp_path):
        executor = TaskExecutor(config)
        result = executor.execute(_task(), str(tmp_path))
        assert result.finished_at is not None

    def test_files_modified_initially_empty(self, config, tmp_path):
        executor = TaskExecutor(config)
        result = executor.execute(_task(), str(tmp_path))
        # No actual Edit/Write calls in mock, so list should be empty
        assert isinstance(result.files_modified, list)

    def test_executor_exception_returns_failure(self, config, tmp_path):
        async def bad_query(prompt, options):
            raise RuntimeError("API down")
            yield  # make it a generator

        with patch("agent.executor.query", bad_query):
            executor = TaskExecutor(config)
            result = executor.execute(_task(), str(tmp_path))
        assert result.success is False
        assert "API down" in result.error
