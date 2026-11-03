#!/bin/sh
cd /workspace || exit 70
exec /opt/harness/venv/bin/mini -c mini.yaml -c /run/harness/mini-llmlab.yaml -c /run/harness/mini-task.yaml --exit-immediately -o /logs/traj.json
