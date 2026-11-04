#!/bin/sh
# Offline end-to-end smoke: harness -> the real llm-gateway image (GATEWAY_CONFIG from gateway_config.py)
# -> scripted_upstream.py as a canonical chat upstream, on an --internal network. No real model is called.
#   run_gateway_smoke.sh <render-dir> <gateway-config.json> [gateway-image]
#   -> <render-dir>/logs/ (harness evidence, the gateway's calls.jsonl / gateway_events.jsonl / state.json,
#      upstream.jsonl with the translated chat requests)
set -eu
dir=$(cd "$1" && pwd)
config=$(cat "$2")
gw_image=${3:-llmlab-v3-wt/llm-gateway:dev}
here=$(cd "$(dirname "$0")" && pwd)
image=$(python3 -c "import json,sys; print(json.load(open('$dir/meta.json'))['image'])")
name=v3wt-wp07gw-$(basename "$dir")-$$
net=$name-net
rm -rf "$dir/logs" && mkdir -p "$dir/logs/gateway" && chmod -R 777 "$dir/logs"
docker network create --internal "$net" >/dev/null
cleanup() {
  docker logs "$name-up" > "$dir/logs/upstream.stderr" 2>&1 || true
  docker logs "$name-gw" > "$dir/logs/gateway.stderr" 2>&1 || true
  docker rm -f "$name-up" "$name-gw" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  # The gateway writes as its own uid: hand the evidence back to the caller.
  docker run --rm --network none -v "$dir/logs:/l" --user root llmlab-v3-wt/agent-base:dev \
    chown -R "$(id -u):$(id -g)" /l >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run -d --rm --name "$name-up" --network "$net" --network-alias upstream \
  -v "$here/scripted_upstream.py:/s/up.py:ro" -v "$dir/logs:/logs" --user root \
  llmlab-v3-wt/agent-base:dev python3 /s/up.py --port 8080 --log /logs/upstream.jsonl ${SMOKE_UPSTREAM_ARGS:-} >/dev/null
docker run -d --name "$name-gw" --network "$net" --network-alias llm-gateway \
  -e GATEWAY_CONFIG="$config" -e OPENROUTER_API_KEY=sk-scripted -e GATEWAY_CONNECT_ALLOWLIST=upstream \
  -v "$dir/logs/gateway:/logs" "$gw_image" >/dev/null
sleep 1.5
extra=""
if [ -f "$dir/env.extra" ]; then extra="--env-file $dir/env.extra"; fi
docker run --rm --name "$name-agent" --network "$net" --memory 2g --pids-limit 512 \
  -v "$dir/stage:/stage:ro" -v "$dir/logs:/logs" --env-file "$dir/env.list" $extra --user root \
  --entrypoint sh "$image" -c '
    (cd /stage && tar -cf - .) | tar -xf - -C / --no-same-owner --no-overwrite-dir
    chown -R node:node /run/home /workspace /run/harness 2>/dev/null
    chown node:node /logs
    exec setpriv --reuid=node --regid=node --init-groups sh /run/entry.sh'
echo "exit.json: $(cat "$dir/logs/exit.json" 2>/dev/null)"
