#!/bin/sh
cd /workspace || exit 70
exec opencode run --format json --pure --title llm-lab-run
