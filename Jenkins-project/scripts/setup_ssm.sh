#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"

aws ssm put-parameter \
  --name "/jenkins-ai-agent/jenkins/url" \
  --type "String" \
  --value "http://your-jenkins-host:8080" \
  --overwrite \
  --region "$REGION"

aws ssm put-parameter \
  --name "/jenkins-ai-agent/jenkins/username" \
  --type "String" \
  --value "your-jenkins-username" \
  --overwrite \
  --region "$REGION"

aws ssm put-parameter \
  --name "/jenkins-ai-agent/jenkins/api_token" \
  --type "SecureString" \
  --value "your-jenkins-api-token" \
  --overwrite \
  --region "$REGION"

aws ssm put-parameter \
  --name "/jenkins-ai-agent/gchat/webhook_url" \
  --type "SecureString" \
  --value "https://chat.googleapis.com/your-webhook" \
  --overwrite \
  --region "$REGION"

echo "SSM parameters configured."
