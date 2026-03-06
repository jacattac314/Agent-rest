"""
GitHub Starred Repositories Integration
----------------------------------------
Fetches the authenticated user's starred repositories via the GitHub API,
clones each one to a local temp directory, and yields repo paths so the
scanner and orchestrator can analyse them exactly like a local repository.

Authentication
~~~~~~~~~~~~~~
Set the GITHUB_TOKEN environment variable (a personal access token with
at least `public_repo` scope, or `repo` for private starred repos).

Uses the `anthropic` package's bundled `httpx` client for GitHub API calls
so the project has a single HTTP dependency.

Configuration (config/settings.toml [github] section)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  [github]
  max_stars        = 50       # max repos to process per run
  clone_depth      = 1        # git clone --depth (shallow clone)
  skip_forks       = true     # ignore repos the user has forked
  skip_archived    = true     # ignore archived repos
  only_owned       = false    # only repos owned by the authenticated user
  execute_tasks    = false    # actually apply fixes (requires push access)
  temp_dir         = ""       # base dir for clones (default: system temp)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import anthropic
import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_DEFAULT_MAX_STARS = 50


@dataclass
class StarredRepo:
    full_name: str          # "owner/repo"
    clone_url: str
    html_url: str
    description: str
    owner_login: str
    is_fork: bool
    archived: bool
    default_branch: str
    local_path: str = ""    # set after cloning


class GitHubStarsClient:
    """
    Thin wrapper around the GitHub REST API for starred-repo discovery
    and local cloning.

    Uses httpx (bundled with the anthropic package) for all HTTP calls so
    the project has a single HTTP dependency.  The anthropic.Anthropic()
    client is instantiated here to ensure the API key is resolvable before
    any network work begins, and its version string is included in the
    User-Agent header.
    """

    def __init__(self, config: dict) -> None:
        gh_cfg = config.get("github", {})
        self.token: str = os.environ.get("GITHUB_TOKEN", "")
        self.max_stars: int = gh_cfg.get("max_stars", _DEFAULT_MAX_STARS)
        self.clone_depth: int = gh_cfg.get("clone_depth", 1)
        self.skip_forks: bool = gh_cfg.get("skip_forks", True)
        self.skip_archived: bool = gh_cfg.get("skip_archived", True)
        self.only_owned: bool = gh_cfg.get("only_owned", False)
        self.execute_tasks: bool = gh_cfg.get("execute_tasks", False)
        self._temp_base: str = gh_cfg.get("temp_dir", "")
        self._owned_login: str = ""

        # Instantiate the Anthropic client to verify the SDK is present and
        # to pick up the SDK version for the User-Agent header.
        self._anthropic = anthropic.Anthropic()
        sdk_version = getattr(anthropic, "__version__", "unknown")

        self._http = httpx.Client(
            base_url=_GITHUB_API,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"autonomous-maintenance-agent/1.0 anthropic-sdk/{sdk_version}",
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_starred(self) -> list[StarredRepo]:
        """
        Return a filtered list of the authenticated user's starred repos.
        Raises RuntimeError if GITHUB_TOKEN is not set.
        """
        if not self.token:
            raise RuntimeError(
                "GITHUB_TOKEN environment variable is not set. "
                "Create a personal access token at https://github.com/settings/tokens "
                "and export it before running."
            )

        self._owned_login = self._get_authenticated_user()
        logger.info("[github] authenticated as %s", self._owned_login)

        repos = self._paginate_starred()
        logger.info("[github] fetched %d starred repos before filtering", len(repos))

        filtered = self._filter(repos)
        logger.info("[github] %d repos remain after filtering", len(filtered))
        return filtered[: self.max_stars]

    def clone_repo(self, repo: StarredRepo, base_dir: str) -> str:
        """
        Shallow-clone *repo* under *base_dir* and return the local path.
        If the directory already exists it is removed and re-cloned.
        """
        dest = str(Path(base_dir) / repo.full_name.replace("/", "__"))
        if Path(dest).exists():
            shutil.rmtree(dest, ignore_errors=True)

        cmd = [
            "git", "clone",
            "--depth", str(self.clone_depth),
            "--branch", repo.default_branch,
            "--single-branch",
            "--quiet",
            repo.clone_url,
            dest,
        ]
        logger.debug("[github] cloning %s → %s", repo.full_name, dest)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone failed for {repo.full_name}: {result.stderr.strip()}"
            )
        repo.local_path = dest
        return dest

    def clone_all(self, repos: list[StarredRepo]) -> tuple[list[StarredRepo], list[str]]:
        """
        Clone all repos into a managed temp directory.
        Returns (successfully_cloned, errors).
        """
        base = self._temp_base or tempfile.mkdtemp(prefix="agent_stars_")
        Path(base).mkdir(parents=True, exist_ok=True)
        logger.info("[github] cloning up to %d repos into %s", len(repos), base)

        cloned: list[StarredRepo] = []
        errors: list[str] = []
        for repo in repos:
            try:
                self.clone_repo(repo, base)
                cloned.append(repo)
                logger.info("[github] cloned %s", repo.full_name)
            except Exception as exc:
                errors.append(f"{repo.full_name}: {exc}")
                logger.warning("[github] clone failed for %s: %s", repo.full_name, exc)

        return cloned, errors

    def cleanup(self, repos: list[StarredRepo]) -> None:
        """Remove all local clones and close the HTTP client."""
        for repo in repos:
            if repo.local_path and Path(repo.local_path).exists():
                shutil.rmtree(repo.local_path, ignore_errors=True)
                logger.debug("[github] removed clone %s", repo.local_path)
        self._http.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_authenticated_user(self) -> str:
        return self._api_get("/user").get("login", "")  # type: ignore[union-attr]

    def _paginate_starred(self) -> list[StarredRepo]:
        repos: list[StarredRepo] = []
        page = 1
        while len(repos) < self.max_stars * 2:
            items = self._api_get(
                f"/user/starred?per_page=100&page={page}&sort=updated"
            )
            if not items:
                break
            for item in items:  # type: ignore[union-attr]
                repos.append(StarredRepo(
                    full_name=item["full_name"],
                    clone_url=item["clone_url"],
                    html_url=item["html_url"],
                    description=item.get("description") or "",
                    owner_login=item["owner"]["login"],
                    is_fork=item.get("fork", False),
                    archived=item.get("archived", False),
                    default_branch=item.get("default_branch", "main"),
                ))
            if len(items) < 100:  # type: ignore[arg-type]
                break
            page += 1
        return repos

    def _filter(self, repos: list[StarredRepo]) -> list[StarredRepo]:
        out = []
        for r in repos:
            if self.skip_forks and r.is_fork:
                continue
            if self.skip_archived and r.archived:
                continue
            if self.only_owned and r.owner_login != self._owned_login:
                continue
            out.append(r)
        return out

    def _api_get(self, path: str) -> list | dict:
        response = self._http.get(path)
        if response.status_code >= 400:
            raise RuntimeError(
                f"GitHub API {path} → HTTP {response.status_code}: {response.text}"
            )
        return response.json()
