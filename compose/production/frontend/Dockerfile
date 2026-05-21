FROM node:22-slim AS builder

WORKDIR /app/frontend

COPY ./frontend/package.json ./frontend/package-lock.json ./
RUN npm ci

COPY ./frontend ./
RUN npm run build

# ---

FROM node:22-slim

WORKDIR /app

COPY --from=builder /app/frontend/build ./build
COPY --from=builder /app/frontend/package.json ./

ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000

CMD ["node", "build"]
