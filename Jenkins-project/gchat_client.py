# src/gchat_client.py
"""
Google Chat webhook client.
Sends Card v2 messages with Approve / Reject interactive buttons.
The callback_base_url must point to your running webhook_server.py.
"""

from __future__ import annotations
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

from jenkins_client import BuildInfo
from bedrock_client import DiagnosticResult

logger = logging.getLogger(__name__)

# Emoji map for error types
ERROR_EMOJI = {
    "Test Failure":      "🧪",
    "Build Error":       "🔨",
    "Dependency Error":  "📦",
    "Timeout":           "⏱️",
    "Config Error":      "⚙️",
    "Network Error":     "🌐",
    "Permission Error":  "🔐",
    "Other":             "❌",
}

CONFIDENCE_COLOR = {
    "HIGH":   "#B71C1C",   # deep red
    "MEDIUM": "#E65100",   # deep orange
    "LOW":    "#F57F17",   # amber
}

RESULT_EMOJI = {
    "FAILURE":  "🔴",
    "UNSTABLE": "🟡",
    "ABORTED":  "⚫",
}


class GChatClient:
    """Sends Google Chat Card v2 messages via incoming webhook."""

    def __init__(self, webhook_url: str, callback_base_url: str):
        self.webhook_url = webhook_url
        self.callback_base_url = callback_base_url.rstrip("/")
        logger.info("GChatClient ready — callback base: %s", self.callback_base_url)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def send_failure_alert(
        self,
        build: BuildInfo,
        diagnostic: DiagnosticResult,
    ) -> bool:
        """Send a full diagnostic card with Approve/Reject buttons."""
        card = self._build_failure_card(build, diagnostic)
        return self._post({"cardsV2": [card]})

    def send_text(self, text: str) -> bool:
        """Send a plain text message (useful for simple status updates)."""
        return self._post({"text": text})

    def send_action_result(self, job_name: str, build_number: int,
                           action: str, success: bool) -> bool:
        """Notify outcome of an Approve action."""
        emoji = "✅" if success else "❌"
        status = "succeeded" if success else "failed"
        text = (
            f"{emoji} *{action}* for `{job_name} #{build_number}` {status}."
        )
        return self.send_text(text)

    # ──────────────────────────────────────────────
    # Card builder
    # ──────────────────────────────────────────────

    def _build_failure_card(
        self,
        build: BuildInfo,
        diag: DiagnosticResult,
    ) -> dict:
        emoji = RESULT_EMOJI.get(build.result, "❌")
        err_emoji = ERROR_EMOJI.get(diag.error_type, "❌")
        header_color = CONFIDENCE_COLOR.get(diag.confidence, "#B71C1C")

        # Encode action payload for callback buttons
        approve_payload = self._encode_payload({
            "action": diag.suggested_action,
            "job_name": build.job_name,
            "build_number": build.build_number,
            "decision": "approve",
        })
        reject_payload = self._encode_payload({
            "action": diag.suggested_action,
            "job_name": build.job_name,
            "build_number": build.build_number,
            "decision": "reject",
        })

        # Fix steps formatted as a numbered list
        fix_steps_text = "\n".join(
            f"{i+1}. {step}" for i, step in enumerate(diag.fix_steps)
        ) or "No specific steps — investigate manually."

        card_id = f"jenkins-alert-{build.job_name}-{build.build_number}"

        return {
            "cardId": card_id,
            "card": {
                "header": {
                    "title": f"{emoji} Jenkins Build Failed",
                    "subtitle": f"{build.job_name} — Build #{build.build_number}",
                    "imageUrl": "https://www.jenkins.io/images/logos/jenkins/jenkins.png",
                    "imageType": "CIRCLE",
                },
                "sections": [
                    # ── Build metadata ──
                    {
                        "header": "📋 Build Details",
                        "collapsible": False,
                        "widgets": [
                            self._kv("Job", f"`{build.job_name}`"),
                            self._kv("Build", f"#{build.build_number}"),
                            self._kv("Result", f"{emoji} {build.result}"),
                            self._kv("Duration", build.duration_human),
                            self._kv("Branch", build.branch or "N/A"),
                            self._kv("Triggered By", build.triggered_by or "N/A"),
                            {
                                "buttonList": {
                                    "buttons": [
                                        {
                                            "text": "🔗 Open Build",
                                            "onClick": {"openLink": {"url": build.url}},
                                        }
                                    ]
                                }
                            },
                        ],
                    },
                    # ── AI Diagnosis ──
                    {
                        "header": f"{err_emoji} AI Diagnosis  ·  Confidence: {diag.confidence}",
                        "collapsible": False,
                        "widgets": [
                            self._kv("Error Type", diag.error_type),
                            self._kv("Affected Component", diag.affected_component),
                            {
                                "textParagraph": {
                                    "text": f"<b>Root Cause:</b>\n{diag.root_cause}"
                                }
                            },
                        ],
                    },
                    # ── Fix Steps ──
                    {
                        "header": "🛠️ Suggested Fix",
                        "collapsible": True,
                        "uncollapsibleWidgetsCount": 1,
                        "widgets": [
                            {"textParagraph": {"text": fix_steps_text}},
                        ],
                    },
                    # ── Approve / Reject ──
                    {
                        "header": f"⚡ Action: {diag.suggested_action.replace('_', ' ').title()}",
                        "widgets": [
                            {
                                "buttonList": {
                                    "buttons": [
                                        {
                                            "text": "✅ Approve",
                                            "color": {
                                                "red": 0.18, "green": 0.56,
                                                "blue": 0.34, "alpha": 1,
                                            },
                                            "onClick": {
                                                "action": {
                                                    "function": f"{self.callback_base_url}/webhook/action",
                                                    "parameters": [
                                                        {"key": "payload", "value": approve_payload}
                                                    ],
                                                }
                                            },
                                        },
                                        {
                                            "text": "❌ Reject",
                                            "color": {
                                                "red": 0.73, "green": 0.11,
                                                "blue": 0.11, "alpha": 1,
                                            },
                                            "onClick": {
                                                "action": {
                                                    "function": f"{self.callback_base_url}/webhook/action",
                                                    "parameters": [
                                                        {"key": "payload", "value": reject_payload}
                                                    ],
                                                }
                                            },
                                        },
                                    ]
                                }
                            }
                        ],
                    },
                ],
            },
        }

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _kv(label: str, value: str) -> dict:
        return {
            "decoratedText": {
                "topLabel": label,
                "text": value,
            }
        }

    @staticmethod
    def _encode_payload(data: dict) -> str:
        """Encode action payload as JSON string (passed through button parameter)."""
        return json.dumps(data)

    def _post(self, body: dict) -> bool:
        try:
            resp = requests.post(
                self.webhook_url,
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Google Chat message sent successfully")
            return True
        except requests.HTTPError as exc:
            logger.error("Google Chat HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
            return False
        except requests.RequestException as exc:
            logger.error("Google Chat request failed: %s", exc)
            return False
