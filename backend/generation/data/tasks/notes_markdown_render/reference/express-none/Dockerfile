FROM node:24-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c
ENV NODE_ENV=production \
    PORT=8000
WORKDIR /app
COPY backend/package.json backend/package-lock.json* backend/
RUN cd backend \
    && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY backend/ backend/
RUN mkdir -p data
EXPOSE 8000
CMD ["node", "backend/src/server.ts"]
