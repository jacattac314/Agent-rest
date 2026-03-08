"""
Tests for agent.identifier — TaskIdentifier
"""
from __future__ import annotations

from datetime import datetime

import pytest

from agent.identifier import MaintenanceTask, TaskIdentifier, TaskKind
from agent.scanner import CodeSmell, DependencyManifest, DocGap, RepositorySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(**kwargs) -> RepositorySnapshot:
    defaults = dict(
        root="/tmp/repo",
        scanned_at=datetime.utcnow(),
        files=[],
        doc_gaps=[],
        code_smells=[],
        dep_manifests=[],
        test_ratio=0.5,
        summary={},
    )
    defaults.update(kwargs)
    return RepositorySnapshot(**defaults)


def _doc_gap(kind, symbol="foo", file_path="mod.py", line=1):
    return DocGap(file_path=file_path, kind=kind, symbol=symbol, line=line)


def _smell(kind, desc="desc", file_path="mod.py", line=5):
    return CodeSmell(file_path=file_path, kind=kind, description=desc, line=line)


def _manifest(file_path="requirements.txt", manager="pip"):
    return DependencyManifest(file_path=file_path, manager=manager, raw_content="requests>=2")


# ---------------------------------------------------------------------------
# README tasks
# ---------------------------------------------------------------------------

class TestReadmeTasks:
    def test_missing_readme_creates_task(self):
        snap = _snapshot(doc_gaps=[_doc_gap("missing_readme", symbol="")])
        tasks = TaskIdentifier().identify(snap)
        kinds = [t.kind for t in tasks]
        assert TaskKind.ADD_README in kinds

    def test_readme_task_deduplicated(self):
        gaps = [_doc_gap("missing_readme", symbol=""), _doc_gap("missing_readme", symbol="")]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        readme_tasks = [t for t in tasks if t.kind == TaskKind.ADD_README]
        assert len(readme_tasks) == 1

    def test_no_readme_task_without_gap(self):
        snap = _snapshot()
        tasks = TaskIdentifier().identify(snap)
        assert all(t.kind != TaskKind.ADD_README for t in tasks)


# ---------------------------------------------------------------------------
# Docstring tasks
# ---------------------------------------------------------------------------

class TestDocstringTasks:
    def test_function_docstring_gap_creates_task(self):
        gaps = [_doc_gap("missing_function_docstring", symbol="do_thing")]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.ADD_FUNCTION_DOCSTRING for t in tasks)

    def test_module_docstring_gap_included_in_task(self):
        gaps = [_doc_gap("missing_module_docstring", symbol="")]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.ADD_FUNCTION_DOCSTRING for t in tasks)

    def test_docstrings_grouped_per_file(self):
        gaps = [
            _doc_gap("missing_function_docstring", symbol="a", file_path="m.py"),
            _doc_gap("missing_function_docstring", symbol="b", file_path="m.py"),
        ]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        docstring_tasks = [t for t in tasks if t.kind == TaskKind.ADD_FUNCTION_DOCSTRING]
        assert len(docstring_tasks) == 1

    def test_docstrings_separate_per_file(self):
        gaps = [
            _doc_gap("missing_function_docstring", symbol="a", file_path="a.py"),
            _doc_gap("missing_function_docstring", symbol="b", file_path="b.py"),
        ]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        docstring_tasks = [t for t in tasks if t.kind == TaskKind.ADD_FUNCTION_DOCSTRING]
        assert len(docstring_tasks) == 2

    def test_task_file_path_matches_gap(self):
        gaps = [_doc_gap("missing_function_docstring", symbol="fn", file_path="special.py")]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.file_path == "special.py" for t in tasks)

    def test_task_context_contains_gaps(self):
        gaps = [_doc_gap("missing_function_docstring", symbol="fn")]
        snap = _snapshot(doc_gaps=gaps)
        tasks = TaskIdentifier().identify(snap)
        task = next(t for t in tasks if t.kind == TaskKind.ADD_FUNCTION_DOCSTRING)
        assert "gaps" in task.context


# ---------------------------------------------------------------------------
# Type hint tasks
# ---------------------------------------------------------------------------

class TestTypeHintTasks:
    def test_missing_type_hints_creates_task(self):
        smells = [_smell("missing_type_hints", "Function 'foo' has no return type annotation")]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.ADD_TYPE_HINTS for t in tasks)

    def test_type_hints_grouped_per_file(self):
        smells = [
            _smell("missing_type_hints", "Function 'a'", file_path="m.py"),
            _smell("missing_type_hints", "Function 'b'", file_path="m.py"),
        ]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        hint_tasks = [t for t in tasks if t.kind == TaskKind.ADD_TYPE_HINTS]
        assert len(hint_tasks) == 1

    def test_type_hints_separate_per_file(self):
        smells = [
            _smell("missing_type_hints", "Function 'a'", file_path="x.py"),
            _smell("missing_type_hints", "Function 'b'", file_path="y.py"),
        ]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        hint_tasks = [t for t in tasks if t.kind == TaskKind.ADD_TYPE_HINTS]
        assert len(hint_tasks) == 2


# ---------------------------------------------------------------------------
# Long function tasks
# ---------------------------------------------------------------------------

