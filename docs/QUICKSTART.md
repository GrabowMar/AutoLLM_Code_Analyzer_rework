# Quick Start Guide

> **Summary**: Installation, configuration, and first run instructions for ThesisAppRework using Docker Compose or local setup.
> **Key files**: `docker-compose.yml`, `.env`, `start.sh`
> **See also**: [Deployment Guide](deployment-guide.md), [Development Guide](development-guide.md)

Get up and running with ThesisAppRework in under 5 minutes.

## Prerequisites

Before starting, ensure you have:

- **Docker Desktop** - [Download](https://www.docker.com/products/docker-desktop/) (Required - application runs in containers)
- **Git** - [Download](https://git-scm.com/downloads)
- **Python 3.10+** - [Download](https://www.python.org/downloads/) (Optional - only for local development)

## Installation

### Docker-First Setup (Recommended)

The application is designed to run entirely in Docker containers with Celery for distributed task processing.

#### 1. Clone the Repository

```bash
git clone https://github.com/GrabowMar/ThesisAppRework.git
cd ThesisAppRework
```

#### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set your API key:
```
# Optional global fallback. Each user can also add their own key in the UI
# under Settings → API Access (recommended).
OPENROUTER_API_KEY=sk-or-v1-your-key-here
# Set to False to require every user to bring their own key.
OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True
SECRET_KEY=your-secret-key-here
```

#### 3. Start with Docker Compose

```bash
docker compose up -d
```

This starts the complete stack:
- **Nginx proxy** (ports 80, 443) - reverse proxy for external access
- Flask web application (port 5000 internal)
- Redis (Celery broker)
- Celery worker (background tasks)
- All analyzer services with replicas (4x static, 3x dynamic, 2x perf, 2x AI)
- WebSocket gateway (port 8765)

Access the application at **http://localhost** (or **https://localhost** for HTTPS)

### Local Development Setup (Optional)

For local development without Docker:

#### 1. Create Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux/macOS:**
```bash
python -m venv .venv
source .venv/bin/activate
```

#### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 3. Initialize Database

```bash
python src/init_db.py
```

## Running the Application

### Docker Compose (Production Mode)

The application runs entirely in containers with Celery for distributed task processing.

#### Start All Services

```bash
docker compose up -d
```

This starts:
- **web**: Flask application with Gunicorn
- **redis**: Task queue broker
- **celery-worker**: Background task processor
- **analyzer-gateway**: WebSocket gateway
- **static-analyzer**: Code quality and security
- **dynamic-analyzer**: Runtime security testing
- **performance-tester**: Load testing
- **ai-analyzer**: AI-powered analysis

#### View Logs

```bash
docker compose logs -f web
docker compose logs -f celery-worker
```

#### Stop Services

```bash
docker compose down
```

### Local Development Mode (Optional)

For faster iteration without Docker:

#### Interactive Menu

```bash
./start.ps1
```

Available modes:

| Mode | Description |
|------|-------------|
| `Start` | Full stack (Flask + Analyzers) in Docker |
| `Stop` | Stop all services |
| `Dev` | Development mode (Flask only, local) |
| `Status` | Status dashboard |
| `Logs` | Tail all logs |
| `Rebuild` | Fast incremental container rebuild |
| `CleanRebuild` | Full rebuild without cache |
| `Maintenance` | Manual cleanup (7-day orphan grace) |
| `Reload` | Hot reload for code changes |
| `Wipeout` | ⚠️ Reset state (DB, apps, results) |
| `Nuke` | 🔥 **Full Reset + Rebuild** (Wipeout + CleanRebuild) |
| `Password` | Reset admin password |
| `Health` | Check service health |

#### Quick Commands

| Command | Description |
|---------|-------------|
| `./start.ps1 -Mode Start` | Start full Docker stack |
| `./start.ps1 -Mode Dev` | Start Flask locally (fast) |
| `./start.ps1 -Mode Stop` | Stop all services |
| `./start.ps1 -Mode Status` | View dashboard |
| `./start.ps1 -Mode Nuke` | **Emergency reset**: Wipe everything and rebuild stack |

#### Direct Python (Local Only)

```bash
python src/main.py
```

Access the application at **http://localhost:5000**

## First Analysis

### Using the Web UI

1. Navigate to http://localhost:5000
2. Log in or create an account
3. Go to **Analysis → Create New**
4. Select a model and app number
5. Choose analysis type (e.g., "comprehensive")
6. Click **Start Analysis**

### Using the CLI

```bash
# Start analyzer containers
python analyzer/analyzer_manager.py start

# Run analysis
python analyzer/analyzer_manager.py analyze openai_gpt-4 1 comprehensive

# View results
python analyzer/analyzer_manager.py status
```

## Verifying Installation

### Check Flask Web Application

```bash
curl http://localhost:5000/health
```

Expected: `{"status": "healthy"}`

### Check Docker Services

```bash
docker compose ps
```

All services should show "healthy" status:
- web
- redis
- celery-worker
- analyzer-gateway
- static-analyzer
- dynamic-analyzer
- performance-tester
- ai-analyzer

### Check Celery Worker

```bash
docker compose logs celery-worker | grep "ready"
```

Expected: `celery@<hostname> ready`

### Check Analyzers (Alternative)

```bash
# Using analyzer manager CLI
python analyzer/analyzer_manager.py health
```

## Common Issues

### Port 5000 Already in Use

```bash
# Stop all services
./start.ps1 -Mode Stop

# Or manually kill process
netstat -ano | findstr :5000
taskkill /PID <PID> /F
```

### Docker Not Running

Ensure Docker Desktop is running before starting analyzers.

### Missing API Key

If a generation or AI analysis job fails with "OpenRouter API key not
configured" or a 401 from OpenRouter, either:

- add a personal key in the web UI under **Settings → API Access** (stored
  encrypted per user, recommended), or
- set the global fallback `OPENROUTER_API_KEY` in `.env` (used when
  `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True`).

## Next Steps

- [Architecture Overview](ARCHITECTURE.md) - Understand the system design
- [Background Services](BACKGROUND_SERVICES.md) - Task execution and maintenance
- [API Reference](api-reference.md) - REST API documentation
- [Development Guide](development-guide.md) - Contributing and testing
- [Troubleshooting](TROUBLESHOOTING.md) - Common issues and solutions
