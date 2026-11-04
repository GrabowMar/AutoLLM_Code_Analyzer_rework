#!/bin/sh
cd /workspace || exit 70
for f in .gemini/settings.json .gemini/.env; do
  if [ -e "$f" ] || [ -L "$f" ]; then
    echo "llm-lab: workspace $f is Gemini CLI project config; refusing to run" >&2; exit 52
  fi
done
f=$(find . -name node_modules -prune -o -name GEMINI.md -print -quit)
if [ -n "$f" ]; then
  echo "llm-lab: workspace $f is Gemini CLI project config; refusing to run" >&2; exit 52
fi
(while :; do node /run/harness/loopback.js; sleep 0.2; done) &
sleep 0.3
exec gemini --skip-trust --approval-mode=yolo -o stream-json -m deepseek/deepseek-v4.1-flash
