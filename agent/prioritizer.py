"""
Task Prioritizer
----------------
Uses the Claude API (with adaptive thinking) to evaluate each MaintenanceTask
and assign:
  - impact_score  (1-10): developer value delivered
  - risk_score    (1-10): probability of introducing regressions
  - priority      (composite): impact * (10 - risk)  → higher is better

Tasks are sorted descending by priority.  The scheduler then selects the
top-N tasks whose risk_score falls below the human-review threshold.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .identifier import MaintenanceTask

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a senior software engineer acting as a maintenance-task prioritizer.
Given a list of maintenance tasks for a software repository, score each task on:

  impact_score (1-10): How much developer value does completing this task deliver?
    1 = trivial cosmetic, 10 = critical improvement to safety/correctness/onboarding
  risk_score   (1-10): How likely is this change to introduce a regression?
    1 = zero risk (adding a comment), 10 = high risk (refactoring core logic)

Scoring rules:
  - README / docstring additions: impact 4-6, risk 1-2
  - Type hint additions: impact 4, risk 1
  - TODO addressing: impact varies by description (2-8), risk 2-5
  - Dependency audits: impact 6, risk 1 (audit only, no code change)
  - Long function refactors: impact 5-7, risk 5-7
  - Test coverage improvements: impact 7, risk 2-4

Return ONLY valid JSON — an array of objects with fields:
  id, impact_score, risk_score
"""


class TaskPrioritizer:
    """
    Scores and ranks MaintenanceTasks using the Claude Messages API.
    Uses streaming + adaptive thinking for accurate, nuanced scoring.
    """

    def __init__(self, config: dict):
        self.client = anthropic.Anthropic()
        self.model = config["agent"].get("model", "claude-opus-4-6")

    def prioritize(self, tasks: list[MaintenanceTask]) -> list[MaintenanceTask]:
        if not tasks:
            return []

        scores = self._score_with_claude(tasks)
        score_map: dict[str, dict] = {s["id"]: s for s in scores}

        for task in tasks:
            s = score_map.get(task.id, {})
            task.impact_score = s.get("impact_score", 5)
            task.risk_score = s.get("risk_score", 5)
            # High impact, low risk → high priority
            task.priority = task.impact_score * (10 - task.risk_score)

        tasks.sort(key=lambda t: t.priority, reverse=True)
        logger.info("Prioritized %d tasks", len(tasks))
        return tasks

    def _score_with_claude(self, tasks: list[MaintenanceTask]) -> list[dict[str, Any]]:
        task_list = [
            {"id": t.id, "kind": t.kind.value, "title": t.title, "description": t.description[:400]}
            for t in tasks
        ]
        user_message = json.dumps(task_list, indent=2)

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                final = stream.get_final_message()

            raw = next(
                (b.text for b in final.content if b.type == "text"),
                "[]",
            )
            # Strip markdown fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]

            return json.loads(raw)

        except (anthropic.APIError, json.JSONDecodeError) as exc:
            logger.warning("Prioritizer Claude call failed (%s); using defaults", exc)
            return []
