"""
Bill's Living Backlog
--------------------------
A persistent, cross-repository task backlog maintained by Bill.

State is stored in a JSON file (default: reports/bill_backlog.json).
A human-readable Markdown render is written alongside it as BACKLOG.md.

Lifecycle of a task:
  pending   → identified by the scanner, not yet executed
  done      → successfully executed and committed
  deferred  → risk score too high for auto-execution (needs human review)
  failed    → attempted but execution errored
  dismissed → duplicate or stale; removed on next cycle if repo no longer sees it
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

TaskStatus = Literal["pending", "done", "deferred", "failed", "dismissed"]


class BacklogManager:
    """
    Reads, updates, and writes Bill's multi-repo living backlog.

    All timestamps are stored as UTC ISO-8601 strings.
    """

    def __init__(self, backlog_file: str = "reports/bill_backlog.json"):
        self.path = Path(backlog_file)
        self.md_path = self.path.with_suffix(".md").with_name("BACKLOG.md")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Could not read backlog file %s — starting fresh", self.path)
        return {"tasks": {}}  # keyed by global task key = "repo::task_id"

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        self._render_markdown()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_identified(
        self,
        repo_root: str,
        tasks: list,          # list[MaintenanceTask]
        deferred_tasks: list, # list[MaintenanceTask] — high-risk
    ) -> None:
        """
        Called after identify+prioritize.  Adds new tasks, preserves existing
        ones, marks tasks no longer seen in this repo as 'dismissed'.
        """
        now = _utcnow()
        seen_keys: set[str] = set()

        for task in tasks:
            key = _make_key(repo_root, task.id)
            seen_keys.add(key)
            existing = self._data["tasks"].get(key)
            if existing and existing["status"] in ("done", "deferred", "failed"):
                # Don't overwrite completed/deferred entries
                continue
            self._data["tasks"][key] = {
                "key": key,
                "repo": repo_root,
                "task_id": task.id,
                "kind": task.kind.value,
                "title": task.title,
                "description": task.description,
                "file_path": task.file_path or "",
                "line": task.line,
                "impact_score": task.impact_score,
                "risk_score": task.risk_score,
                "priority": task.priority,
                "status": "pending",
                "first_seen": existing.get("first_seen", now) if existing else now,
                "last_seen": now,
                "completed_at": None,
                "branch": None,
                "commit_sha": None,
                "error": None,
                "notes": existing.get("notes", "") if existing else "",
            }

        for task in deferred_tasks:
            key = _make_key(repo_root, task.id)
            seen_keys.add(key)
            existing = self._data["tasks"].get(key, {})
            self._data["tasks"][key] = {
                **existing,
                "key": key,
                "repo": repo_root,
                "task_id": task.id,
                "kind": task.kind.value,
                "title": task.title,
                "description": task.description,
                "file_path": task.file_path or "",
                "line": task.line,
                "impact_score": task.impact_score,
                "risk_score": task.risk_score,
                "priority": task.priority,
                "status": "deferred",
                "first_seen": existing.get("first_seen", now),
                "last_seen": now,
                "completed_at": existing.get("completed_at"),
                "notes": existing.get("notes", "Awaiting human review — risk score too high for auto-execution."),
            }

        # Dismiss tasks from this repo that the scanner no longer sees
        for key, entry in self._data["tasks"].items():
            if entry["repo"] == repo_root and key not in seen_keys:
                if entry["status"] == "pending":
                    entry["status"] = "dismissed"

        self._save()

    def mark_done(
        self,
        repo_root: str,
        task_id: str,
        branch: str | None,
        commit_sha: str | None,
        summary: str,
    ) -> None:
        key = _make_key(repo_root, task_id)
        if key in self._data["tasks"]:
            self._data["tasks"][key].update({
                "status": "done",
                "completed_at": _utcnow(),
                "branch": branch,
                "commit_sha": commit_sha,
                "notes": summary,
            })
        self._save()

    def mark_failed(self, repo_root: str, task_id: str, error: str) -> None:
        key = _make_key(repo_root, task_id)
        if key in self._data["tasks"]:
            self._data["tasks"][key].update({
                "status": "failed",
                "error": error,
                "last_seen": _utcnow(),
            })
        self._save()

    def get_summary(self) -> dict:
        """Return counts per status and per repo for the morning report."""
        by_status: dict[str, int] = {}
        by_repo: dict[str, dict[str, int]] = {}
        for entry in self._data["tasks"].values():
            s = entry["status"]
            by_status[s] = by_status.get(s, 0) + 1
            repo = entry["repo"]
            by_repo.setdefault(repo, {})
            by_repo[repo][s] = by_repo[repo].get(s, 0) + 1
        return {"by_status": by_status, "by_repo": by_repo}

    def get_pending(self, repo: str | None = None) -> list[dict]:
        return [
            e for e in self._data["tasks"].values()
            if e["status"] == "pending"
            and (repo is None or e["repo"] == repo)
        ]

    def get_deferred(self, repo: str | None = None) -> list[dict]:
        return [
            e for e in self._data["tasks"].values()
            if e["status"] == "deferred"
            and (repo is None or e["repo"] == repo)
        ]

    def all_tasks(self) -> list[dict]:
        return list(self._data["tasks"].values())

    # ------------------------------------------------------------------
    # Markdown render
    # ------------------------------------------------------------------

    def _render_markdown(self) -> None:
        tasks = self._data["tasks"]
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        pending   = [t for t in tasks.values() if t["status"] == "pending"]
        deferred  = [t for t in tasks.values() if t["status"] == "deferred"]
        failed    = [t for t in tasks.values() if t["status"] == "failed"]
        done      = [t for t in tasks.values() if t["status"] == "done"]
        dismissed = [t for t in tasks.values() if t["status"] == "dismissed"]

        # Sort pending by priority descending
        pending.sort(key=lambda t: t.get("priority", 0), reverse=True)
        deferred.sort(key=lambda t: t.get("impact_score", 0), reverse=True)

        lines: list[str] = []
        lines.append("# Bill's Living Backlog")
        lines.append("")
        lines.append(f"*Last updated: {now_str}*")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Summary table
        total = len(tasks)
        lines.append("## Summary")
        lines.append("")
        lines.append("| Status | Count |")
        lines.append("|--------|------:|")
        lines.append(f"| Pending (auto-queue) | {len(pending)} |")
        lines.append(f"| Deferred (human review needed) | {len(deferred)} |")
        lines.append(f"| Failed | {len(failed)} |")
        lines.append(f"| Done | {len(done)} |")
        lines.append(f"| Dismissed | {len(dismissed)} |")
        lines.append(f"| **Total ever seen** | **{total}** |")
        lines.append("")

        # Per-repo breakdown
        repos: dict[str, list] = {}
        for t in tasks.values():
            repos.setdefault(t["repo"], []).append(t)
        if len(repos) > 1:
            lines.append("## Repositories")
            lines.append("")
            for repo, rtasks in sorted(repos.items()):
                by_s = {}
                for t in rtasks:
                    by_s[t["status"]] = by_s.get(t["status"], 0) + 1
                summary_parts = ", ".join(f"{v} {k}" for k, v in by_s.items())
                lines.append(f"- `{repo}` — {summary_parts}")
            lines.append("")

        # Pending
        if pending:
            lines.append("## Pending Tasks (Auto-Queue)")
            lines.append("")
            lines.append("Tasks the agent will execute on the next maintenance cycle.")
            lines.append("")
            _render_task_table(lines, pending)
            lines.append("")

        # Deferred
        if deferred:
            lines.append("## Deferred Tasks (Human Review Required)")
            lines.append("")
            lines.append(
                "> These tasks were flagged as high-risk (risk score ≥ 8). "
                "Review and implement manually or adjust thresholds in `config/settings.toml`."
            )
            lines.append("")
            _render_task_table(lines, deferred, show_risk=True)
            lines.append("")

        # Failed
        if failed:
            lines.append("## Failed Tasks")
            lines.append("")
            for t in failed:
                lines.append(f"### {t['title']}")
                lines.append(f"- **Repo:** `{t['repo']}`")
                lines.append(f"- **File:** `{t['file_path']}`")
                if t.get("error"):
                    lines.append(f"- **Error:** {t['error']}")
                lines.append("")

        # Done (last 20)
        if done:
            recent_done = sorted(done, key=lambda t: t.get("completed_at") or "", reverse=True)[:20]
            lines.append("## Recently Completed")
            lines.append("")
            lines.append("| Task | Repo | Completed | Branch |")
            lines.append("|------|------|-----------|--------|")
            for t in recent_done:
                completed = (t.get("completed_at") or "")[:10]
                branch = t.get("branch") or ""
                short_branch = branch.split("/")[-1][:30] if branch else "—"
                repo_short = Path(t["repo"]).name
                lines.append(f"| {t['title'][:50]} | `{repo_short}` | {completed} | `{short_branch}` |")
            lines.append("")

        lines.append("---")
        lines.append("*Maintained automatically by Bill — Bill*")

        self.md_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Backlog Markdown written to %s", self.md_path)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_key(repo_root: str, task_id: str) -> str:
    repo_name = Path(repo_root).name
    return f"{repo_name}::{task_id}"


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _render_task_table(lines: list[str], tasks: list[dict], show_risk: bool = False) -> None:
    if show_risk:
        lines.append("| Priority | Impact | Risk | Kind | Title | File |")
        lines.append("|--------:|------:|-----:|------|-------|------|")
        for t in tasks:
            kind = t["kind"].replace("_", " ").title()
            file_short = Path(t["file_path"]).name if t["file_path"] else "—"
            lines.append(
                f"| {t.get('priority', '?')} "
                f"| {t.get('impact_score', '?')} "
                f"| {t.get('risk_score', '?')} "
                f"| {kind} "
                f"| {t['title'][:55]} "
                f"| `{file_short}` |"
            )
    else:
        lines.append("| Priority | Impact | Kind | Title | File |")
        lines.append("|--------:|------:|------|-------|------|")
        for t in tasks:
            kind = t["kind"].replace("_", " ").title()
            file_short = Path(t["file_path"]).name if t["file_path"] else "—"
            lines.append(
                f"| {t.get('priority', '?')} "
                f"| {t.get('impact_score', '?')} "
                f"| {kind} "
                f"| {t['title'][:55]} "
                f"| `{file_short}` |"
            )
