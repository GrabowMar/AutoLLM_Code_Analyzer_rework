#!/bin/sh
# Run one rendered harness (render.py output) against the scripted upstream on an --internal docker
# network, where the upstream answers as llm-gateway:8080 and nothing else is reachable.
#   run_smoke.sh <render-dir>        -> <render-dir>/logs/ (harness + upstream evidence)
set -eu
dir=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
image=$(python3 -c "import json,sys; print(json.load(open('$dir/meta.json'))['image'])")
name=wp07smoke-$(basename "$dir")-$$
net=$name-net
rm -rf "$dir/logs" && mkdir -p "$dir/logs" && chmod 777 "$dir/logs"
docker network create --internal "$net" >/dev/null
cleanup() { docker rm -f "$name-up" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --rm --name "$name-up" --network "$net" --network-alias llm-gateway \
  -v "$here/scripted_upstream.py:/s/up.py:ro" -v "$dir/logs:/logs" --user root \
  llmlab-v3-wt/agent-base:dev python3 /s/up.py --port 8080 --log /logs/upstream.jsonl >/dev/null
sleep 1
envjson=""
if [ -f "$dir/env.json" ]; then envjson="$dir/env.json"; fi
# Copy the stage in as root, hand HOME/workspace/logs to node, then run the entry script as node.
docker run --rm --name "$name-agent" --network "$net" --memory 2g --pids-limit 512 \
  -v "$dir/stage:/stage:ro" -v "$dir/logs:/logs" --env-file "$dir/env.list" --user root \
  --entrypoint sh "$image" -c '
    cp -a /stage/. / && chown -R node:node /run/home /workspace /logs /run/harness 2>/dev/null
    exec setpriv --reuid=node --regid=node --init-groups sh /run/entry.sh'
echo "exit.json: $(cat "$dir/logs/exit.json" 2>/dev/null)"
