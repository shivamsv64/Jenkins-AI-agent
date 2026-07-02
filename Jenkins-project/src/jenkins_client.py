from __future__ import annotations

import logging
from typing import Optional, Sequence

import requests
from requests.auth import HTTPBasicAuth

from .models import BuildInfo

logger = logging.getLogger(__name__)


class JenkinsClient:
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

    def list_jobs(self, filter_names: list[str] | None = None, ignore_names: list[str] | None = None) -> list[str]:
        data = self._get("api/json", tree="jobs[name,color]")
        jobs = [j["name"] for j in data.get("jobs", [])]

        if filter_names:
            filter_names = [f for f in filter_names if f]
            if filter_names:
                jobs = [j for j in jobs if j in filter_names]

        if ignore_names:
            ignore_names = [i for i in ignore_names if i]
            jobs = [j for j in jobs if j not in ignore_names]

        logger.debug("Jobs to monitor: %s", jobs)
        return jobs

    def get_recent_builds(self, job_name: str, count: int = 3) -> list[dict]:
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

    def get_console_log(self, job_name: str, build_number: int, max_chars: int = 8000) -> str:
        path = f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/consoleText"
        try:
            log = self._get_text(path)
        except requests.HTTPError as exc:
            logger.warning("Could not fetch console log: %s", exc)
            return ""

        if len(log) > max_chars:
            log = "...[truncated]...\n" + log[-max_chars:]
        return log

    def get_build_info(self, job_name: str, build_number: int, max_log_chars: int = 8000) -> BuildInfo:
        raw = self._request(
            "GET",
            f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/api/json",
            timeout=(self.connect_timeout, self.read_timeout),
        ).json()

        branch = ""
        triggered_by = ""
        parameters: dict = {}
        git_url = ""
        commit_sha = ""
        node_name = raw.get("builtOn", "") or raw.get("builtOnDisplayName", "")
        node_status = "Unknown"
        node_offline_reason = ""
        node_temporarily_offline = False
        node_num_executors = None
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
            # Try to extract SCM info if available
            if isinstance(action, dict):
                # common Jenkins fields
                if action.get("remoteUrls"):
                    urls = action.get("remoteUrls")
                    if isinstance(urls, (list, tuple)) and urls:
                        git_url = urls[0]
                if action.get("lastBuiltRevision"):
                    rev = action.get("lastBuiltRevision")
                    if isinstance(rev, dict):
                        sha1 = rev.get("SHA1") or rev.get("sha1")
                        if sha1:
                            commit_sha = sha1

        console_log = self.get_console_log(job_name, build_number, max_log_chars)

        # Attempt to resolve node health details
        if node_name:
            try:
                node_raw = self._request(
                    "GET",
                    f"computer/{requests.utils.quote(node_name, safe='')}/api/json",
                    timeout=(self.connect_timeout, self.read_timeout),
                ).json()
                node_offline_reason = node_raw.get("offlineCauseReason", "") or node_raw.get("offlineCause", "")
                node_temporarily_offline = node_raw.get("temporarilyOffline", False)
                node_num_executors = node_raw.get("numExecutors")
                if node_raw.get("offline", False) or node_temporarily_offline:
                    node_status = "offline"
                else:
                    node_status = "online"
            except Exception as exc:
                logger.debug("Could not fetch node status for %s: %s", node_name, exc)

        # Attempt to locate a Jenkinsfile: try workspace path, then GitHub raw fetch if we have repo + commit
        jenkinsfile = ""
        try:
            # Try workspace locations served by Jenkins
            for path in (
                f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/ws/Jenkinsfile",
                f"job/{requests.utils.quote(job_name, safe='')}/ws/Jenkinsfile",
                f"job/{requests.utils.quote(job_name, safe='')}/{build_number}/workspace/Jenkinsfile",
            ):
                try:
                    jenkinsfile = self._get_text(path)
                    if jenkinsfile and len(jenkinsfile) > 0:
                        break
                except Exception:
                    jenkinsfile = ""

            # If not found and we have a GitHub URL + commit, try raw.githubusercontent.com heuristic
            if not jenkinsfile and git_url and commit_sha and "github.com" in git_url:
                try:
                    # convert git url like https://github.com/user/repo.git -> user/repo
                    import re

                    m = re.search(r"github\.com[:/]+([^/]+/[^/.]+)(?:\.git)?", git_url)
                    if m:
                        repo = m.group(1)
                        raw_url = f"https://raw.githubusercontent.com/{repo}/{commit_sha}/Jenkinsfile"
                        resp = self.session.get(raw_url, timeout=(self.connect_timeout, self.read_timeout))
                        if resp.status_code == 200:
                            jenkinsfile = resp.text
                        # also attempt to fetch a small snapshot of common repo files
                        files = [
                            "package.json",
                            "requirements.txt",
                            "pom.xml",
                            "build.gradle",
                            "Dockerfile",
                            "Makefile",
                        ]
                        repo_files_text = []
                        for fname in files:
                            try:
                                furl = f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{fname}"
                                fresp = self.session.get(furl, timeout=(self.connect_timeout, self.read_timeout))
                                if fresp.status_code == 200 and fresp.text:
                                    repo_files_text.append(f"--- {fname} ---\n{fresp.text}\n")
                            except Exception:
                                continue
                        repo_files_text = "\n".join(repo_files_text)
                        if repo_files_text:
                            # attach repo files after Jenkinsfile for prompt
                            if jenkinsfile:
                                jenkinsfile = jenkinsfile + "\n\n--- REPO FILES ---\n" + repo_files_text
                            else:
                                jenkinsfile = "--- REPO FILES ---\n" + repo_files_text
                except Exception:
                    pass
        except Exception:
            jenkinsfile = ""

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
            git_url=git_url,
            commit_sha=commit_sha,
            jenkinsfile=jenkinsfile,
            node_name=node_name,
            node_status=node_status,
            node_offline_reason=node_offline_reason,
            node_temporarily_offline=node_temporarily_offline,
            node_num_executors=node_num_executors,
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
