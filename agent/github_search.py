"""
GitHub Search
-------------
Searches GitHub for code, repositories, and issues related to a missing feature.
Uses the GitHub REST API (unauthenticated or with a personal access token for
higher rate limits — 60 req/hr unauthenticated, 5000 req/hr authenticated).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


@dataclass
class GitHubCodeResult:
    repo_full_name: str
    file_path: str
    html_url: str


@dataclass
class GitHubRepoResult:
    full_name: str
    description: str
    html_url: str
    stars: int
    language: str


@dataclass
class GitHubSearchResults:
    query: str
    code_results: list[GitHubCodeResult] = field(default_factory=list)
    repo_results: list[GitHubRepoResult] = field(default_factory=list)
    error: str = ""


class GitHubSearcher:
    """
    Wraps the GitHub REST API to search for code and repositories
    related to a missing-feature description.

    Requires the `requests` package.  Pass a personal access token
    (or set GITHUB_TOKEN) to raise the rate limit from 60 to 5000 req/hr.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self._session: "requests.Session | None" = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        feature_description: str,
        language: str | None = None,
    ) -> GitHubSearchResults:
        """
        Search GitHub for code files and repositories relevant to a feature.

        Args:
            feature_description: Natural-language description of the missing feature.
            language: Optional programming language filter (e.g. "python").

        Returns:
            GitHubSearchResults with up to 5 code results and 5 repo results.
        """
        query = self._build_query(feature_description, language)
        results = GitHubSearchResults(query=query)

        try:
            session = self._get_session()
            results.code_results = self._search_code(session, query)
            time.sleep(0.5)  # Respect secondary rate limits between requests
            results.repo_results = self._search_repos(session, query)
        except Exception as exc:
            logger.error("GitHub search failed: %s", exc)
            results.error = str(exc)

        logger.info(
            "GitHub search '%s': %d code, %d repo results",
            query,
            len(results.code_results),
            len(results.repo_results),
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self) -> "requests.Session":
        if not REQUESTS_AVAILABLE:
            raise RuntimeError(
                "requests is not installed. Run: pip install requests"
            )
        if self._session is None:
            import requests as _requests
            self._session = _requests.Session()
            self._session.headers.update({
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "autonomous-maintenance-agent/1.0",
            })
            if self.token:
                self._session.headers["Authorization"] = f"Bearer {self.token}"
        return self._session

    def _build_query(self, description: str, language: str | None) -> str:
        """Extract keyword terms from the description for the GitHub search query."""
        # Use the first 8 words as keywords to keep the query focused
        keywords = " ".join(description.split()[:8])
        if language:
            return f"{keywords} language:{language}"
        return keywords

    def _search_code(self, session: "requests.Session", query: str) -> list[GitHubCodeResult]:
        try:
            resp = session.get(
                f"{self.BASE_URL}/search/code",
                params={"q": query, "per_page": 5},
                timeout=10,
            )
            resp.raise_for_status()
            return [
                GitHubCodeResult(
                    repo_full_name=item["repository"]["full_name"],
                    file_path=item["path"],
                    html_url=item["html_url"],
                )
                for item in resp.json().get("items", [])
            ]
        except Exception as exc:
            logger.warning("GitHub code search failed: %s", exc)
            return []

    def _search_repos(self, session: "requests.Session", query: str) -> list[GitHubRepoResult]:
        try:
            resp = session.get(
                f"{self.BASE_URL}/search/repositories",
                params={"q": query, "per_page": 5, "sort": "stars"},
                timeout=10,
            )
            resp.raise_for_status()
            return [
                GitHubRepoResult(
                    full_name=item["full_name"],
                    description=item.get("description") or "",
                    html_url=item["html_url"],
                    stars=item.get("stargazers_count", 0),
                    language=item.get("language") or "Unknown",
                )
                for item in resp.json().get("items", [])
            ]
        except Exception as exc:
            logger.warning("GitHub repository search failed: %s", exc)
            return []
