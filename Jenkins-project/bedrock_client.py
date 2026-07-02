# src/bedrock_client.py
"""
Amazon Bedrock client using Nova Pro for Jenkins failure analysis.
Uses the default boto3 credential chain — no keys hardcoded.
Credential resolution order:
  1. ~/.aws/credentials  (aws configure)
  2. EC2 instance profile / ECS task role
  3. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars (fallback)
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from jenkins_client import BuildInfo

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    root_cause: str
    error_type: str           # e.g. "Test Failure", "Build Error", "Timeout"
    affected_component: str   # e.g. "unit-tests", "docker-build", "maven"
    fix_steps: list[str]
    confidence: str           # HIGH | MEDIUM | LOW
    suggested_action: str     # "retry_build" | "manual_fix" | "investigate"
    raw_response: str = ""


SYSTEM_PROMPT = """You are an expert SRE and DevOps engineer specializing in CI/CD pipeline
troubleshooting. You analyze Jenkins build failures and provide precise, actionable diagnostics.

Always respond with valid JSON only — no markdown, no preamble, no trailing text.

JSON schema:
{
  "root_cause": "<1-2 sentence root cause>",
  "error_type": "<Test Failure|Build Error|Dependency Error|Timeout|Config Error|Network Error|Permission Error|Other>",
  "affected_component": "<which component/stage failed>",
  "fix_steps": ["<step 1>", "<step 2>", ...],
  "confidence": "<HIGH|MEDIUM|LOW>",
  "suggested_action": "<retry_build|manual_fix|investigate>"
}"""


class BedrockClient:
    """Calls Amazon Nova Pro via Bedrock Converse API."""

    def __init__(self, cfg: dict):
        self.model_id = cfg.get("model_id", "amazon.nova-pro-v1:0")
        self.region = cfg.get("region", "us-east-1")
        self.temperature = cfg.get("temperature", 0.2)
        self.max_tokens = cfg.get("max_tokens", 1024)

        # boto3 picks up credentials from ~/.aws/credentials automatically
        self.client = boto3.client("bedrock-runtime", region_name=self.region)
        logger.info(
            "BedrockClient ready — model=%s region=%s", self.model_id, self.region
        )

    def _build_prompt(self, build: BuildInfo) -> str:
        return f"""Analyze this Jenkins build failure and return JSON diagnostics.

JOB NAME: {build.job_name}
BUILD NUMBER: #{build.build_number}
RESULT: {build.result}
DURATION: {build.duration_human}
BRANCH: {build.branch or 'N/A'}
TRIGGERED BY: {build.triggered_by or 'N/A'}
BUILD URL: {build.url}

CONSOLE LOG (last portion):
---
{build.console_log or '[No console log available]'}
---

Diagnose the failure and return JSON only."""

    def analyze_failure(self, build: BuildInfo) -> DiagnosticResult:
        """Send build info to Nova Pro and parse the diagnostic JSON."""
        prompt = self._build_prompt(build)
        logger.info(
            "Sending build %s #%d to Nova Pro for analysis",
            build.job_name, build.build_number,
        )

        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "temperature": self.temperature,
                    "maxTokens": self.max_tokens,
                },
            )
        except ClientError as exc:
            logger.error("Bedrock API error: %s", exc)
            return self._fallback_result(str(exc))

        # Extract text from converse response
        raw = ""
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("type") == "text" or "text" in block:
                raw += block.get("text", "")

        logger.debug("Nova Pro raw response: %s", raw)
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> DiagnosticResult:
        """Parse Nova Pro JSON response into DiagnosticResult."""
        # Strip accidental markdown fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip().rstrip("```").strip()

        try:
            data = json.loads(text)
            return DiagnosticResult(
                root_cause=data.get("root_cause", "Unknown root cause"),
                error_type=data.get("error_type", "Other"),
                affected_component=data.get("affected_component", "Unknown"),
                fix_steps=data.get("fix_steps", []),
                confidence=data.get("confidence", "LOW"),
                suggested_action=data.get("suggested_action", "investigate"),
                raw_response=raw,
            )
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse Nova Pro JSON: %s — raw: %s", exc, raw[:300])
            return self._fallback_result(raw)

    @staticmethod
    def _fallback_result(raw: str) -> DiagnosticResult:
        return DiagnosticResult(
            root_cause="Analysis unavailable — see console log for details.",
            error_type="Other",
            affected_component="Unknown",
            fix_steps=["Check Jenkins console log manually"],
            confidence="LOW",
            suggested_action="investigate",
            raw_response=raw,
        )
