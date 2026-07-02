# Jenkins AI Agent

AI-powered Jenkins failure diagnostics using Amazon Bedrock (Nova Pro) + Google Chat webhooks with Approve/Reject actions.

## Architecture

```
jenkins-ai-agent/
├── src/
│   ├── __init__.py
│   ├── agent.py
│   ├── bedrock_client.py
│   ├── jenkins_client.py
│   ├── gchat_client.py
│   ├── remediation.py
│   ├── webhook_server.py
│   └── models.py
├── config/
│   ├── config.yaml.example
│   └── logging.yaml
├── scripts/
│   └── setup_ssm.sh
├── Dockerfile
├── requirements.txt
├── main.py
├── README.md
└── .env.example
```

## Secret Management

All secrets are stored in **AWS SSM Parameter Store** (SecureString).
No passwords, tokens, or keys are hardcoded anywhere.

Secrets stored in SSM:
- `/jenkins-ai-agent/jenkins/url` (can contain one or more Jenkins URLs separated by commas)
- `/jenkins-ai-agent/jenkins/username`
- `/jenkins-ai-agent/jenkins/api_token`
- `/jenkins-ai-agent/gchat/webhook_url`

AWS credentials are sourced from your local `~/.aws/credentials` (already configured via `aws configure`).

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Store secrets in SSM (one-time setup)
bash scripts/setup_ssm.sh

# 3. Copy and edit config/config.yaml.example to config/config.yaml (non-secret settings only)
cp config/config.yaml.example config/config.yaml

# 4. Run agent once
python main.py --mode once

# 5. Run agent as daemon (polls every N seconds)
python main.py --mode daemon

# 6. Start webhook server for Approve/Reject callbacks (separate terminal)
python webhook_server.py
```

## Modes

| Mode | Description |
|------|-------------|
| `once` | Check all jobs once, send alerts, exit |
| `daemon` | Poll continuously on configured interval |
| `job <name>` | Analyze a specific job by name |

## Multi-Jenkins URL support

If you have more than one Jenkins instance, put multiple URLs in the SSM parameter value separated by commas, for example:

```text
http://jenkins-a:8080, http://jenkins-b:8080
```

The client will try each URL in order and move to the next one if the current endpoint is unavailable or times out.

## Google Chat Flow

1. Agent detects failed Jenkins build
2. Nova Pro analyzes console log → root cause + fix steps
3. Card v2 message sent to Google Chat with:
   - Job name, build number, duration, branch
   - AI-generated root cause summary
   - Suggested fix steps
   - **Approve** button → triggers remediation
   - **Reject** button → dismisses alert
4. User clicks Approve → webhook server receives callback → executes fix
