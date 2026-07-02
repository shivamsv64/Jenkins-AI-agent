#!/usr/bin/env python3
# main.py
"""
Jenkins AI Agent — CLI entrypoint.

Usage:
    python main.py --mode once             # Scan all jobs once
    python main.py --mode daemon           # Poll continuously
    python main.py --mode job --job NAME   # Analyze a single job
    python main.py --mode once --interval 120  # Override poll interval
"""

from __future__ import annotations
import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import yaml

from src.secrets import SecretsManager
from src.jenkins_client import JenkinsClient
from src.bedrock_client import BedrockClient
from src.gchat_client import GChatClient
from src.agent import JenkinsAIAgent


def setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/agent.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=log_cfg.get("max_bytes", 10_485_760),
            backupCount=log_cfg.get("backup_count", 5),
        ),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=handlers,
    )


def load_config(path: str = "config/config.yaml") -> dict:
    candidates = [Path(path)]
    if path != "config/config.yaml":
        candidates.append(Path("config/config.yaml"))
    candidates.append(Path("config.yaml"))

    for candidate in candidates:
        if candidate.exists():
            with candidate.open() as f:
                return yaml.safe_load(f)

    raise FileNotFoundError(
        "Could not find config.yaml. Checked: "
        + ", ".join(str(p) for p in candidates)
    )


def build_agent(cfg: dict) -> JenkinsAIAgent:
    """Resolve all secrets from SSM and wire up the agent."""
    bedrock_cfg = cfg.get("bedrock", {})
    region = bedrock_cfg.get("region", "us-east-1")

    secrets = SecretsManager(region=region)

    # ── Jenkins ──────────────────────────────────────────────
    jcfg = cfg["jenkins"]
    if jcfg.get("url"):
        jenkins_url = jcfg["url"]
        jenkins_username = jcfg.get("username") or secrets.get(jcfg["ssm_username_param"])
        jenkins_token = jcfg.get("api_token") or secrets.get(jcfg["ssm_token_param"])
    else:
        jenkins_url = secrets.get(jcfg["ssm_url_param"])
        jenkins_username = secrets.get(jcfg["ssm_username_param"])
        jenkins_token = secrets.get(jcfg["ssm_token_param"])

    jenkins = JenkinsClient(
        url=jenkins_url,
        username=jenkins_username,
        api_token=jenkins_token,
        cfg=jcfg,
    )

    # ── Bedrock ──────────────────────────────────────────────
    bedrock = BedrockClient(cfg=bedrock_cfg)

    # ── Google Chat ──────────────────────────────────────────
    gchat_webhook = cfg["gchat"].get("webhook_url")
    if not gchat_webhook:
        gchat_webhook = secrets.get(cfg["gchat"]["ssm_webhook_param"], required=False)
    gchat = GChatClient(
        webhook_url=gchat_webhook or "",
        callback_base_url=cfg["gchat"]["callback_base_url"],
    ) if gchat_webhook else None

    return JenkinsAIAgent(jenkins=jenkins, bedrock=bedrock, gchat=gchat, cfg=cfg)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Jenkins AI Agent — powered by Amazon Nova Pro"
    )
    parser.add_argument(
        "--mode", choices=["once", "daemon", "job"], default="once",
        help="Run mode: once | daemon | job",
    )
    parser.add_argument(
        "--job", default="",
        help="Job name (required when --mode=job)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Override poll interval in seconds (daemon mode only)",
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to config YAML (default: config/config.yaml)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg)
    logger = logging.getLogger("main")

    logger.info("Starting Jenkins AI Agent — mode=%s", args.mode)

    try:
        agent = build_agent(cfg)
    except RuntimeError as exc:
        logger.critical("Failed to initialise agent: %s", exc)
        sys.exit(1)

    if args.mode == "once":
        summary = agent.run_once()
        logger.info("Done: %s", summary)

    elif args.mode == "daemon":
        agent.run_daemon(interval=args.interval)

    elif args.mode == "job":
        if not args.job:
            logger.error("--job is required when --mode=job")
            sys.exit(1)
        result = agent.analyze_job(args.job)
        logger.info("Result: %s", result)


if __name__ == "__main__":
    main()
