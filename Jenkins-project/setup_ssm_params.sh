#!/usr/bin/env bash
# scripts/setup_ssm_params.sh
# ─────────────────────────────────────────────────────────────
# One-time setup: stores secrets in AWS SSM Parameter Store.
# Run this once from your local machine (aws-cli must be configured).
# After this, the agent never needs secrets in any file.
# ─────────────────────────────────────────────────────────────

set -euo pipefail

AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
PREFIX="/jenkins-ai-agent"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Jenkins AI Agent — SSM Secret Setup       ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Region: $AWS_REGION"
echo "Prefix: $PREFIX"
echo ""

# ── Helper ────────────────────────────────────────────────────
put_param() {
    local name="$1"
    local prompt="$2"
    local type="${3:-SecureString}"

    # Check if already exists
    if aws ssm get-parameter --name "$name" --region "$AWS_REGION" \
       --query "Parameter.Name" --output text 2>/dev/null; then
        read -r -p "  '$name' already exists. Overwrite? [y/N]: " overwrite
        [[ "$overwrite" =~ ^[Yy]$ ]] || { echo "  Skipped."; return; }
    fi

    read -r -s -p "  Enter $prompt: " value
    echo ""
    aws ssm put-parameter \
        --name "$name" \
        --value "$value" \
        --type "$type" \
        --overwrite \
        --region "$AWS_REGION" \
        --description "Jenkins AI Agent — $prompt" \
        > /dev/null
    echo "  ✅ Stored: $name"
}

# ── Jenkins ───────────────────────────────────────────────────
echo "── Jenkins Settings ─────────────────────────────────"
put_param "$PREFIX/jenkins/url"       "Jenkins URL (e.g. http://EC2-IP:8080)"  "String"
put_param "$PREFIX/jenkins/username"  "Jenkins username"
put_param "$PREFIX/jenkins/api_token" "Jenkins API token"

# ── Google Chat ───────────────────────────────────────────────
echo ""
echo "── Google Chat Settings ─────────────────────────────"
put_param "$PREFIX/gchat/webhook_url" "Google Chat incoming webhook URL"

# ── Email Notifications ─────────────────────────────────────
echo ""
echo "── Email Notification Settings ─────────────────────"
put_param "$PREFIX/email/username" "SMTP username (for Gmail App Password)"
put_param "$PREFIX/email/password" "SMTP password or Gmail App Password"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ All secrets stored in SSM!             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Verify with:"
echo "  aws ssm get-parameters-by-path --path $PREFIX --with-decryption --region $AWS_REGION"
echo ""
echo "Next: edit config/config.yaml (non-secret settings), then:"
echo "  python main.py --mode once"
