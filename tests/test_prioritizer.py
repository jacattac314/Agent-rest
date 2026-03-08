"""
Tests for agent.prioritizer — TaskPrioritizer (Claude API mocked)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.identifier import MaintenanceTask, TaskKind
from agent.prioritizer import TaskPrioritizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "agent": {
            "model": "claude-opus-4-6",
            "max_turns": 5,
            "max_budget_usd": 1.0,
        }
    }


def _task(id_: str, kind=TaskKind.ADD_README, title="T", desc="D") -> MaintenanceTask:
    return MaintenanceTask(id=id_, kind=kind, title=title, description=desc, file_path="")


def _make_claude_response(scores: list[dict]) -> MagicMock:
    """Build a mock that mimics the anthropic streaming response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps(scores)

    final_msg = MagicMock()
    final_msg.content = [text_block]

    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
    stream_ctx.__exit__ = MagicMock(return_value=False)
    stream_ctx.get_final_message = MagicMock(return_value=final_msg)

    return stream_ctx


# ---------------------------------------------------------------------------
# Tests — normal operation
# ---------------------------------------------------------------------------

class TestPrioritize:
    def test_empty_list_returns_empty(self, config):
        p = TaskPrioritizer(config)
        assert p.prioritize([]) == []

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_scores_applied(self, mock_anthropic_cls, config):
        scores = [{"id": "t1", "impact_score": 7, "risk_score": 2}]
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_claude_response(scores)

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)

        assert result[0].impact_score == 7
        assert result[0].risk_score == 2

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_priority_computed_correctly(self, mock_anthropic_cls, config):
        scores = [{"id": "t1", "impact_score": 8, "risk_score": 3}]
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_claude_response(scores)

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)

        # priority = impact * (10 - risk) = 8 * 7 = 56
        assert result[0].priority == 56

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_sorted_descending_by_priority(self, mock_anthropic_cls, config):
        scores = [
            {"id": "t1", "impact_score": 3, "risk_score": 1},
            {"id": "t2", "impact_score": 9, "risk_score": 1},
        ]
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_claude_response(scores)

        tasks = [_task("t1"), _task("t2")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)

        assert result[0].id == "t2"
        assert result[1].id == "t1"

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_unknown_task_id_gets_defaults(self, mock_anthropic_cls, config):
        # Claude returns scores for a different id
        scores = [{"id": "other", "impact_score": 9, "risk_score": 1}]
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_claude_response(scores)

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)

        # Falls back to default=5
        assert result[0].impact_score == 5
        assert result[0].risk_score == 5

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_json_in_markdown_fence_stripped(self, mock_anthropic_cls, config):
        raw_scores = [{"id": "t1", "impact_score": 6, "risk_score": 2}]

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "```json\n" + json.dumps(raw_scores) + "\n```"

        final_msg = MagicMock()
        final_msg.content = [text_block]

        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
        stream_ctx.__exit__ = MagicMock(return_value=False)
        stream_ctx.get_final_message = MagicMock(return_value=final_msg)

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = stream_ctx

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)
        assert result[0].impact_score == 6


# ---------------------------------------------------------------------------
# Tests — error / fallback paths
# ---------------------------------------------------------------------------

class TestPrioritizerFallback:
    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_api_error_falls_back_to_defaults(self, mock_anthropic_cls, config):
        import anthropic as anthropic_mod
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.side_effect = anthropic_mod.APIStatusError(
            "rate limited", response=MagicMock(status_code=429), body={}
        )

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)

        # Should not raise; scores should be default 5
        assert len(result) == 1
        assert result[0].impact_score == 5
        assert result[0].risk_score == 5

    @patch("agent.prioritizer.anthropic.Anthropic")
    def test_invalid_json_falls_back_to_defaults(self, mock_anthropic_cls, config):
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "not json at all"

        final_msg = MagicMock()
        final_msg.content = [text_block]

        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
        stream_ctx.__exit__ = MagicMock(return_value=False)
        stream_ctx.get_final_message = MagicMock(return_value=final_msg)

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = stream_ctx

        tasks = [_task("t1")]
        p = TaskPrioritizer(config)
        result = p.prioritize(tasks)
        assert result[0].impact_score == 5
