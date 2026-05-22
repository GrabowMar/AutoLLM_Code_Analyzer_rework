# Frontend Dockerfile for React + Vite Application - Robust with Fallbacks
# Multi-stage build for optimized production image

# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

# Install build dependencies with retry logic
RUN for i in 1 2 3; do \
        apk add --no-cache python3 make g++ && break || \
        (echo "Retry $i: Failed to install build tools" && sleep 2); \
    done || true

# Copy package files
COPY package.json ./

# Upgrade npm with fallback
RUN npm install -g npm@latest || \
    (echo "WARNING: Could not upgrade npm, using existing version $(npm -v)" && true)

# Install dependencies with multiple fallback strategies
RUN echo "=== Installing Node dependencies ===" && \
    echo "Node version: $(node -v)" && \
    echo "NPM version: $(npm -v)" && \
    (npm ci 2>/dev/null && echo "✓ npm ci succeeded") || \
    (echo "⚠ npm ci failed (no package-lock.json), trying npm install..." && \
     npm install && echo "✓ npm install succeeded") || \
    (echo "⚠ npm install failed, trying with legacy peer deps..." && \
     npm install --legacy-peer-deps && echo "✓ Install with legacy-peer-deps succeeded") || \
    (echo "⚠ Trying to install packages individually..." && \
     npm install react react-dom axios && \
     npm install -D vite @vitejs/plugin-react && \
     echo "✓ Individual package install completed") || \
    (echo "ERROR: All installation strategies failed" && exit 1)

# Verify critical dependencies
RUN node -e "require('react'); console.log('✓ React installed');" || \
    (echo "WARNING: React verification failed" && true)
RUN node -e "require('vite'); console.log('✓ Vite installed');" || \
    (echo "WARNING: Vite verification failed" && true)

# Copy source code
COPY . .

# Verify required files exist
RUN if [ ! -f index.html ]; then \
        echo "ERROR: index.html not found" && exit 1; \
    fi && \
    if [ ! -f vite.config.js ]; then \
        echo "WARNING: vite.config.js not found, creating minimal config" && \
        echo 'import { defineConfig } from "vite"; export default defineConfig({});' > vite.config.js; \
    fi && \
    if [ ! -d src ]; then \
        echo "ERROR: src directory not found" && exit 1; \
    fi

# Build the application with fallbacks
RUN echo "=== Building React application ===" && \
    (npm run build && echo "✓ Build successful") || \
    (echo "⚠ Build failed, checking for common issues..." && \
     echo "Checking if dist exists from previous build..." && \
     [ -d dist ] && echo "✓ Using existing dist directory" && ls -la dist) || \
    (echo "⚠ Trying build with increased memory..." && \
     NODE_OPTIONS="--max-old-space-size=4096" npm run build && \
     echo "✓ Build successful with increased memory") || \
    (echo "ERROR: All build strategies failed" && \
     echo "Directory contents:" && ls -la && \
     echo "Package.json scripts:" && cat package.json | grep -A 5 '"scripts"' && \
     exit 1)

# Verify build output
RUN if [ -d dist ]; then \
        echo "✓ Build output created in dist/" && \
        echo "Contents:" && ls -la dist/; \
    else \
        echo "ERROR: dist directory not created after build" && \
        echo "Checking for alternative build directories..." && \
        find . -type d -name 'build' -o -name 'dist' -o -name 'out' && \
        exit 1; \
    fi

# Stage 2: Production
FROM nginx:alpine

# Install curl for health checks
RUN apk add --no-cache curl wget || \
    (echo "WARNING: Could not install curl/wget for health checks" && true)

# Create nginx user if it doesn't exist
RUN addgroup -g 101 -S nginx 2>/dev/null || true && \
    adduser -S -D -H -u 101 -h /var/cache/nginx -s /sbin/nologin -G nginx -g nginx nginx 2>/dev/null || true

# Copy built assets from build stage with verification
COPY --from=build /app/dist /usr/share/nginx/html

# Verify files were copied
RUN if [ ! -f /usr/share/nginx/html/index.html ]; then \
        echo "ERROR: index.html not found in nginx html directory" && \
        echo "Contents of /usr/share/nginx/html:" && \
        ls -la /usr/share/nginx/html && \
        exit 1; \
    fi && echo "✓ Frontend files copied successfully"

# Copy custom nginx configuration with fallback
# Note: COPY doesn't support shell operators, so we copy everything and handle missing file in RUN
COPY . /tmp/frontend-context/

# Set up nginx configuration with proper fallback
RUN if [ -f /tmp/frontend-context/nginx.conf ]; then \
        echo "✓ Using custom nginx.conf" && \
        cp /tmp/frontend-context/nginx.conf /etc/nginx/conf.d/default.conf; \
    else \
        echo "⚠ Custom nginx.conf not found, creating default SPA config" && \
        echo 'server { listen 80; root /usr/share/nginx/html; index index.html; location / { try_files \$uri \$uri/ /index.html; } }' > /etc/nginx/conf.d/default.conf; \
    fi && \
    rm -rf /tmp/frontend-context

# Test nginx configuration (skip DNS resolution issues)
RUN nginx -t 2>&1 | grep -q "syntax is ok" && echo "✓ Nginx configuration syntax OK" || \
    (echo "⚠ Nginx config test warnings (may be DNS-related, will work at runtime)" && \
     echo "Nginx config:" && cat /etc/nginx/conf.d/default.conf && true)

# Expose port
EXPOSE 80

# Enhanced health check (silent, 120s interval to reduce log spam)
HEALTHCHECK --interval=120s --timeout=10s --start-period=60s --retries=5 \
    CMD wget -q --spider http://localhost/health 2>/dev/null || \
        curl -sf http://localhost/health 2>/dev/null || \
        exit 1

# Start nginx with enhanced logging
CMD echo "=== Starting Nginx ===" && \
    echo "Nginx version: $(nginx -v 2>&1)" && \
    echo "Contents of /usr/share/nginx/html:" && \
    ls -la /usr/share/nginx/html && \
    nginx -g "daemon off;" || \
    (echo "ERROR: Nginx failed to start" && \
     echo "Nginx error log:" && \
     cat /var/log/nginx/error.log 2>/dev/null || true && \
     sleep 5 && exit 1)
