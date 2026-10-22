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
for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
	iptables -A DOCKER-USER -o "$uplink" -d "$cidr" -j REJECT --reject-with icmp-net-prohibited
done
iptables -A DOCKER-USER -j RETURN
echo "apps-dind: nested containers blocked from private ranges via $uplink"

# Hourly: drop images no container uses that are over a week old (removed
# apps; base images are rebuilt on demand). Build cache is capped by
# daemon.json's builder GC.
(
	while sleep 3600; do
		docker image prune --all --force --filter until=168h >/dev/null
	done
) &

wait "$dockerd_pid"
