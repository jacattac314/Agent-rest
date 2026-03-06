"""
Autonomous Maintenance Agent — Multi-Agent System
--------------------------------------------------
A proactive background agent that continuously monitors a software repository,
identifies maintenance opportunities (docs gaps, stale deps, technical debt),
prioritizes them by risk/impact, and autonomously implements low-risk fixes.

Multi-Agent Architecture
~~~~~~~~~~~~~~~~~~~~~~~~
The system implements a role-based team of specialist agents coordinated by a
central Orchestrator:

  Orchestrator  – central controller; manages the SharedState and drives the
                  pipeline through PLANNING → EXECUTING → VERIFYING phases.
  PlannerAgent  – decomposes the repository health snapshot into an ordered
                  task graph stored in SharedState.
  TaskExecutor  – implements approved tasks via the Claude Agent SDK.
  VerifierAgent – validates executor results with file-existence, syntax, and
                  content heuristic checks.

All agents communicate through the SharedState object, ensuring a single source
of truth, full auditability, and session resumability.
"""

from .orchestrator import MultiAgentOrchestrator
from .planner import PlannerAgent
from .state import AgentRole, SessionStatus, SharedState, TaskNode, TaskStatus
from .verifier import VerifierAgent

__all__ = [
    "MultiAgentOrchestrator",
    "PlannerAgent",
    "VerifierAgent",
    "SharedState",
    "SessionStatus",
    "TaskStatus",
    "TaskNode",
    "AgentRole",
]
