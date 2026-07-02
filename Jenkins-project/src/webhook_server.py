from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import yaml
from flask import Flask, jsonify, request

from .jenkins_client import JenkinsClient
from .gchat_client import GChatClient
from .remediation import RemediationExecutor
from .secrets import SecretsManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("webhook_server")


def _load_cfg() -> dict:
    for path in [Path("config/config.yaml"), Path("config.yaml")]:
        if path.exists():
            with path.open() as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("Could not find config.yaml")


CFG = _load_cfg()

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
        jenkins_url = _secrets.get(jcfg["ssm_url_param"])
        jenkins_username = _secrets.get(jcfg["ssm_username_param"])
        jenkins_token = _secrets.get(jcfg["ssm_token_param"])
        gchat_webhook = _secrets.get(CFG["gchat"]["ssm_webhook_param"])

        _jenkins = JenkinsClient(jenkins_url, jenkins_username, jenkins_token, jcfg)
        _gchat = GChatClient(webhook_url=gchat_webhook, callback_base_url=CFG["gchat"]["callback_base_url"])
        remediation_cfg = CFG.get("remediation", {})
        _remediator = RemediationExecutor(
            jenkins=_jenkins,
            allowed_actions=remediation_cfg.get("allowed_actions", []),
            auto_execute=remediation_cfg.get("auto_execute", True),
        )
    return _jenkins, _gchat, _remediator


app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/webhook/action")
def handle_action():
    body = request.get_json(silent=True) or {}
    logger.info("Received webhook: %s", json.dumps(body)[:500])

    payload_str = _extract_payload(body)
    if not payload_str:
        logger.warning("No payload found in webhook body")
        return jsonify({"text": "⚠️ Invalid callback — no payload found."}), 400

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return jsonify({"text": "⚠️ Malformed payload."}), 400

    action = payload.get("action", "investigate")
    job_name = payload.get("job_name", "")
    build_number = int(payload.get("build_number", 0))
    decision = payload.get("decision", "reject")

    logger.info("Action callback — decision=%s action=%s job=%s build=#%d", decision, action, job_name, build_number)

    _, gchat, remediator = get_clients()

    if decision == "reject":
        gchat.send_text(f"⏭️ Alert for `{job_name} #{build_number}` was *dismissed*.")
        return jsonify({"text": f"Dismissed alert for {job_name} #{build_number}."})

    result = remediator.execute(action, job_name, build_number)
    gchat.send_action_result(job_name, build_number, action, result.success)

    return jsonify({"text": result.message, "success": result.success})


def _extract_payload(body: dict) -> str | None:
    try:
        for param in body["action"]["parameters"]:
            if param.get("key") == "payload":
                return param["value"]
    except (KeyError, TypeError):
        pass

    try:
        for param in body["commonEventObject"]["parameters"].values():
            if isinstance(param, str):
                return param
    except (KeyError, TypeError):
        pass

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
