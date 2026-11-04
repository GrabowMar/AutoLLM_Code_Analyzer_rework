# OpenCode harness layer on the shared sandbox base (see agent-base/).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG OPENCODE_VERSION=1.18.35
# OpenCode installs `@opencode-ai/plugin@<its version>` into every config directory it loads (the global one is
# $XDG_CONFIG_HOME/opencode) unless that directory already has node_modules and a package-lock.json covering
# its package.json. Without this pre-baked copy it opens CONNECT registry.npmjs.org:443 before its first model
# call. The adapter's command sets this directory up in the fresh HOME, so no run fetches anything from npm.
# The .gitignore is the one OpenCode itself writes there.
RUN npm install -g "opencode-ai@${OPENCODE_VERSION}" \
    && mkdir -p /opt/opencode/config \
    && cd /opt/opencode/config \
    && npm install --save-exact --ignore-scripts --no-audit --no-fund "@opencode-ai/plugin@${OPENCODE_VERSION}" \
    && printf 'node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore' > .gitignore \
    && npm cache clean --force \
    && find /run/home -mindepth 1 -delete
# The installs above ran as root with HOME=/run/home and left root-owned .npm/.config/.cache/.local there;
# the harness (user node) could then not write its config dir (cp: Permission denied, exit 70). HOME stays
# empty and node-owned, as agent-base leaves it.
USER node
