#!/bin/sh
cd /workspace || exit 70
mkdir -p /run/home/.config/opencode && cp /opt/opencode/config/package.json /opt/opencode/config/package-lock.json /opt/opencode/config/.gitignore /run/home/.config/opencode/ && ln -s /opt/opencode/config/node_modules /run/home/.config/opencode/node_modules || exit 70
exec opencode run --format json --pure --title llm-lab-run
