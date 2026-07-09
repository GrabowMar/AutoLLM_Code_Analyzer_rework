# Syntax validator for generated frontend code (JS/JSX/TS/TSX via esbuild).
# Built lazily by backend.generation.services.generation_validation, mirroring
# the analyzer-base pattern in backend/analysis/images/.
FROM node:22-alpine

RUN npm install -g esbuild@0.25.5 && npm cache clean --force

# Non-root, no capabilities needed — the container only parses files copied in.
USER node
WORKDIR /work
