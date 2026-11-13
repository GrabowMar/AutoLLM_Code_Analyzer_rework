FROM node:24-slim@sha256:4444444444444444444444444444444444444444444444444444444444444444 AS frontend
WORKDIR /src/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim@sha256:3333333333333333333333333333333333333333333333333333333333333333
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=frontend /src/frontend/dist frontend/dist
ENV PORT=8000
EXPOSE 8000
CMD ["python", "-m", "backend.main"]
