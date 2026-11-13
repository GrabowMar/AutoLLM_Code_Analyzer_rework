#!/bin/sh
# Start dockerd via the stock dind entrypoint, then fence nested containers off
# from private address space: the dev stack's own network (postgres, redis,
# django), the host's other Docker networks and link-local cloud metadata.
# Public internet stays reachable (package installs during builds), and replies
# to connections Django opens into an app's published port are still allowed.
set -eu

dockerd-entrypoint.sh "$@" &
dockerd_pid=$!
trap 'kill -TERM "$dockerd_pid" 2>/dev/null; wait "$dockerd_pid"' TERM INT

# DOCKER-USER exists once dockerd has set up its firewall chains.
until iptables -nL DOCKER-USER >/dev/null 2>&1; do
	if ! kill -0 "$dockerd_pid" 2>/dev/null; then
		wait "$dockerd_pid"
		exit $?
	fi
	sleep 1
done

uplink=$(ip -4 route show default | awk '{print $5; exit}')
iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
# Narrow exception: only gateways of non-OpenRouter agent jobs (attached to the
# dedicated nested network llmlab-gw-egress) may reach the executor's internal
# LLM proxy, on one IP and one port. Anything else bound for the llmproxy
# network is rejected: it is a separate interface, so the uplink REJECTs below
# would not cover it.
if [ -n "${LLM_PROXY_ADDR:-}" ] && [ -n "${LLM_GATEWAY_EGRESS_SUBNET:-}" ]; then
	iptables -A DOCKER-USER -i llmlab-gwegr -s "$LLM_GATEWAY_EGRESS_SUBNET" -d "$LLM_PROXY_ADDR" -p tcp --dport "${LLM_PROXY_PORT:-8090}" -j RETURN
	iptables -A DOCKER-USER -d "${LLMPROXY_SUBNET:-172.30.99.0/29}" -j REJECT --reject-with icmp-net-prohibited
	# apps-dind's own llmproxy address is reached via INPUT, not FORWARD: refuse it from nested bridges
	iptables -A INPUT -i br+ -d "${APPS_DIND_LLMPROXY_ADDR:-172.30.99.3}" -j REJECT
	iptables -A INPUT -i docker0 -d "${APPS_DIND_LLMPROXY_ADDR:-172.30.99.3}" -j REJECT
	iptables -A INPUT -i llmlab-gwegr -d "${APPS_DIND_LLMPROXY_ADDR:-172.30.99.3}" -j REJECT
fi
# Dependency caches (WP-05, docs/STACKS.md): only sandbox gateways (llmlab-gw-egress) may reach the read-only
# caches on the internal depcache network, on their four service ports; agents reach them through their
# gateway's forward proxy (proxy_hosts), never directly. Everything else bound for that network is refused
# (a separate interface: the uplink REJECTs below would not cover it), apps-dind's own address included.
if [ -n "${DEPCACHE_SUBNET:-}" ] && [ -n "${LLM_GATEWAY_EGRESS_SUBNET:-}" ]; then
	iptables -A DOCKER-USER -i llmlab-gwegr -s "$LLM_GATEWAY_EGRESS_SUBNET" -d "$DEPCACHE_SUBNET" -p tcp -m multiport --dports 4873,3141,3000,8080 -j RETURN
	iptables -A DOCKER-USER -d "$DEPCACHE_SUBNET" -j REJECT --reject-with icmp-net-prohibited
	for iface in br+ docker0 llmlab-gwegr; do
		iptables -A INPUT -i "$iface" -d "$DEPCACHE_SUBNET" -j REJECT
	done
fi
for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
	iptables -A DOCKER-USER -o "$uplink" -d "$cidr" -j REJECT --reject-with icmp-net-prohibited
done
iptables -A DOCKER-USER -j RETURN
echo "apps-dind: nested containers blocked from private ranges via $uplink"

# Opt-in (APPS_DIND_IMAGE_PRUNE=1): hourly, drop images no container uses that
# are over a week old. Off by default: evaluation suites and stack bases pin
# images by digest (eval-tester, adherence-checker, node/python bases), no
# container holds them between runs, and a pruned one fails every later
# evaluation with image_digest_mismatch (12 runs on 2026-11-10). Build cache is
# capped by daemon.json's builder GC; sample artifacts have reference-aware
# TTL/size GC in the executor.
if [ "${APPS_DIND_IMAGE_PRUNE:-0}" = "1" ]; then
	(
		while sleep 3600; do
			docker image prune --all --force --filter until=168h --filter 'label!=llmlab.sample_image=true' >/dev/null
		done
	) &
fi

wait "$dockerd_pid"
