#!/bin/sh
cd /workspace || exit 70
exec claude --bare -p --output-format stream-json --verbose --no-session-persistence --permission-mode bypassPermissions --model deepseek/deepseek-v4.1-flash --disallowedTools WebFetch,WebSearch,Task,Agent --max-turns 1 --effort low
