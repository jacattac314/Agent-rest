"""
Maintenance Scheduler
---------------------
Orchestrates the full maintenance cycle on a configurable interval:

  1. Scan the repository          (RepositoryScanner)
  2. Identify maintenance tasks   (TaskIdentifier)
  3. Prioritize tasks             (TaskPrioritizer)
  4. Filter to auto-executable tasks (risk < threshold, impact >= threshold)
  5. Execute each task            (TaskExecutor)
  6. Commit changes               (GitIntegration)
  7. Write audit log entry        (AuditLogger)

Uses APScheduler for background scheduling.  The scheduler can also be
triggered manually (run_now=True) for one-shot operation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .executor import ExecutionResult, TaskExecutor
from .git_integration import CommitInfo, GitIntegration
from .github_search import GitHubSearcher
from .identifier import MaintenanceTask, TaskIdentifier, TaskKind
from .implementation_planner import ImplementationPlanner
from .pr_creator import PullRequestCreator
from .prioritizer import TaskPrioritizer
from .scanner import RepositoryScanner, RepositorySnapshot

logger = logging.getLogger(__name__)


class AuditLogger:
    """Appends structured JSON records to the audit log file."""

    def __init__(self, audit_file: str):
        self.path = Path(audit_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: dict):
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


class FeatureResearchJob:
    """
    Executes the full missing-feature research pipeline for a single task:

      1. Search GitHub for relevant code and repositories.
      2. Use Claude API to write a structured implementation plan.
      3. Commit the plan file to a new branch via GitIntegration.
      4. Open a draft GitHub pull request with the plan as the PR body.

    This job is scheduled by MaintenanceScheduler whenever the main maintenance
    cycle surfaces one or more IMPLEMENT_MISSING_FEATURE tasks.
    """

    def __init__(self, config: dict, repo_root: str):
        self.config = config
        self.repo_root = repo_root
        github_cfg = config.get("github", {})
        self.searcher = GitHubSearcher(token=github_cfg.get("token"))
        self.planner = ImplementationPlanner(config)
        self.git = GitIntegration(config)
        self.pr_creator = PullRequestCreator(config)
        self.audit = AuditLogger(config["logging"].get("audit_file", "logs/audit.log"))

    def run(self, task: MaintenanceTask) -> dict:
        """
        Run the full research-and-plan pipeline for one missing-feature task.

        Args:
            task: An IMPLEMENT_MISSING_FEATURE MaintenanceTask.

        Returns:
            A summary dict logged to the audit file.
        """
        logger.info("FeatureResearchJob: starting for task '%s'", task.title)
        feature_description = task.context.get("feature_description", task.description)

        # Step 1 — Search GitHub
        search_results = self.searcher.search(
            feature_description=feature_description,
            language=self._detect_language(task.file_path),
        )
        logger.info(
            "GitHub search complete: %d code, %d repo results",
            len(search_results.code_results),
            len(search_results.repo_results),
        )

        # Step 2 — Write implementation plan
        plan_path = self.planner.create_plan(
            task=task,
            search_results=search_results,
            repo_root=self.repo_root,
        )
        logger.info("Implementation plan written: %s", plan_path)

        # Step 3 — Commit plan file to a new branch
        commit_info = self.git.commit_task(
            repo_root=self.repo_root,
            task_id=task.id,
            task_title=f"feat: implementation plan — {task.title}",
            files_modified=[plan_path],
            summary=f"Auto-generated implementation plan for: {task.title}",
        )

        pr_url = ""
        pr_error = ""

        # Step 4 — Open a draft pull request (only if we have a committed branch)
        if commit_info:
            pr_result = self.pr_creator.create_pr(
                branch=commit_info.branch,
                title=f"feat: {task.title}",
                plan_path=plan_path,
                task_description=feature_description,
            )
            if pr_result.success:
                pr_url = pr_result.pr_url
                logger.info("Draft PR opened: %s", pr_url)
            else:
                pr_error = pr_result.error
                logger.warning("PR creation skipped: %s", pr_error)
        else:
            logger.info("No commit produced (plan file may already be tracked); skipping PR.")

        record = {
            "job": "feature_research",
            "task_id": task.id,
            "task_title": task.title,
            "github_query": search_results.query,
            "code_results": len(search_results.code_results),
            "repo_results": len(search_results.repo_results),
            "plan_path": plan_path,
            "branch": commit_info.branch if commit_info else None,
            "pr_url": pr_url,
            "pr_error": pr_error,
        }
        self.audit.log(record)
        return record

    def _detect_language(self, file_path: str) -> str | None:
        """Return a GitHub-compatible language name based on file extension."""
        ext_map = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
        }
        from pathlib import Path as _Path
        return ext_map.get(_Path(file_path).suffix.lower())


class MaintenanceCycle:
    """
    Runs a single end-to-end maintenance pass over a repository.
    """

    def __init__(self, config: dict, repo_root: str):
        self.config = config
        self.repo_root = repo_root
        self.scanner = RepositoryScanner(config)
        self.identifier = TaskIdentifier()
        self.prioritizer = TaskPrioritizer(config)
        self.executor = TaskExecutor(config)
        self.git = GitIntegration(config)
        self.audit = AuditLogger(config["logging"].get("audit_file", "logs/audit.log"))

        prio_cfg = config.get("prioritizer", {})
        self.auto_execute_min_impact: int = prio_cfg.get("auto_execute_min_impact", 3)
        self.human_review_min_risk: int = prio_cfg.get("human_review_min_risk", 8)
        self.max_tasks_per_cycle: int = prio_cfg.get("max_tasks_per_cycle", 5)

        from .backlog import BacklogManager
        output_dir = config.get("reporting", {}).get("output_dir", "reports")
        self.backlog = BacklogManager(f"{output_dir}/bill_backlog.json")
        self.feature_research = FeatureResearchJob(config, repo_root)

    def run(self) -> dict:
        """Execute one maintenance cycle and return a summary dict."""
        cycle_start = datetime.utcnow()
        logger.info("=== Maintenance cycle started at %s ===", cycle_start.isoformat())

        # Phase 1: Perceive
        snapshot: RepositorySnapshot = self.scanner.scan(self.repo_root)
        logger.info("Scan complete: %s", snapshot.summary)

        # Phase 2: Identify
        tasks: list[MaintenanceTask] = self.identifier.identify(snapshot)
        logger.info("Identified %d tasks", len(tasks))

        # Phase 3: Prioritize (calls Claude API)
        tasks = self.prioritizer.prioritize(tasks)

        # Phase 4: Filter — low risk, sufficient impact, cap at max_tasks_per_cycle
        approved = [
            t for t in tasks
            if t.impact_score >= self.auto_execute_min_impact
            and t.risk_score < self.human_review_min_risk
        ][: self.max_tasks_per_cycle]

        deferred = [
            t for t in tasks
            if t.risk_score >= self.human_review_min_risk
        ]

        logger.info(
            "Approved for auto-execution: %d | Deferred (high risk): %d",
            len(approved),
            len(deferred),
        )

        # Update living backlog — all non-deferred tasks are pending (including
        # those below the impact threshold or beyond this cycle's cap)
        all_pending = [t for t in tasks if t.risk_score < self.human_review_min_risk]
        self.backlog.sync_identified(self.repo_root, all_pending, deferred)

        # Phase 5-6: Execute & commit
        results: list[dict] = []
        for task in approved:
            logger.info("Executing task: %s", task.title)
            exec_result: ExecutionResult = self.executor.execute(task, self.repo_root)

            commit_info: CommitInfo | None = None
            if exec_result.success and exec_result.files_modified:
                commit_info = self.git.commit_task(
                    repo_root=self.repo_root,
                    task_id=task.id,
                    task_title=task.title,
                    files_modified=exec_result.files_modified,
                    summary=exec_result.summary,
                )

            # Update backlog with execution result
            if exec_result.success:
                self.backlog.mark_done(
                    repo_root=self.repo_root,
                    task_id=task.id,
                    branch=commit_info.branch if commit_info else None,
                    commit_sha=commit_info.commit_sha if commit_info else None,
                    summary=exec_result.summary,
                )
            else:
                self.backlog.mark_failed(self.repo_root, task.id, exec_result.error or "unknown error")

            record = {
                "cycle_start": cycle_start.isoformat(),
                "task_id": task.id,
                "task_kind": task.kind.value,
                "task_title": task.title,
                "impact_score": task.impact_score,
                "risk_score": task.risk_score,
                "priority": task.priority,
                "success": exec_result.success,
                "files_modified": exec_result.files_modified,
                "summary": exec_result.summary,
                "error": exec_result.error,
                "branch": commit_info.branch if commit_info else None,
                "commit_sha": commit_info.commit_sha if commit_info else None,
            }
            self.audit.log(record)
            results.append(record)

        # Phase 7: Research missing-feature tasks — search GitHub, write plan, open PR
        feature_tasks = [t for t in tasks if t.kind == TaskKind.IMPLEMENT_MISSING_FEATURE]
        feature_research_results: list[dict] = []
        for feature_task in feature_tasks:
            logger.info("Running feature research for: %s", feature_task.title)
            research_record = self.feature_research.run(feature_task)
            feature_research_results.append(research_record)

        cycle_summary = {
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": datetime.utcnow().isoformat(),
            "repository": self.repo_root,
            "scan_summary": snapshot.summary,
            "tasks_identified": len(tasks),
            "tasks_approved": len(approved),
            "tasks_deferred": len(deferred),
            "tasks_succeeded": sum(1 for r in results if r["success"]),
            "tasks_failed": sum(1 for r in results if not r["success"]),
            "task_results": results,
            "deferred_titles": [t.title for t in deferred],
            "feature_research": feature_research_results,
        }
        logger.info("=== Cycle complete: %d/%d tasks succeeded ===",
                    cycle_summary["tasks_succeeded"], len(approved))
        return cycle_summary


class MaintenanceScheduler:
    """
    Wraps APScheduler to run MaintenanceCycle on a configurable interval.
    """

    def __init__(self, config: dict, repo_root: str):
        self.config = config
        self.repo_root = repo_root
        self.cycle = MaintenanceCycle(config, repo_root)
        self._scheduler = None

    def start(self, run_now: bool = True, blocking: bool = True):
        """
        Start the background scheduler.
        If run_now=True, execute an immediate cycle before the first interval fires.
        If blocking=True, keep the process alive (useful for standalone daemon mode).
        """
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.error("APScheduler not installed. Run: pip install apscheduler")
            raise

        interval = self.config["scheduler"].get("interval_minutes", 60)

        if run_now:
            logger.info("Running immediate startup maintenance cycle…")
            self.cycle.run()

        SchedulerClass = BlockingScheduler if blocking else BackgroundScheduler
        self._scheduler = SchedulerClass()
        self._scheduler.add_job(
            self.cycle.run,
            trigger="interval",
            minutes=interval,
            id="maintenance_cycle",
            name="Autonomous Maintenance Cycle",
            max_instances=1,        # prevent overlapping runs
            coalesce=True,
        )

        # Bill morning report — 7:55 AM CST (UTC-6 = 13:55 UTC)
        self._scheduler.add_job(
            self._deliver_morning_report,
            trigger="cron",
            hour=13,
            minute=55,
            timezone="UTC",
            id="morning_report",
            name="Bill Morning Report (7:55 AM CST)",
            max_instances=1,
            coalesce=True,
        )
        logger.info("Scheduler started — maintenance cycle every %d minute(s)", interval)
        logger.info("Bill morning report scheduled for 7:55 AM CST daily")
        self._scheduler.start()

    def _deliver_morning_report(self) -> None:
        """Callback invoked by APScheduler at 7:55 AM CST."""
        from .reporter import deliver_report
        deliver_report(self.config, self.repo_root)

    def run_once(self) -> dict:
        """Execute exactly one maintenance cycle synchronously (no scheduling)."""
        return self.cycle.run()

    def stop(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
