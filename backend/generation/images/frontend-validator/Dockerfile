# Syntax validator for collected frontend code (JS/JSX/TS/TSX via esbuild), spec section 6.8.
# Run by backend.generation.agentic.static_checks (informational static checks; they never gate the outcome).
# The container gets the files under /work (put_archive), parses each one with esbuild and prints one JSON
# report on stdout: {"validator": "esbuild@<v>", "files": n, "errors": [{"path", "line", "column", "message"}]}.
# No network is needed at run time; the container runs with no network, all capabilities dropped.
FROM node:22-alpine

RUN mkdir -p /opt/validator \
    && cd /opt/validator \
    && npm init -y >/dev/null \
    && npm install --no-audit --no-fund esbuild@0.25.5 \
    && npm cache clean --force
COPY validate.mjs /opt/validator/validate.mjs

USER node
WORKDIR /work
ENTRYPOINT ["node", "/opt/validator/validate.mjs"]
CMD ["/work"]
