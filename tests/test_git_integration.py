"""
Tests for agent.git_integration — GitIntegration
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.git_integration import CommitInfo, GitIntegration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "git": {
            "auto_commit": True,
            "branch_prefix": "agent/maintenance",
            "author_name": "Test Agent",
            "author_email": "agent@test.local",
        }
    }


@pytest.fixture
def git(config):
    return GitIntegration(config)


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestGitIntegrationConfig:
    def test_auto_commit_default_true(self, git):
        assert git.auto_commit is True

    def test_branch_prefix(self, git):
        assert git.branch_prefix == "agent/maintenance"

    def test_author_name(self, git):
        assert git.author_name == "Test Agent"

    def test_author_email(self, git):
        assert git.author_email == "agent@test.local"

    def test_auto_commit_false_from_config(self):
        cfg = {"git": {"auto_commit": False, "branch_prefix": "p", "author_name": "A", "author_email": "a@b"}}
        gi = GitIntegration(cfg)
        assert gi.auto_commit is False


# ---------------------------------------------------------------------------
# commit_task — auto_commit disabled
# ---------------------------------------------------------------------------

class TestCommitTaskDisabled:
    def test_returns_none_when_auto_commit_false(self, tmp_path):
        cfg = {"git": {"auto_commit": False, "branch_prefix": "p", "author_name": "A", "author_email": "a@b"}}
        gi = GitIntegration(cfg)
        result = gi.commit_task(str(tmp_path), "t1", "title", ["f.py"], "summary")
        assert result is None


# ---------------------------------------------------------------------------
# commit_task — git not available
# ---------------------------------------------------------------------------

class TestCommitTaskNoGit:
    def test_returns_none_when_git_unavailable(self, config, tmp_path):
        with patch("agent.git_integration.GIT_AVAILABLE", False):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "t1", "title", ["f.py"], "summary")
        assert result is None


# ---------------------------------------------------------------------------
# commit_task — empty files list
# ---------------------------------------------------------------------------

class TestCommitTaskNoFiles:
    def test_returns_none_for_empty_files(self, config, tmp_path):
        gi = GitIntegration(config)
        result = gi.commit_task(str(tmp_path), "t1", "title", [], "summary")
        assert result is None


# ---------------------------------------------------------------------------
# commit_task — not a git repo
# ---------------------------------------------------------------------------

class TestCommitTaskNotRepo:
    def test_returns_none_when_not_git_repo(self, config, tmp_path):
        from git import InvalidGitRepositoryError

        with patch("agent.git_integration.Repo", side_effect=InvalidGitRepositoryError("no git")):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "t1", "title", ["f.py"], "summary")
        assert result is None


# ---------------------------------------------------------------------------
# commit_task — successful commit (fully mocked git repo)
# ---------------------------------------------------------------------------

class TestCommitTaskSuccess:
    def _make_mock_repo(self, tmp_path: Path, files: list[str]):
        """Build a minimal mock Repo that fakes a successful commit."""
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc1234567890"

        mock_index = MagicMock()
        mock_index.diff.return_value = [MagicMock()]  # staged diff non-empty
        mock_index.commit.return_value = mock_commit

        mock_branch = MagicMock()
        mock_branch.name = "main"
        mock_branch.checkout = MagicMock()

        mock_new_branch = MagicMock()
        mock_new_branch.checkout = MagicMock()

        mock_repo = MagicMock()
        mock_repo.working_tree_dir = str(tmp_path)
        mock_repo.index = mock_index
        mock_repo.active_branch = mock_branch
        mock_repo.heads = {mock_branch.name: mock_branch}
        mock_repo.untracked_files = []
        mock_repo.create_head.return_value = mock_new_branch

        return mock_repo

    def test_returns_commit_info(self, config, tmp_path):
        # Create actual file so existence check passes
        f = tmp_path / "module.py"
        f.write_text("x = 1\n")

        mock_repo = self._make_mock_repo(tmp_path, ["module.py"])

        with patch("agent.git_integration.GIT_AVAILABLE", True), \
             patch("agent.git_integration.Repo", return_value=mock_repo):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "task-id", "Add docs", ["module.py"], "Done.")

        assert isinstance(result, CommitInfo)

    def test_branch_has_prefix(self, config, tmp_path):
        f = tmp_path / "module.py"
        f.write_text("x = 1\n")

        mock_repo = self._make_mock_repo(tmp_path, ["module.py"])

        with patch("agent.git_integration.GIT_AVAILABLE", True), \
             patch("agent.git_integration.Repo", return_value=mock_repo):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "task-id", "Add docs", ["module.py"], "Done.")

        assert result is not None
        assert result.branch.startswith("agent/maintenance/")

    def test_commit_sha_in_result(self, config, tmp_path):
        f = tmp_path / "module.py"
        f.write_text("x = 1\n")

        mock_repo = self._make_mock_repo(tmp_path, ["module.py"])

        with patch("agent.git_integration.GIT_AVAILABLE", True), \
             patch("agent.git_integration.Repo", return_value=mock_repo):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "task-id", "Add docs", ["module.py"], "Done.")

        assert result is not None
        assert result.commit_sha == "abc1234567890"

    def test_nonexistent_files_skipped(self, config, tmp_path):
        """Files listed but not on disk should be quietly skipped."""
        # No files created in tmp_path
        mock_repo = self._make_mock_repo(tmp_path, [])
        mock_repo.index.diff.return_value = []  # nothing staged

        with patch("agent.git_integration.GIT_AVAILABLE", True), \
             patch("agent.git_integration.Repo", return_value=mock_repo):
            gi = GitIntegration(config)
            result = gi.commit_task(str(tmp_path), "task-id", "Title", ["ghost.py"], "Done.")

        # No existing files → nothing to stage → returns None
        assert result is None


# ---------------------------------------------------------------------------
# CommitInfo dataclass
# ---------------------------------------------------------------------------

class TestCommitInfo:
    def test_fields(self):
        ci = CommitInfo(
            branch="agent/maintenance/abc-123",
            commit_sha="deadbeef",
            files_committed=["a.py"],
            message="chore: do thing",
        )
        assert ci.branch == "agent/maintenance/abc-123"
        assert ci.commit_sha == "deadbeef"
        assert ci.files_committed == ["a.py"]
        assert ci.message == "chore: do thing"
