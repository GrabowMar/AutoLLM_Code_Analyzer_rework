FROM node:22-slim

WORKDIR /app/frontend

# Install dependencies
COPY ./frontend/package.json ./frontend/package-lock.json ./
RUN npm ci

# Copy source
COPY ./frontend ./

EXPOSE 8080

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080"]
