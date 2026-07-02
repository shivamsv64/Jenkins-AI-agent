# src/jenkins_client.py
"""
Jenkins REST API client.
Credentials are fetched from AWS SSM Parameter Store at init time.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


@dataclass
class BuildInfo:
    job_name: str
    build_number: int
    result: str                  # FAILURE | UNSTABLE | ABORTED | SUCCESS
    url: str
    duration_ms: int
    timestamp: int
    branch: str = ""
    triggered_by: str = ""
    console_log: str = ""
    parameters: dict = field(default_factory=dict)

    @property
    def duration_human(self) -> str:
        secs = self.duration_ms // 1000
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if m else f"{s}s"


class JenkinsClient:
    """Thin wrapper around the Jenkins REST API."""

    def __init__(
        self,
        url: str | Sequence[str] | None,
        username: str,
        api_token: str,
        cfg: dict,
    ):
        self.base_urls = self._normalize_urls(url)
        self.base_url = self.base_urls[0]
        self.auth = HTTPBasicAuth(username, api_token)
        self.verify_ssl = cfg.get("verify_ssl", False)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = self.verify_ssl
        self.connect_timeout = cfg.get("connect_timeout", 10)
        self.read_timeout = cfg.get("read_timeout", 30)
        logger.info("JenkinsClient initialised — %s", ", ".join(self.base_urls))

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _normalize_urls(self, url: str | Sequence[str] | None) -> list[str]:
        if isinstance(url, (list, tuple, set)):
            values = [str(item).strip() for item in url if str(item).strip()]
        elif url is None:
            values = []
        else:
            values = [item.strip() for item in str(url).split(",") if item.strip()]

        if not values:
            raise ValueError("No Jenkins URL configured")

        return [item.rstrip("/") for item in values]

    def _should_retry(self, exc: requests.exceptions.RequestException) -> bool:
        if isinstance(exc, requests.exceptions.Timeout):
            return True
        if isinstance(exc, requests.exceptions.ConnectionError):
            return True
        if isinstance(exc, requests.exceptions.SSLError):
            return True
        if isinstance(exc, requests.HTTPError):
            status_code = exc.response.status_code if exc.response is not None else None
            return status_code is None or status_code >= 500
        return True

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        last_error: Optional[Exception] = None
        for base_url in self.base_urls:
            url = f"{base_url}/{path.lstrip('/')}"
            try:
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()
                self.base_url = base_url
                return resp
            except requests.exceptions.RequestException as exc:
                last_error = exc
                logger.warning("Jenkins request failed for %s (%s)", url, exc)
                if not self._should_retry(exc):
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("No Jenkins URLs available")

    def _get(self, path: str, **params) -> dict:
        request_params = {"tree": params.pop("tree", None), **params}
        resp = self._request(
            "GET",
            path,
            params=request_params,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        return resp.json()

    def _get_text(self, path: str) -> str:
        resp = self._request(
            "GET",
            path,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        return resp.text

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def list_jobs(self, filter_names: list[str] | None = None,
                  ignore_names: list[str] | None = None) -> list[str]:
        """Return job names, optionally filtered."""
        data = self._get(
            "api/json",
            tree="jobs[name,color]",
        )
        jobs = [j["name"] for j in data.get("jobs", [])]

        if filter_names:
            filter_names = [f for f in filter_names if f]  # drop empty strings
            if filter_names:
                jobs = [j for j in jobs if j in filter_names]

        if ignore_names:
            ignore_names = [i for i in ignore_names if i]
            jobs = [j for j in jobs if j not in ignore_names]

        logger.debug("Jobs to monitor: %s", jobs)
        return jobs

    def get_recent_builds(self, job_name: str, count: int = 3) -> list[dict]:
        """Return raw build metadata for the last `count` builds."""
        tree = (
            f"builds[number,result,url,duration,timestamp,"
            f"actions[causes[shortDescription],parameters[name,value]],"
            f"changeSet[items[comment,author[fullName]]]]{{0,{count}}}"
        )
        data = self._request(
            "GET",
            f"job/{requests.utils.quote(job_name, safe='')}/api/json",
            params={"tree": tree},
            timeout=(self.connect_timeout, self.read_timeout),
        ).json()
        return data.get("builds", [])

    def get_console_log(self, job_name: str, build_number: int,
                        max_chars: int = 8000) -> str:
        """Fetch and optionally truncate the console log."""
        path = (
            f"job/{requests.utils.quote(job_name, safe='')}/"
            f"{build_number}/consoleText"
        )
        try:
            log = self._get_text(path)
        except requests.HTTPError as exc:
            logger.warning("Could not fetch console log: %s", exc)
            return ""

        if len(log) > max_chars:
            # Keep the tail — errors are usually at the end
            log = "...[truncated]...\n" + log[-max_chars:]
        return log

    def get_build_info(self, job_name: str, build_number: int,
                       max_log_chars: int = 8000) -> BuildInfo:
        """Assemble a full BuildInfo for a specific build."""
        raw = self._request(
            "GET",
            f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/api/json",
            timeout=(self.connect_timeout, self.read_timeout),
        ).json()

        # Extract branch from actions
        branch = ""
        triggered_by = ""
        parameters: dict = {}
        for action in raw.get("actions", []):
            cls = action.get("_class", "")
            if "ParametersAction" in cls:
                for p in action.get("parameters", []):
                    parameters[p["name"]] = p.get("value", "")
                    if p["name"].lower() in ("branch", "git_branch", "branch_name"):
                        branch = str(p.get("value", ""))
            if "CauseAction" in cls:
                causes = action.get("causes", [])
                if causes:
                    triggered_by = causes[0].get("shortDescription", "")

        console_log = self.get_console_log(job_name, build_number, max_log_chars)

        return BuildInfo(
            job_name=job_name,
            build_number=build_number,
            result=raw.get("result", "UNKNOWN"),
            url=raw.get("url", ""),
            duration_ms=raw.get("duration", 0),
            timestamp=raw.get("timestamp", 0),
            branch=branch,
            triggered_by=triggered_by,
            console_log=console_log,
            parameters=parameters,
        )

    def get_failed_builds(self, job_name: str, lookback: int = 3,
                          max_log_chars: int = 8000) -> list[BuildInfo]:
        """Return BuildInfo objects for recent failed builds of a job."""
        recent = self.get_recent_builds(job_name, count=lookback)
        failed = []
        for b in recent:
            if b.get("result") in ("FAILURE", "UNSTABLE", "ABORTED"):
                info = self.get_build_info(
                    job_name, b["number"], max_log_chars=max_log_chars
                )
                failed.append(info)
        return failed

    def retry_build(self, job_name: str, build_number: int) -> bool:
        """Re-trigger a build using the same parameters."""
        try:
            self._request(
                "POST",
                f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/replay",
                timeout=(self.connect_timeout, self.read_timeout),
            )
            logger.info("Retriggered %s #%d", job_name, build_number)
            return True
        except requests.HTTPError as exc:
            logger.error("Retry failed: %s", exc)
            return False
