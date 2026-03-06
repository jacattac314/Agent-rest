"""
Bill — Task Execution Narrator
--------------------------------
Bill is the gruff, dependable maintenance worker who runs the execution side
of the agent.  Where Terrance delivers polished morning briefings, Bill rolls
up his sleeves, digs into the code, and tells you exactly what he found and
what he did about it — in as few words as possible.

Personality:
  - Blue-collar, workmanlike — speaks like a seasoned tradesman
  - Proud of clean, documented code; grumbles loudly about technical debt
  - Uses repair / plumbing / construction metaphors naturally
  - Firm on risk: anything above the threshold is "above his pay grade"
  - Never wastes words; every sentence earns its place
  - Ends with a terse but satisfied (or disgusted) summary

Bill is called by the executor and scheduler to produce console-level
commentary on each task as it runs and on the overall cycle result.
"""

from __future__ import annotations

from .identifier import TaskKind

# ---------------------------------------------------------------------------
# Task-start commentary
# ---------------------------------------------------------------------------

_KIND_OPENERS: dict[str, str] = {
    TaskKind.ADD_README.value: (
        "No README? Classic. Alright, I'll write one. "
        "Give me a minute to look around first."
    ),
    TaskKind.ADD_FUNCTION_DOCSTRING.value: (
        "Mystery function with no docs. "
        "Let's label this pipe before someone calls the wrong thing."
    ),
    TaskKind.ADD_MODULE_DOCSTRING.value: (
        "Module's got no header. "
        "Like a fuse box with no labels — fixing that right now."
    ),
    TaskKind.ADD_CLASS_DOCSTRING.value: (
        "Class with no docstring. "
        "Nobody knows what this does. Not for long."
    ),
    TaskKind.ADD_TYPE_HINTS.value: (
        "No return type on this function. "
        "Putting the signs up so nobody guesses wrong."
    ),
    TaskKind.RESOLVE_TODO.value: (
        "Found a TODO somebody left behind. "
        "I hate unfinished work. Let's close it out."
    ),
    TaskKind.REFACTOR_LONG_FUNCTION.value: (
        "This function's longer than my commute. "
        "I'll tidy it up but I won't rewrite the whole house."
    ),
    TaskKind.AUDIT_DEPENDENCIES.value: (
        "Time to check what's lurking in the supply closet. "
        "Unpinned packages give me the creeps."
    ),
    TaskKind.ADD_TESTS.value: (
        "No tests? That's a load-bearing wall with no inspection. "
        "Writing a basic check so at least something's verified."
    ),
}

_DEFAULT_OPENER = "Alright. Let's see what we've got here."


def task_start(task_kind: str, task_title: str, file_path: str | None = None) -> str:
    """
    Return Bill's opening line when he picks up a new task.
    """
    opener = _KIND_OPENERS.get(task_kind, _DEFAULT_OPENER)
    location = f"  File: {file_path}" if file_path else ""
    lines = [
        "",
        "─" * 56,
        f"  Bill: {opener}",
        f"  Job : {task_title}",
    ]
    if location:
        lines.append(location)
    lines.append("─" * 56)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task-finish commentary
# ---------------------------------------------------------------------------

_KIND_CLOSERS_SUCCESS: dict[str, str] = {
    TaskKind.ADD_README.value: "README's done. Clean, readable. You're welcome.",
    TaskKind.ADD_FUNCTION_DOCSTRING.value: "Docstring's in. Next person who reads this will know exactly what's happening.",
    TaskKind.ADD_MODULE_DOCSTRING.value: "Header's on the module. Labelled the fuse box.",
    TaskKind.ADD_CLASS_DOCSTRING.value: "Class has docs now. Mystery solved.",
    TaskKind.ADD_TYPE_HINTS.value: "Types are annotated. Signs are up.",
    TaskKind.RESOLVE_TODO.value: "TODO's gone. Finished what they started.",
    TaskKind.REFACTOR_LONG_FUNCTION.value: "Trimmed it down. Still does the job, just cleaner.",
    TaskKind.AUDIT_DEPENDENCIES.value: "Audit report written. Now you know what's in the closet.",
    TaskKind.ADD_TESTS.value: "Test is in. One less unverified load-bearing wall.",
}

