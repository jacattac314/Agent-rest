"""
Task Identifier
---------------
Converts raw scanner observations (DocGaps, CodeSmells, DependencyManifests)
into structured MaintenanceTask objects with clear titles, descriptions,
and enough context for the Prioritizer and Executor to act on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .scanner import RepositorySnapshot


class TaskKind(str, Enum):
    ADD_README = "add_readme"
    ADD_MODULE_DOCSTRING = "add_module_docstring"
    ADD_FUNCTION_DOCSTRING = "add_function_docstring"
    ADD_CLASS_DOCSTRING = "add_class_docstring"
    ADD_TYPE_HINTS = "add_type_hints"
    REFACTOR_LONG_FUNCTION = "refactor_long_function"
    ADDRESS_TODO = "address_todo"
    AUDIT_DEPENDENCIES = "audit_dependencies"
    IMPROVE_TEST_COVERAGE = "improve_test_coverage"


@dataclass
class MaintenanceTask:
    id: str
    kind: TaskKind
    title: str
    description: str
    file_path: str
    line: int = 0
    # Filled in by Prioritizer
    impact_score: int = 0      # 1-10: value delivered to devs
    risk_score: int = 0        # 1-10: probability of breaking something
    priority: int = 0          # computed composite score
    # Extra key-value context passed to the Executor
    context: dict[str, Any] = field(default_factory=dict)


class TaskIdentifier:
    """
    Converts a RepositorySnapshot into a deduplicated list of MaintenanceTasks.
    Groups related gaps to avoid task explosion (e.g. batches docstring
    tasks per-file rather than per-function).
    """

    def identify(self, snapshot: RepositorySnapshot) -> list[MaintenanceTask]:
        tasks: list[MaintenanceTask] = []
        seen: set[str] = set()

        tasks.extend(self._readme_tasks(snapshot, seen))
        tasks.extend(self._docstring_tasks(snapshot, seen))
        tasks.extend(self._type_hint_tasks(snapshot, seen))
        tasks.extend(self._long_function_tasks(snapshot, seen))
        tasks.extend(self._todo_tasks(snapshot, seen))
        tasks.extend(self._dep_audit_tasks(snapshot, seen))
        tasks.extend(self._test_coverage_tasks(snapshot, seen))

        return tasks

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _readme_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        tasks = []
        for gap in snapshot.doc_gaps:
            if gap.kind == "missing_readme":
                key = "readme"
                if key not in seen:
                    seen.add(key)
                    tasks.append(MaintenanceTask(
                        id=key,
                        kind=TaskKind.ADD_README,
                        title="Add top-level README.md",
                        description=(
                            "The repository has no README.md. "
                            "Create a concise README covering: project purpose, "
                            "installation instructions, usage examples, and contributing guidelines."
                        ),
                        file_path="README.md",
                        context={"snapshot_summary": snapshot.summary},
                    ))
        return tasks

    def _docstring_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        """Group docstring gaps per file to produce one task per file."""
        from collections import defaultdict
        file_gaps: dict[str, list] = defaultdict(list)
        for gap in snapshot.doc_gaps:
            if gap.kind in ("missing_module_docstring", "missing_function_docstring", "missing_class_docstring"):
                file_gaps[gap.file_path].append(gap)

        tasks = []
        for fpath, gaps in file_gaps.items():
            key = f"docstring:{fpath}"
            if key in seen:
                continue
            seen.add(key)
            missing = [f"  - {g.kind.replace('missing_', '').replace('_', ' ')} '{g.symbol}' (line {g.line})"
                       for g in gaps if g.symbol]
            module_missing = any(g.kind == "missing_module_docstring" for g in gaps)

            details = []
            if module_missing:
                details.append("  - module-level docstring (line 1)")
            details.extend(missing)

            tasks.append(MaintenanceTask(
                id=key,
                kind=TaskKind.ADD_FUNCTION_DOCSTRING,
                title=f"Add missing docstrings in {fpath}",
                description=(
                    f"The following items in `{fpath}` are missing docstrings:\n"
                    + "\n".join(details)
                    + "\n\nAdd concise, accurate docstrings that describe purpose, args, and return values."
                ),
                file_path=fpath,
                line=gaps[0].line,
                context={"gaps": [{"kind": g.kind, "symbol": g.symbol, "line": g.line} for g in gaps]},
            ))
        return tasks

    def _type_hint_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        from collections import defaultdict
        file_smells: dict[str, list] = defaultdict(list)
        for smell in snapshot.code_smells:
            if smell.kind == "missing_type_hints":
                file_smells[smell.file_path].append(smell)

        tasks = []
        for fpath, smells in file_smells.items():
            key = f"type_hints:{fpath}"
            if key in seen:
                continue
            seen.add(key)
            functions = [s.description for s in smells]
            tasks.append(MaintenanceTask(
                id=key,
                kind=TaskKind.ADD_TYPE_HINTS,
                title=f"Add return type annotations in {fpath}",
                description=(
                    f"These functions in `{fpath}` are missing return type annotations:\n"
                    + "\n".join(f"  - {d}" for d in functions)
                    + "\n\nAdd accurate Python type hints. Use `None` for procedures."
                ),
                file_path=fpath,
                line=smells[0].line,
                context={"functions": functions},
            ))
        return tasks

    def _long_function_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        tasks = []
        for smell in snapshot.code_smells:
            if smell.kind == "long_function":
                key = f"long_fn:{smell.file_path}:{smell.line}"
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(MaintenanceTask(
                    id=key,
                    kind=TaskKind.REFACTOR_LONG_FUNCTION,
                    title=f"Refactor long function in {smell.file_path}:{smell.line}",
                    description=(
                        f"`{smell.description}` in `{smell.file_path}`. "
                        "Consider extracting cohesive blocks into well-named helper functions "
                        "to improve readability and testability. "
                        "Do NOT change observable behaviour or public interfaces."
                    ),
                    file_path=smell.file_path,
                    line=smell.line,
                ))
        return tasks

    def _todo_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        """Surface the first 10 distinct TODOs as tasks."""
        tasks = []
        for smell in snapshot.code_smells:
            if smell.kind == "todo" and len(tasks) < 10:
                key = f"todo:{smell.file_path}:{smell.line}"
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(MaintenanceTask(
                    id=key,
                    kind=TaskKind.ADDRESS_TODO,
                    title=f"Address TODO in {smell.file_path}:{smell.line}",
                    description=(
                        f"Found `{smell.description}` at `{smell.file_path}:{smell.line}`. "
                        "Implement the requested change, or remove the comment if already done."
                    ),
                    file_path=smell.file_path,
                    line=smell.line,
                ))
        return tasks

    def _dep_audit_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        tasks = []
        for manifest in snapshot.dep_manifests:
            key = f"deps:{manifest.file_path}"
            if key in seen:
                continue
            seen.add(key)
            tasks.append(MaintenanceTask(
                id=key,
                kind=TaskKind.AUDIT_DEPENDENCIES,
                title=f"Audit dependencies in {manifest.file_path}",
                description=(
                    f"Review `{manifest.file_path}` ({manifest.manager}) for "
                    "unpinned versions, deprecated packages, or known security advisories. "
                    "Recommend specific version pins or updates with rationale."
                ),
                file_path=manifest.file_path,
                context={"manager": manifest.manager, "content": manifest.raw_content[:2000]},
            ))
        return tasks

    def _test_coverage_tasks(self, snapshot: RepositorySnapshot, seen: set) -> list[MaintenanceTask]:
        tasks = []
        if snapshot.test_ratio < 0.2:
            key = "test_coverage"
            if key not in seen:
                seen.add(key)
                tasks.append(MaintenanceTask(
                    id=key,
                    kind=TaskKind.IMPROVE_TEST_COVERAGE,
                    title="Improve test coverage (test ratio is low)",
                    description=(
                        f"Only {snapshot.test_ratio:.0%} of source files have corresponding tests. "
                        "Identify the most critical untested modules and add unit tests covering "
                        "happy paths, edge cases, and error conditions."
                    ),
                    file_path="",
                    context={"test_ratio": snapshot.test_ratio},
                ))
        return tasks
