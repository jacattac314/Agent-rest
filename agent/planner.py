"""
Planner Agent
-------------
Translates a RepositorySnapshot into a structured task plan and writes it to
the SharedState.  Implements the "Planner" role in the multi-agent hierarchy:

  User/Orchestrator → Planner → SharedState.plan (ordered TaskNode list)

The Planner uses the existing TaskIdentifier + TaskPrioritizer pipeline to
produce a scored, ordered list of tasks, then persists that plan so that
downstream Executor and Verifier agents can work from a single source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from .identifier import TaskIdentifier, MaintenanceTask
from .prioritizer import TaskPrioritizer
from .scanner import RepositorySnapshot
from .state import AgentRole, SharedState, TaskNode, TaskStatus

logger = logging.getLogger(__name__)

PLANNER_ID = "planner-001"


class PlannerAgent:
    """
    Decomposes a repository health snapshot into an ordered plan of
    MaintenanceTasks and writes the plan into the shared session state.

    Responsibilities
    ----------------
    * Run TaskIdentifier to find all doc gaps, smells, and dependency issues.
    * Run TaskPrioritizer (Claude API) to score impact/risk.
    * Convert scored MaintenanceTasks into TaskNode objects.
    * Write the ordered plan to state and log a planning message.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._identifier = TaskIdentifier()
        self._prioritizer = TaskPrioritizer(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, snapshot: RepositorySnapshot, state: SharedState) -> list[TaskNode]:
        """
        Build a task plan from *snapshot* and persist it in *state*.

        Returns the ordered list of TaskNode objects (highest priority first).
        """
        state.transition(state.status.__class__("planning"))  # SessionStatus.PLANNING

        state.add_message(
            PLANNER_ID,
            AgentRole.PLANNER,
            f"Starting planning phase for {snapshot.root}. "
            f"Found {len(snapshot.doc_gaps)} doc gaps and "
            f"{len(snapshot.code_smells)} code smells.",
        )

        # 1. Identify raw tasks
        raw_tasks: list[MaintenanceTask] = self._identifier.identify(snapshot)
        logger.info("[planner] identified %d raw tasks", len(raw_tasks))

        # 2. Prioritize (Claude API scoring)
        try:
            scored: list[MaintenanceTask] = self._prioritizer.prioritize(raw_tasks)
        except Exception as exc:
            logger.warning("[planner] prioritizer failed (%s), using raw order", exc)
            scored = raw_tasks

        # 3. Convert to TaskNode objects
        nodes: list[TaskNode] = [_task_to_node(t) for t in scored]

        # 4. Write plan into shared state
        state.set_plan(nodes)

        state.add_message(
            PLANNER_ID,
            AgentRole.PLANNER,
            f"Plan complete: {len(nodes)} tasks queued "
            f"(top priority: {nodes[0].title!r} if nodes else 'none').",
            task_count=len(nodes),
        )

        state.record_metric("plan_size", len(nodes))
        logger.info("[planner] plan written to state: %d tasks", len(nodes))
        return nodes


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _task_to_node(task: MaintenanceTask) -> TaskNode:
    return TaskNode(
        task_id=task.id,
        kind=task.kind.value,
        title=task.title,
        description=task.description,
        file_path=task.file_path or "",
        line=task.line or 0,
        impact_score=task.impact_score,
        risk_score=task.risk_score,
        priority=task.priority,
        status=TaskStatus.PENDING,
    )
