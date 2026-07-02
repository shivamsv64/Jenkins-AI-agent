#!/bin/bash
# Test script: Run agent 3 times with 1-minute gaps

VENV="/Users/shivamverma/Desktop/devops-skills/devops-skills/Langhain-project/.venv/bin/python"
PROJECT_DIR="/Users/shivamverma/Desktop/devops-skills/devops-skills/Langhain-project"

cd "$PROJECT_DIR"

echo "=========================================="
echo "Test Run 1 — $(date)"
echo "=========================================="
$VENV main.py --mode once

echo ""
echo "Waiting 1 minute..."
sleep 60

echo "=========================================="
echo "Test Run 2 — $(date)"
echo "=========================================="
$VENV main.py --mode once

echo ""
echo "Waiting 1 minute..."
sleep 60

echo "=========================================="
echo "Test Run 3 — $(date)"
echo "=========================================="
$VENV main.py --mode once

echo ""
echo "=========================================="
echo "Test Complete — $(date)"
echo "=========================================="
