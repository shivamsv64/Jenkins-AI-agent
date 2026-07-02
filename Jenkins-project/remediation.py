# src/remediation.py
"""
Remediation action executor.
Called when a user clicks Approve in Google Chat.
Executes the action suggested by Nova Pro against Jenkins.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from jenkins_client import JenkinsClient

logger = logging.getLogger(__name__)


@dataclass
class RemediationResult:
    success: bool
    action: str
    job_name: str
    build_number: int
    message: str


class RemediationExecutor:
    """Runs approved remediation actions against Jenkins."""

    SUPPORTED_ACTIONS = {
        "retry_build",
        "rebuild_with_params",
        "investigate",   # no-op — just ack
    }

    def __init__(self, jenkins: JenkinsClient, allowed_actions: list[str],
                 auto_execute: bool = True):
        self.jenkins = jenkins
        self.allowed_actions = set(allowed_actions) & self.SUPPORTED_ACTIONS
        self.auto_execute = auto_execute
        logger.info("RemediationExecutor ready — allowed: %s", self.allowed_actions)

    def execute(self, action: str, job_name: str, build_number: int) -> RemediationResult:
        """Dispatch the approved action."""
        logger.info(
            "Executing remediation: action=%s job=%s build=#%d",
            action, job_name, build_number,
        )

        if not self.auto_execute:
            return RemediationResult(
                success=True,
                action=action,
                job_name=job_name,
                build_number=build_number,
                message="Auto-execute disabled — action acknowledged only.",
            )

        if action not in self.allowed_actions:
            msg = f"Action '{action}' is not in the allowed list: {self.allowed_actions}"
            logger.warning(msg)
            return RemediationResult(
                success=False,
                action=action,
                job_name=job_name,
                build_number=build_number,
                message=msg,
            )

        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return RemediationResult(
                success=False,
                action=action,
                job_name=job_name,
                build_number=build_number,
                message=f"No handler implemented for action '{action}'.",
            )

        return handler(job_name, build_number)

    # ──────────────────────────────────────────────
    # Action handlers
    # ──────────────────────────────────────────────

    def _do_retry_build(self, job_name: str, build_number: int) -> RemediationResult:
        success = self.jenkins.retry_build(job_name, build_number)
        return RemediationResult(
            success=success,
            action="retry_build",
            job_name=job_name,
            build_number=build_number,
            message=(
                f"Build #{build_number} of '{job_name}' retriggered successfully."
                if success else
                f"Failed to retrigger build #{build_number} of '{job_name}'."
            ),
        )

    def _do_rebuild_with_params(self, job_name: str, build_number: int) -> RemediationResult:
        # Same as retry via the replay endpoint
        return self._do_retry_build(job_name, build_number)

    def _do_investigate(self, job_name: str, build_number: int) -> RemediationResult:
        return RemediationResult(
            success=True,
            action="investigate",
            job_name=job_name,
            build_number=build_number,
            message="Marked for manual investigation. No automated action taken.",
        )
