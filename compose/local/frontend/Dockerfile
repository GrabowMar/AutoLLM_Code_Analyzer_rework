FROM python:3.15-slim AS jupyterlite

WORKDIR /lite/frontend
COPY ./frontend/jupyterlite ./jupyterlite
RUN ./jupyterlite/build.sh /opt/jupyterlite-venv

FROM node:26-slim

WORKDIR /app/frontend

# Run as the image's uid-1000 `node` user so files the dev server writes into the
# bind mount (.svelte-kit/, vite cache) stay editable from the host/devcontainer.
RUN chown node:node /app/frontend
COPY --from=jupyterlite --chown=node:node /lite/frontend/static/jupyterlite /opt/jupyterlite
COPY --chmod=755 ./compose/local/frontend/start /usr/local/bin/start-frontend
USER node

# Install dependencies
COPY --chown=node:node ./frontend/package.json ./frontend/package-lock.json ./
RUN npm ci

# Copy source
COPY --chown=node:node ./frontend ./

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/start-frontend"]

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080"]
