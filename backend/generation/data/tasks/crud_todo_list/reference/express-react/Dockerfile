FROM node:24-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* frontend/
RUN cd frontend \
    && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ frontend/
RUN cd frontend && npm run build

FROM node:24-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c
ENV NODE_ENV=production \
    PORT=8000
WORKDIR /app
COPY backend/package.json backend/package-lock.json* backend/
RUN cd backend \
    && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist
RUN mkdir -p data
EXPOSE 8000
CMD ["node", "backend/src/server.ts"]
