#!/bin/sh
# Initialise once with a random root password that is never stored or printed, so nobody can create users or
# indexes or upload: the only index is the built-in read-only mirror root/pypi. --restrict-modify root keeps
# user and index creation to root.
set -eu
SERVERDIR=/devpi/server
if [ ! -f "$SERVERDIR/.serverversion" ]; then
	devpi-init --serverdir "$SERVERDIR" --root-passwd "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
exec devpi-server --serverdir "$SERVERDIR" --host 0.0.0.0 --port 3141 --restrict-modify root \
	--mirror-cache-expiry "${DEVPI_MIRROR_CACHE_EXPIRY:-1800}"
