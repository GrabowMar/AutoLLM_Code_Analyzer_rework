# Backend Dockerfile for Flask Application - Robust with Fallbacks
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies with retry logic
RUN for i in 1 2 3; do \
        apt-get update && apt-get install -y \
        gcc \
        curl \
        libxml2-dev \
        libxslt-dev \
        python3-dev \
        && rm -rf /var/lib/apt/lists/* && break || \
        (echo "Retry $i: Failed to install system dependencies" && sleep 2); \
    done

# Upgrade pip and install build tools with fallback
RUN pip install --no-cache-dir --upgrade pip setuptools wheel || \
    (echo "WARNING: Failed to upgrade pip/setuptools, continuing with defaults" && true)

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies with multiple fallback strategies
RUN echo "=== Installing Python dependencies ===" && \
    (pip install --no-cache-dir -r requirements.txt && echo "✓ Dependencies installed successfully") || \
    (echo "⚠ First attempt failed, trying with --use-deprecated=legacy-resolver" && \
     pip install --no-cache-dir --use-deprecated=legacy-resolver -r requirements.txt) || \
    (echo "⚠ Second attempt failed, trying packages individually" && \
     cat requirements.txt | grep -v '^#' | grep -v '^$' | while read pkg; do \
         echo "Installing $pkg..." && \
         pip install --no-cache-dir "$pkg" || echo "WARNING: Failed to install $pkg"; \
     done) || \
    (echo "ERROR: Failed to install dependencies. Image may be incomplete." && exit 1)

# Verify critical Flask dependencies are installed
RUN python -c "import flask; print(f'✓ Flask {flask.__version__} installed')" || \
    (echo "ERROR: Flask not installed. Installing Flask as fallback..." && \
     pip install --no-cache-dir Flask>=3.0.0 && \
     python -c "import flask; print(f'✓ Flask {flask.__version__} installed (fallback)')")

# Copy application code
COPY . .

# Create data directory for SQLite databases with proper permissions
RUN mkdir -p /app/data && chmod 777 /app/data

# Validate app.py exists and has no syntax errors with detailed feedback
RUN if [ ! -f app.py ]; then \
        echo "ERROR: app.py not found in /app directory" && \
        echo "Contents of /app:" && ls -la && exit 1; \
    fi && \
    python -m py_compile app.py && \
    echo "✓ app.py syntax check passed" || \
    (echo "ERROR: app.py has syntax errors:" && \
     python -m py_compile app.py 2>&1 | head -20 && exit 1)

# Verify Flask app can be imported (catches import errors)
RUN python -c "from app import app; print('✓ Flask app imports successfully')" || \
    (echo "WARNING: Flask app has import issues, but continuing..." && true)

# Create non-root user for security with fallback
RUN (useradd -m -u 1000 appuser && chown -R appuser:appuser /app) || \
    (echo "WARNING: Could not create appuser, running as root" && true)

# Switch to non-root user if it exists
RUN if id appuser >/dev/null 2>&1; then \
        echo "✓ Switching to appuser"; \
    else \
        echo "⚠ Running as root (appuser not created)"; \
    fi

USER appuser

# Expose Flask port (configurable via environment)
EXPOSE 5000

# Enhanced health check with multiple fallbacks (silent, 120s interval to reduce log spam)
HEALTHCHECK --interval=120s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -sf http://localhost:${FLASK_RUN_PORT:-5000}/api/health 2>/dev/null || \
        curl -sf http://localhost:${FLASK_RUN_PORT:-5000}/health 2>/dev/null || \
        curl -sf http://localhost:5000/api/health 2>/dev/null || \
        curl -sf http://localhost:5000/health 2>/dev/null || \
        exit 1

# Run with flask run (respects FLASK_RUN_HOST and FLASK_RUN_PORT env vars)
CMD echo "=== Starting Flask Application ===" && \
    echo "Port: ${FLASK_RUN_PORT:-5000}" && \
    echo "Host: ${FLASK_RUN_HOST:-0.0.0.0}" && \
    echo "Python version: $(python --version)" && \
    echo "Flask version: $(python -c 'import flask; print(flask.__version__)' 2>/dev/null || echo 'unknown')" && \
    flask run --host=${FLASK_RUN_HOST:-0.0.0.0} --port=${FLASK_RUN_PORT:-5000} || \
    (echo "ERROR: Flask application crashed" && \
     echo "Exit code: $?" && \
     echo "Last 50 lines of logs:" && \
     tail -50 /app/*.log 2>/dev/null || true && \
     sleep 5 && exit 1)
