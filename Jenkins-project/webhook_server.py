# webhook_server.py
"""
Flask server that receives Approve / Reject button callbacks from Google Chat.
Run this alongside main.py:
    python webhook_server.py

Exposes:
    POST /webhook/action   ← Google Chat button callback
    GET  /health           ← health check
"""

from __future__ import annotations
import json
import logging
import os
import sys
from pathlib import Path

from flask import Flask, request, jsonify
import yaml

from src.secrets import SecretsManager
from src.jenkins_client import JenkinsClient
from src.gchat_client import GChatClient
from src.remediation import RemediationExecutor

# ── Bootstrap logging ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("webhook_server")

# ── Load config ───────────────────────────────────────────────
def _load_cfg() -> dict:
    for path in [Path("config/config.yaml"), Path("config.yaml")]:
        if path.exists():
            with path.open() as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("Could not find config.yaml")


CFG = _load_cfg()

# ── Bootstrap clients (lazy — done once at startup) ───────────
_secrets: SecretsManager | None = None
_jenkins: JenkinsClient | None = None
_gchat: GChatClient | None = None
_remediator: RemediationExecutor | None = None


def get_clients():
    global _secrets, _jenkins, _gchat, _remediator
    if _jenkins is None:
        bedrock_region = CFG.get("bedrock", {}).get("region", "us-east-1")
        _secrets = SecretsManager(region=bedrock_region)

        jcfg = CFG["jenkins"]
        if jcfg.get("url"):
            jenkins_url = jcfg["url"]
            jenkins_username = jcfg.get("username") or _secrets.get(jcfg["ssm_username_param"])
            jenkins_token = jcfg.get("api_token") or _secrets.get(jcfg["ssm_token_param"])
        else:
            jenkins_url = _secrets.get(jcfg["ssm_url_param"])
            jenkins_username = _secrets.get(jcfg["ssm_username_param"])
            jenkins_token = _secrets.get(jcfg["ssm_token_param"])
        gchat_webhook = CFG["gchat"].get("webhook_url") or _secrets.get(CFG["gchat"]["ssm_webhook_param"])

        _jenkins = JenkinsClient(jenkins_url, jenkins_username, jenkins_token, jcfg)
        _gchat = GChatClient(
            webhook_url=gchat_webhook,
            callback_base_url=CFG["gchat"]["callback_base_url"],
        )
        remediation_cfg = CFG.get("remediation", {})
        _remediator = RemediationExecutor(
            jenkins=_jenkins,
            allowed_actions=remediation_cfg.get("allowed_actions", []),
            auto_execute=remediation_cfg.get("auto_execute", True),
        )
    return _jenkins, _gchat, _remediator


# ── Flask app ─────────────────────────────────────────────────
app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/webhook/action")
def handle_action():
    """
    Google Chat sends a POST when a user clicks Approve or Reject.
    Payload is embedded in the button parameter 'payload'.
    """
    body = request.get_json(silent=True) or {}
    logger.info("Received webhook: %s", json.dumps(body)[:500])

    # Google Chat wraps button params in different locations depending on API version
    payload_str = _extract_payload(body)
    if not payload_str:
        logger.warning("No payload found in webhook body")
        return jsonify({"text": "⚠️ Invalid callback — no payload found."}), 400

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return jsonify({"text": "⚠️ Malformed payload."}), 400

    action      = payload.get("action", "investigate")
    job_name    = payload.get("job_name", "")
    build_number = int(payload.get("build_number", 0))
    decision    = payload.get("decision", "reject")

    logger.info(
        "Action callback — decision=%s action=%s job=%s build=#%d",
        decision, action, job_name, build_number,
    )

    _, gchat, remediator = get_clients()

    if decision == "reject":
        gchat.send_text(
            f"⏭️ Alert for `{job_name} #{build_number}` was *dismissed*."
        )
        return jsonify({"text": f"Dismissed alert for {job_name} #{build_number}."})

    # Approve → execute remediation
    result = remediator.execute(action, job_name, build_number)
    gchat.send_action_result(job_name, build_number, action, result.success)

    return jsonify({
        "text": result.message,
        "success": result.success,
    })


def _extract_payload(body: dict) -> str | None:
    """
    Google Chat puts button params in different places depending on context.
    Try all known locations.
    """
    # v2 interactive cards
    try:
        for param in body["action"]["parameters"]:
            if param.get("key") == "payload":
                return param["value"]
    except (KeyError, TypeError):
        pass

    # Common action object location
    try:
        for param in body["commonEventObject"]["parameters"].values():
            if isinstance(param, str):
                return param
    except (KeyError, TypeError):
        pass

    # Flat parameters
    try:
        params = body.get("action", {}).get("parameters", [])
        for p in params:
            if p.get("key") == "payload":
                return p.get("value")
    except Exception:
        pass

    return None


if __name__ == "__main__":
    port = int(os.environ.get("WEBHOOK_PORT", 5000))
    logger.info("Starting webhook server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