_KIND_CLOSERS_FAILURE: dict[str, str] = {
    TaskKind.ADD_README.value: "Couldn't finish the README. Something went sideways — check the logs.",
    TaskKind.ADD_FUNCTION_DOCSTRING.value: "Docstring job failed. File might have changed mid-run.",
    TaskKind.ADD_MODULE_DOCSTRING.value: "Module header job hit a snag. Needs a look.",
    TaskKind.ADD_CLASS_DOCSTRING.value: "Class docs didn't land. Check the error.",
    TaskKind.ADD_TYPE_HINTS.value: "Type hints didn't go in cleanly. Something's off.",
    TaskKind.RESOLVE_TODO.value: "Couldn't resolve the TODO. Didn't touch it.",
    TaskKind.REFACTOR_LONG_FUNCTION.value: "Refactor stalled. Not touching it if I can't do it right.",
    TaskKind.AUDIT_DEPENDENCIES.value: "Dependency audit ran into trouble. Manifest might be malformed.",
    TaskKind.ADD_TESTS.value: "Test job failed. Left the file as-is.",
}

_DEFAULT_CLOSER_SUCCESS = "Done. Moved on."
_DEFAULT_CLOSER_FAILURE = "Hit a wall. Logged the error. Not guessing my way through this one."


def task_finish(
    task_kind: str,
    success: bool,
    files_modified: list[str],
    summary: str = "",
    error: str = "",
) -> str:
    """
    Return Bill's closing line after a task completes (or fails).
    """
    if success:
        closer = _KIND_CLOSERS_SUCCESS.get(task_kind, _DEFAULT_CLOSER_SUCCESS)
        files_line = (
            f"  Modified : {', '.join(files_modified)}"
            if files_modified
            else "  Modified : (no files recorded)"
        )
        lines = [
            f"  Bill: {closer}",
            files_line,
        ]
        if summary:
            # Wrap long summaries at 72 chars
            wrapped = summary[:144] + ("…" if len(summary) > 144 else "")
            lines.append(f"  Summary  : {wrapped}")
    else:
        closer = _KIND_CLOSERS_FAILURE.get(task_kind, _DEFAULT_CLOSER_FAILURE)
        lines = [f"  Bill: {closer}"]
        if error:
            lines.append(f"  Error    : {error[:120]}")

    lines.append("─" * 56)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deferred-task commentary (high-risk tasks Bill refuses to touch)
# ---------------------------------------------------------------------------

def deferred_task(task_title: str, risk_score: int) -> str:
    """
    Return Bill's reaction to a task that's too risky for auto-execution.
    """
    return (
        f"  Bill: Risk score {risk_score}/10 on '{task_title}'. "
        "That's above my pay grade. Flagged for human review — not touching it."
    )


# ---------------------------------------------------------------------------
# Cycle-start and cycle-end banners
# ---------------------------------------------------------------------------

def cycle_start(repo_root: str, task_count: int) -> str:
    """Banner Bill prints when a maintenance cycle kicks off."""
    lines = [
        "",
        "=" * 56,
        "  Bill's on the clock.",
        f"  Repo  : {repo_root}",
        f"  Queue : {task_count} job(s) approved",
        "=" * 56,
        "",
    ]
    return "\n".join(lines)


def cycle_end(succeeded: int, failed: int, deferred: int) -> str:
    """Bill's end-of-cycle wrap-up."""
    total = succeeded + failed
    if total == 0 and deferred == 0:
        closing = "Nothing to do. Repo's in decent shape."
    elif failed == 0 and deferred == 0:
        closing = f"All {succeeded} job(s) done. Clean sweep."
    elif failed == 0:
        closing = (
            f"{succeeded} job(s) done. "
            f"{deferred} flagged for human review — not my call."
        )
    elif succeeded == 0:
        closing = f"Rough cycle. All {failed} job(s) failed. Check the logs."
    else:
        closing = (
            f"{succeeded} done, {failed} failed, {deferred} deferred. "
            "Could be worse."
        )

    lines = [
        "",
        "=" * 56,
        f"  Bill: {closing}",
        "=" * 56,
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# No-tasks-found commentary
# ---------------------------------------------------------------------------

def nothing_to_do() -> str:
    """What Bill says when the scanner finds nothing worth fixing."""
    return (
        "\n"
        "  Bill: Walked the whole codebase. "
        "Nothing below risk threshold worth touching right now.\n"
        "  Come back later.\n"
    )
