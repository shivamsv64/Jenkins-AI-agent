from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from .models import BuildInfo, DiagnosticResult
from .secrets import SecretsManager

logger = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(self, cfg: dict, secrets: Optional[SecretsManager] = None):
        self.enabled = bool(cfg.get("enabled", False))
        self.sender = cfg.get("sender", "")
        self.recipient = cfg.get("recipient", "")
        self.smtp_server = cfg.get("smtp_server", "smtp.gmail.com")
        self.smtp_port = int(cfg.get("smtp_port", 587))
        self.username_ssm_param = cfg.get("username_ssm_param", "")
        self.password_ssm_param = cfg.get("password_ssm_param", "")
        self.secrets = secrets
        self._username: Optional[str] = None
        self._password: Optional[str] = None

    def send_failure_alert(self, build: BuildInfo, diagnostic: DiagnosticResult) -> bool:
        if not self.enabled:
            return False

        if not self.sender or not self.recipient:
            logger.warning("Email notifier is enabled but sender/recipient are not configured")
            return False

        try:
            username, password = self._get_credentials()
            if not username or not password:
                logger.warning("Email notifier credentials are missing; skipping email send")
                return False

            msg = self._build_message(build, diagnostic)
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)

            logger.info("Email alert sent to %s", self.recipient)
            return True
        except Exception as exc:
            logger.error("Failed to send email alert: %s", exc)
            return False

    def _get_credentials(self) -> tuple[Optional[str], Optional[str]]:
        if self._username is not None and self._password is not None:
            return self._username, self._password

        username = None
        password = None

        if self.secrets:
            if self.username_ssm_param:
                username = self.secrets.get(self.username_ssm_param, required=False)
            if self.password_ssm_param:
                password = self.secrets.get(self.password_ssm_param, required=False)

        if not username:
            username = os.getenv("JENKINS_EMAIL_USERNAME") or os.getenv("SMTP_USERNAME")
        if not password:
            password = os.getenv("JENKINS_EMAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")

        self._username = username
        self._password = password
        return username, password

    def _build_message(self, build: BuildInfo, diagnostic: DiagnosticResult) -> EmailMessage:
        subject = f"Jenkins build failed: {build.job_name} #{build.build_number}"
        body = f"""Jenkins build failure alert

Job: {build.job_name}
Build: #{build.build_number}
Result: {build.result}
Duration: {build.duration_human}
Branch: {build.branch or 'N/A'}
Triggered By: {build.triggered_by or 'N/A'}
Build URL: {build.url}

Root Cause: {diagnostic.root_cause}
Error Type: {diagnostic.error_type}
Affected Component: {diagnostic.affected_component}
Confidence: {diagnostic.confidence}
Suggested Action: {diagnostic.suggested_action}

Suggested Fix Steps:
"""
        for idx, step in enumerate(diagnostic.fix_steps, 1):
            body += f"{idx}. {step}\n"

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(body)
        return message
