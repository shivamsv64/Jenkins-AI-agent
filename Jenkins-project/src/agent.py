from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from .jenkins_client import JenkinsClient, BuildInfo
from .bedrock_client import BedrockClient
from .gchat_client import GChatClient
from .email_client import EmailNotifier

logger = logging.getLogger(__name__)

ALERTED_BUILDS_FILE = Path("logs/.alerted_builds.json")


class JenkinsAIAgent:
    def __init__(self, jenkins: JenkinsClient, bedrock: BedrockClient, gchat: GChatClient, cfg: dict):
        self.jenkins = jenkins
        self.bedrock = bedrock
        self.gchat = gchat
        self.cfg = cfg
        self.email_notifier = self._build_email_notifier()
        self._alerted: set[str] = self._load_alerted()

    def run_once(self) -> dict:
        agent_cfg = self.cfg.get("agent", {})
        filter_names = agent_cfg.get("job_filter", [])
        ignore_names = agent_cfg.get("ignore_jobs", [])
        lookback = agent_cfg.get("lookback_builds", 3)
        max_log_chars = agent_cfg.get("max_log_chars", 8000)

        jobs = self.jenkins.list_jobs(filter_names=filter_names, ignore_names=ignore_names)
        logger.info("Scanning %d jobs...", len(jobs))

        summary = {"jobs_scanned": 0, "failures_found": 0, "alerts_sent": 0}
        for job_name in jobs:
            summary["jobs_scanned"] += 1
            try:
                failed = self.jenkins.get_failed_builds(job_name, lookback=lookback, max_log_chars=max_log_chars)
                for build in failed:
                    summary["failures_found"] += 1
                    if self._process_build(build):
                        summary["alerts_sent"] += 1
            except Exception as exc:
                logger.error("Error processing job '%s': %s", job_name, exc)

        logger.info("Run complete — %s", summary)
        return summary

    def run_daemon(self, interval: Optional[int] = None):
        if interval is None:
            interval = self.cfg.get("agent", {}).get("poll_interval_seconds", 60)
        logger.info("Daemon mode — polling every %ds", interval)
        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("Unexpected error in daemon loop: %s", exc)
            logger.info("Sleeping %ds...", interval)
            time.sleep(interval)

    def analyze_job(self, job_name: str) -> dict:
        agent_cfg = self.cfg.get("agent", {})
        lookback = agent_cfg.get("lookback_builds", 3)
        max_log_chars = agent_cfg.get("max_log_chars", 8000)

        failed = self.jenkins.get_failed_builds(job_name, lookback=lookback, max_log_chars=max_log_chars)
        results = []
        for build in failed:
            sent = self._process_build(build)
            results.append({
                "build_number": build.build_number,
                "result": build.result,
                "alert_sent": sent,
            })
        return {"job": job_name, "builds": results}

    def _process_build(self, build: BuildInfo) -> bool:
        build_key = f"{build.job_name}#{build.build_number}"
        if build_key in self._alerted:
            logger.debug("Skipping already-alerted build: %s", build_key)
            return False

        logger.info("Analyzing %s #%d (%s)...", build.job_name, build.build_number, build.result)

        diagnostic = self.bedrock.analyze_failure(build)
        logger.info("Diagnosis: %s | Confidence: %s | Action: %s", diagnostic.error_type, diagnostic.confidence, diagnostic.suggested_action)

        # Log any line-level error locations returned by the LLM
        try:
            for loc in getattr(diagnostic, "error_locations", []) or []:
                logger.info("Error location: %s:%s — %s", loc.get("file"), loc.get("line"), loc.get("message"))
        except Exception:
            pass

        sent = False
        if self.gchat is not None:
            sent = self.gchat.send_failure_alert(build, diagnostic)

        email_sent = False
        if self.email_notifier is not None:
            email_sent = self.email_notifier.send_failure_alert(build, diagnostic)

        if sent or email_sent:
            self._mark_alerted(build_key)
            logger.info("Alert sent for %s (gchat=%s email=%s)", build_key, sent, email_sent)
        else:
            logger.warning("Failed to send GChat/email alert for %s", build_key)

        return sent or email_sent

    def _build_email_notifier(self):
        email_cfg = self.cfg.get("email", {})
        if not email_cfg.get("enabled", False):
            return None

        try:
            from .secrets import SecretsManager
            secrets = SecretsManager(region=self.cfg.get("bedrock", {}).get("region", "us-east-1"))
            return EmailNotifier(cfg=email_cfg, secrets=secrets)
        except Exception as exc:
            logger.warning("Email notifier initialization failed: %s", exc)
            return None

    def _load_alerted(self) -> set[str]:
        if ALERTED_BUILDS_FILE.exists():
            try:
                data = json.loads(ALERTED_BUILDS_FILE.read_text())
                return set(data.get("alerted", []))
            except Exception:
                pass
        return set()

    def _mark_alerted(self, build_key: str):
        self._alerted.add(build_key)
        trimmed = list(self._alerted)[-500:]
        ALERTED_BUILDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERTED_BUILDS_FILE.write_text(json.dumps({"alerted": trimmed}, indent=2))