class TestLongFunctionTasks:
    def test_long_function_creates_task(self):
        smells = [_smell("long_function", "Function 'huge' is 70 lines long (>60)")]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.REFACTOR_LONG_FUNCTION for t in tasks)

    def test_long_function_task_includes_file_path(self):
        smells = [_smell("long_function", "Function 'huge'", file_path="big.py", line=10)]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        task = next(t for t in tasks if t.kind == TaskKind.REFACTOR_LONG_FUNCTION)
        assert task.file_path == "big.py"
        assert task.line == 10

    def test_each_long_function_separate_task(self):
        smells = [
            _smell("long_function", "Function 'a' is 70 lines", file_path="m.py", line=1),
            _smell("long_function", "Function 'b' is 80 lines", file_path="m.py", line=100),
        ]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        long_tasks = [t for t in tasks if t.kind == TaskKind.REFACTOR_LONG_FUNCTION]
        assert len(long_tasks) == 2


# ---------------------------------------------------------------------------
# TODO tasks
# ---------------------------------------------------------------------------

class TestTodoTasks:
    def test_todo_creates_task(self):
        smells = [_smell("todo", "TODO: fix this")]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.ADDRESS_TODO for t in tasks)

    def test_todo_tasks_capped_at_10(self):
        smells = [_smell("todo", f"TODO: thing {i}", line=i) for i in range(20)]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        todo_tasks = [t for t in tasks if t.kind == TaskKind.ADDRESS_TODO]
        assert len(todo_tasks) <= 10

    def test_todo_task_has_correct_file_path(self):
        smells = [_smell("todo", "TODO: fix", file_path="svc.py", line=42)]
        snap = _snapshot(code_smells=smells)
        tasks = TaskIdentifier().identify(snap)
        task = next(t for t in tasks if t.kind == TaskKind.ADDRESS_TODO)
        assert task.file_path == "svc.py"
        assert task.line == 42


# ---------------------------------------------------------------------------
# Dependency audit tasks
# ---------------------------------------------------------------------------

class TestDepAuditTasks:
    def test_manifest_creates_audit_task(self):
        snap = _snapshot(dep_manifests=[_manifest()])
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.AUDIT_DEPENDENCIES for t in tasks)

    def test_manifests_deduplicated(self):
        snap = _snapshot(dep_manifests=[_manifest(), _manifest()])
        tasks = TaskIdentifier().identify(snap)
        audit_tasks = [t for t in tasks if t.kind == TaskKind.AUDIT_DEPENDENCIES]
        assert len(audit_tasks) == 1

    def test_each_manifest_separate_task(self):
        snap = _snapshot(dep_manifests=[
            _manifest("requirements.txt", "pip"),
            _manifest("package.json", "npm"),
        ])
        tasks = TaskIdentifier().identify(snap)
        audit_tasks = [t for t in tasks if t.kind == TaskKind.AUDIT_DEPENDENCIES]
        assert len(audit_tasks) == 2

    def test_audit_task_context_has_manager(self):
        snap = _snapshot(dep_manifests=[_manifest("requirements.txt", "pip")])
        tasks = TaskIdentifier().identify(snap)
        task = next(t for t in tasks if t.kind == TaskKind.AUDIT_DEPENDENCIES)
        assert task.context.get("manager") == "pip"


# ---------------------------------------------------------------------------
# Test coverage tasks
# ---------------------------------------------------------------------------

class TestCoverageTask:
    def test_low_ratio_creates_task(self):
        snap = _snapshot(test_ratio=0.0)
        tasks = TaskIdentifier().identify(snap)
        assert any(t.kind == TaskKind.IMPROVE_TEST_COVERAGE for t in tasks)

    def test_sufficient_ratio_no_task(self):
        snap = _snapshot(test_ratio=0.5)
        tasks = TaskIdentifier().identify(snap)
        assert all(t.kind != TaskKind.IMPROVE_TEST_COVERAGE for t in tasks)

    def test_coverage_task_deduplicated(self):
        snap = _snapshot(test_ratio=0.0)
        tasks = TaskIdentifier().identify(snap)
        cov_tasks = [t for t in tasks if t.kind == TaskKind.IMPROVE_TEST_COVERAGE]
        assert len(cov_tasks) == 1

    def test_coverage_task_context_has_ratio(self):
        snap = _snapshot(test_ratio=0.1)
        tasks = TaskIdentifier().identify(snap)
        task = next(t for t in tasks if t.kind == TaskKind.IMPROVE_TEST_COVERAGE)
        assert task.context.get("test_ratio") == 0.1


# ---------------------------------------------------------------------------
# MaintenanceTask dataclass
# ---------------------------------------------------------------------------

class TestMaintenanceTaskDefaults:
    def test_default_scores_zero(self):
        task = MaintenanceTask(
            id="t1",
            kind=TaskKind.ADD_README,
            title="test",
            description="desc",
            file_path="",
        )
        assert task.impact_score == 0
        assert task.risk_score == 0
        assert task.priority == 0

    def test_context_default_empty_dict(self):
        task = MaintenanceTask(
            id="t2",
            kind=TaskKind.ADD_README,
            title="test",
            description="desc",
            file_path="",
        )
        assert task.context == {}
