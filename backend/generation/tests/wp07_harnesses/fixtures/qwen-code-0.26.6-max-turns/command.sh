#!/bin/sh
cd /workspace || exit 70
exec qwen --bare --approval-mode yolo --auth-type openai --output-format stream-json --chat-recording false --telemetry false -m deepseek/deepseek-v4.1-flash --max-tool-calls 160 --exclude-tools web_fetch,web_search,task,agent --max-session-turns 1
