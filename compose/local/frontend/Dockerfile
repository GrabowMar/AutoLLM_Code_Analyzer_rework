FROM node:26-slim

WORKDIR /app/frontend

# Run as the image's uid-1000 `node` user so files the dev server writes into the
# bind mount (.svelte-kit/, vite cache) stay editable from the host/devcontainer.
RUN chown node:node /app/frontend
USER node

# Install dependencies
COPY --chown=node:node ./frontend/package.json ./frontend/package-lock.json ./
RUN npm ci

# Copy source
COPY --chown=node:node ./frontend ./

EXPOSE 8080

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080"]
