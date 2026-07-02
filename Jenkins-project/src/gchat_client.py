from __future__ import annotations

import json
import logging

import requests

from .models import BuildInfo, DiagnosticResult

logger = logging.getLogger(__name__)

ERROR_EMOJI = {
    "Test Failure": "🧪",
    "Build Error": "🔨",
    "Dependency Error": "📦",
    "Timeout": "⏱️",
    "Config Error": "⚙️",
    "Network Error": "🌐",
    "Permission Error": "🔐",
    "Other": "❌",
}

CONFIDENCE_COLOR = {
    "HIGH": "#B71C1C",
    "MEDIUM": "#E65100",
    "LOW": "#F57F17",
}

RESULT_EMOJI = {
    "FAILURE": "🔴",
    "UNSTABLE": "🟡",
    "ABORTED": "⚫",
}


class GChatClient:
    def __init__(self, webhook_url: str, callback_base_url: str):
        self.webhook_url = webhook_url
        self.callback_base_url = callback_base_url.rstrip("/")
        logger.info("GChatClient ready — callback base: %s", self.callback_base_url)

    def send_failure_alert(self, build: BuildInfo, diagnostic: DiagnosticResult) -> bool:
        card = self._build_failure_card(build, diagnostic)
        return self._post({"cardsV2": [card]})

    def send_text(self, text: str) -> bool:
        return self._post({"text": text})

    def send_action_result(self, job_name: str, build_number: int, action: str, success: bool) -> bool:
        emoji = "✅" if success else "❌"
        status = "succeeded" if success else "failed"
        text = f"{emoji} *{action}* for `{job_name} #{build_number}` {status}."
        return self.send_text(text)

    def _build_failure_card(self, build: BuildInfo, diag: DiagnosticResult) -> dict:
        emoji = RESULT_EMOJI.get(build.result, "❌")
        err_emoji = ERROR_EMOJI.get(diag.error_type, "❌")
        header_color = CONFIDENCE_COLOR.get(diag.confidence, "#B71C1C")

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

        fix_steps_text = "\n".join(
            f"{i + 1}. {step}" for i, step in enumerate(diag.fix_steps)
        ) or "No specific steps — investigate manually."

        card_id = f"jenkins-alert-{build.job_name}-{build.build_number}"

        # Prepare error locations text
        error_locs = diag.error_locations or []
        if error_locs:
            error_locations_text = "\n".join(
                f"{el.get('file')}:{el.get('line')} — {el.get('message')}" for el in error_locs[:5]
            )
        else:
            error_locations_text = "No precise error locations provided."

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
                            self._kv("Node", build.node_name or "N/A"),
                            self._kv("Node Status", build.node_status or "Unknown"),
                            self._kv("Node Offline", build.node_offline_reason or "N/A"),
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
                    {
                        "header": "🛠️ Suggested Fix",
                        "collapsible": True,
                        "uncollapsibleWidgetsCount": 1,
                        "widgets": [
                            {"textParagraph": {"text": fix_steps_text}},
                        ],
                    },
                    {
                        "header": "📍 Error Locations (file:line)",
                        "collapsible": True,
                        "widgets": [
                            {"textParagraph": {"text": error_locations_text}},
                        ],
                    },
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
        return json.dumps(data)

    def _post(self, body: dict) -> bool:
        try:
            resp = requests.post(self.webhook_url, json=body, timeout=10)
            resp.raise_for_status()
            logger.info("Google Chat message sent successfully")
            return True
        except requests.HTTPError as exc:
            logger.error("Google Chat HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
            return False
        except requests.RequestException as exc:
            logger.error("Google Chat request failed: %s", exc)
            return False
