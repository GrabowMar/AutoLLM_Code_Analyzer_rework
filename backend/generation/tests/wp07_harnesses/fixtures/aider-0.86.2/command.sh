#!/bin/sh
cd /workspace || exit 70
/opt/harness/venv/bin/aider --model openai/deepseek/deepseek-v4.1-flash --weak-model openai/deepseek/deepseek-v4.1-flash --editor-model openai/deepseek/deepseek-v4.1-flash --openai-api-base http://llm-gateway:8080/api/v1 --openai-api-key sk-llmlab-dummy --edit-format diff --map-tokens 1024 --yes-always --no-auto-commits --no-dirty-commits --no-gitignore --no-check-update --analytics-disable --no-show-model-warnings --no-show-release-notes --no-suggest-shell-commands --no-detect-urls --no-pretty --no-stream --no-fancy-input --chat-history-file /logs/aider.chat.md --input-history-file /logs/aider.input.md --llm-history-file /logs/aider.llm.md --model-metadata-file /run/harness/aider-model-metadata.json --message-file /run/brief.md $(git ls-files | grep -vE '^(Dockerfile|\.dockerignore|docker-compose\.yml)$|(^|/)(package-lock\.json|uv\.lock|poetry\.lock)$')
status=$?
rm -rf .aider.tags.cache.v4
exit $status
