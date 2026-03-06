"""
Multi-Agent Shared State
------------------------
Central state object shared across all agents in the maintenance pipeline.
Models a task graph with status transitions and structured message passing
between the Planner, Executor, Verifier, and Orchestrator roles.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"
    SKIPPED = "skipped"


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AgentMessage:
    """A structured message produced by an agent and appended to the session log."""
    message_id: str
    agent_id: str
    role: AgentRole
    content: str
    timestamp: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(cls, agent_id: str, role: AgentRole, content: str, **meta) -> "AgentMessage":
        return cls(
            message_id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=meta,
        )


@dataclass
class TaskNode:
    """A single node in the task execution graph."""
    task_id: str
    kind: str
    title: str
    description: str
    file_path: str
    line: int
    impact_score: int
    risk_score: int
    priority: int
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: str = ""          # agent_id that owns this task
    started_at: str = ""
    completed_at: str = ""
    result: dict = field(default_factory=dict)   # execution result
    verification: dict = field(default_factory=dict)  # verifier result

    def start(self, agent_id: str) -> None:
        self.status = TaskStatus.IN_PROGRESS
        self.assigned_to = agent_id
        self.started_at = datetime.now(timezone.utc).isoformat()

    def complete(self, result: dict) -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.result = result

    def fail(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.result = {"error": error}

    def defer(self, reason: str) -> None:
        self.status = TaskStatus.DEFERRED
        self.result = {"reason": reason}


@dataclass
class SharedState:
    """
    The central state object for one maintenance session.

    All agents read from and write to this object through well-defined
    mutator methods, ensuring a single source of truth for the session.
    """
    session_id: str
    repo_root: str
    goal: str
    status: SessionStatus = SessionStatus.CREATED
    plan: list = field(default_factory=list)      # List[TaskNode] (serialised as dicts)
    messages: list = field(default_factory=list)  # List[AgentMessage]
    metrics: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def new_session(cls, repo_root: str, goal: str = "repository maintenance") -> "SharedState":
        return cls(
            session_id=str(uuid.uuid4())[:12],
            repo_root=repo_root,
            goal=goal,
        )

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def transition(self, status: SessionStatus) -> None:
        self.status = status
        self._touch()

    # ------------------------------------------------------------------
    # Plan management
    # ------------------------------------------------------------------

    def set_plan(self, tasks: list[TaskNode]) -> None:
        self.plan = [asdict(t) for t in tasks]
        self._touch()

    def get_task(self, task_id: str) -> dict | None:
        for t in self.plan:
            if t["task_id"] == task_id:
                return t
        return None

    def update_task(self, task_id: str, **updates: Any) -> None:
        for t in self.plan:
            if t["task_id"] == task_id:
                t.update(updates)
                self._touch()
                return

    def get_pending_tasks(self) -> list[dict]:
        return [t for t in self.plan if t["status"] == TaskStatus.PENDING]

    def get_tasks_by_status(self, status: TaskStatus) -> list[dict]:
        return [t for t in self.plan if t["status"] == status]

    # ------------------------------------------------------------------
    # Message log
    # ------------------------------------------------------------------

    def add_message(self, agent_id: str, role: AgentRole, content: str, **meta) -> AgentMessage:
        msg = AgentMessage.create(agent_id, role, content, **meta)
        self.messages.append(asdict(msg))
        self._touch()
        return msg

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def record_metric(self, key: str, value: Any) -> None:
        self.metrics[key] = value
        self._touch()

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    def plan_summary(self) -> dict:
        counts: dict[str, int] = {}
        for t in self.plan:
            s = t.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)  # type: ignore[return-value]

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "SharedState":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        state = cls(
            session_id=data["session_id"],
            repo_root=data["repo_root"],
            goal=data["goal"],
            status=SessionStatus(data["status"]),
            plan=data.get("plan", []),
            messages=data.get("messages", []),
            metrics=data.get("metrics", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        return state

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
