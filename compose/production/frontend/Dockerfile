# JupyterLite site for the report pages' Notebook tab (static files; Pyodide loads from the CDN).
FROM python:3.13-slim AS jupyterlite

WORKDIR /lite/frontend
COPY ./frontend/jupyterlite ./jupyterlite
RUN ./jupyterlite/build.sh /opt/jupyterlite-venv

# ---

FROM node:26-slim AS builder

WORKDIR /app/frontend

COPY ./frontend/package.json ./frontend/package-lock.json ./
RUN npm ci

COPY ./frontend ./
COPY --from=jupyterlite /lite/frontend/static/jupyterlite ./static/jupyterlite
RUN npm run build

# ---

FROM node:26-slim

WORKDIR /app

COPY --from=builder /app/frontend/build ./build
COPY --from=builder /app/frontend/package.json ./

ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000

CMD ["node", "build"]
