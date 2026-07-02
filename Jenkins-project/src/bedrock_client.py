from __future__ import annotations

import json
import logging

import boto3
from botocore.exceptions import ClientError

from .models import BuildInfo, DiagnosticResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert SRE and DevOps engineer specializing in CI/CD pipeline troubleshooting. You analyze Jenkins build failures and provide precise, actionable diagnostics.

Always respond with valid JSON only — no markdown, no preamble, no trailing text.

JSON schema:
{
  "root_cause": "<1-2 sentence root cause>",
  "error_type": "<Test Failure|Build Error|Dependency Error|Timeout|Config Error|Network Error|Permission Error|Other>",
  "affected_component": "<which component/stage failed>",
  "fix_steps": ["<step 1>", "<step 2>", ...],
    "confidence": "<HIGH|MEDIUM|LOW>",
    "suggested_action": "<retry_build|manual_fix|investigate>",
    "error_locations": [
        {"file": "<path/to/file>", "line": <number>, "message": "<short explanation>"}
    ]
}"""

ENHANCED_INSTRUCTIONS = (
    "When available, examine the project's Jenkinsfile and repository files line-by-line. "
    "If a Jenkinsfile is provided, analyze it for syntax errors, bad stage logic, incorrect shell commands, or misconfigured SCM steps. "
    "If repository files or a workspace snapshot are provided, check dependency files, build scripts, and failing test outputs and report exact file paths and line numbers where possible. "
    "Also determine whether this failure was caused by Jenkins node health issues, such as the agent/node being offline, having no available executors, or disconnecting during build execution. "
    "Produce step-by-step fixes and exact commands to reproduce and fix the issue."
)


class BedrockClient:
    def __init__(self, cfg: dict):
        self.model_id = cfg.get("model_id", "amazon.nova-pro-v1:0")
        self.region = cfg.get("region", "us-east-1")
        self.temperature = cfg.get("temperature", 0.2)
        self.max_tokens = cfg.get("max_tokens", 1024)
        self.client = boto3.client("bedrock-runtime", region_name=self.region)
        logger.info("BedrockClient ready — model=%s region=%s", self.model_id, self.region)

    def _build_prompt(self, build: BuildInfo) -> str:
        jfile = getattr(build, "jenkinsfile", None) or ""
        jfile_snippet = jfile[:12000] if jfile else "[No Jenkinsfile available]"
        # create numbered lines for precise references
        if jfile and jfile_snippet and jfile_snippet != "[No Jenkinsfile available]":
            numbered = []
            for i, line in enumerate(jfile_snippet.splitlines()):
                numbered.append(f"{i+1}: {line}")
            jfile_numbered = "\n".join(numbered)
        else:
            jfile_numbered = jfile_snippet

        return f"""Analyze this Jenkins build failure and return JSON diagnostics.

JOB NAME: {build.job_name}
BUILD NUMBER: #{build.build_number}
RESULT: {build.result}
DURATION: {build.duration_human}
BRANCH: {build.branch or 'N/A'}
TRIGGERED BY: {build.triggered_by or 'N/A'}
BUILD URL: {build.url}
NODE: {build.node_name or 'N/A'}
NODE STATUS: {build.node_status or 'Unknown'}
NODE OFFLINE REASON: {build.node_offline_reason or 'N/A'}
NODE TEMPORARILY OFFLINE: {build.node_temporarily_offline}
NODE EXECUTORS: {build.node_num_executors if build.node_num_executors is not None else 'N/A'}

CONSOLE LOG (last portion):
---
{build.console_log or '[No console log available]'}
---


JENKINSFILE (first 12k chars, numbered lines):
---
{jfile_numbered}
---

When referencing a line in the Jenkinsfile above, use the exact 1-indexed line numbers shown.

{ENHANCED_INSTRUCTIONS}

Diagnose the failure and return JSON only."""

    def analyze_failure(self, build: BuildInfo) -> DiagnosticResult:
        prompt = self._build_prompt(build)
        logger.info("Sending build %s #%d to Nova Pro for analysis", build.job_name, build.build_number)

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

        raw = ""
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("type") == "text" or "text" in block:
                raw += block.get("text", "")

        logger.debug("Nova Pro raw response: %s", raw)
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> DiagnosticResult:
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
                error_locations=data.get("error_locations", []),
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
