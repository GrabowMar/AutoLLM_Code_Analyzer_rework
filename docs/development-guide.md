# Development Guide

> **Summary**: Local development setup, testing workflow, coding conventions, and contribution guidelines.
> **Key files**: `pytest.ini`, `requirements.txt`, `conftest.py`
> **See also**: [Quick Start Guide](QUICKSTART.md), [API Reference](api-reference.md)

Setup and development workflow for ThesisAppRework.

## Prerequisites

- **Docker Desktop** - Required for running analyzer services and full stack
- **Python 3.10+** - For local development
- **Node.js 18+** - For frontend tools (optional)
- **Git** - Version control

## Quick Setup

### Docker-First Development (Recommended)

```bash
# Clone repository
git clone https://github.com/GrabowMar/ThesisAppRework.git
cd ThesisAppRework

# Configure environment
cp .env.example .env
# Edit .env with your settings (OPENROUTER_API_KEY, etc.)

# Start full stack with Docker Compose
docker compose up -d

# View logs
docker compose logs -f web
docker compose logs -f celery-worker
```

### Local Development (Alternative)

```bash
# Clone and setup
git clone https://github.com/GrabowMar/ThesisAppRework.git
cd ThesisAppRework

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Initialize database
python src/init_db.py

# Start Flask only (no Docker)
./start.ps1 -Mode Dev
```

## Project Structure

```
ThesisAppRework/
├── src/                    # Flask application
│   ├── main.py            # Entry point
│   ├── app/
│   │   ├── factory.py     # App factory
│   │   ├── models/        # SQLAlchemy models
│   │   ├── services/      # Business logic
│   │   ├── routes/        # Web routes
│   │   └── api/           # REST API
│   └── templates/         # Jinja2 templates
├── analyzer/              # Analyzer microservices
│   ├── analyzer_manager.py
│   ├── services/          # Container services
│   └── shared/            # Shared code
├── generated/apps/        # AI-generated applications
├── results/               # Analysis results
├── tests/                 # Test suite
└── docs/                  # Documentation
```

## Development Modes

### Docker Compose (Full Stack)

**Recommended for development** - matches production environment.

```bash
# Start all services
docker compose up -d

# Rebuild after code changes
docker compose up -d --build

# View logs
docker compose logs -f web
docker compose logs -f celery-worker

# Access shell in web container
docker compose exec web bash
```

Access at http://localhost:5000

**Stack includes:**
- Flask web app (port 5000)
- Redis (Celery broker)
- Celery worker (background tasks)
- All analyzer services
- WebSocket gateway

### Local Flask Only (Fast Iteration)

For quick frontend/backend changes without Docker overhead.

```bash
./start.ps1 -Mode Dev
# Or directly:
python src/main.py
```

**Note:** Analyzer functionality requires Docker containers running separately.

```bash
# Start analyzers separately if needed
cd analyzer
docker compose up -d
```

### Interactive Menu

```bash
./start.ps1
```

Available modes:
| Mode | Description |
|------|-------------|
| `Start` | Full stack (Flask + Analyzers) |
| `Stop` | Stop all services |
| `Dev` | Development mode (Flask only, debug on) |
| `Status` | Status dashboard |
| `Logs` | Tail all logs |
| `Rebuild` | Fast incremental container rebuild |
| `CleanRebuild` | Full rebuild without cache |
| `Maintenance` | Manual cleanup (7-day orphan grace) |
| `Reload` | Hot reload for code changes |
| `Wipeout` | Full reset (WARNING: data loss) |
| `Password` | Reset admin password |
| `Health` | Check service health |

## Running Tests

### VS Code Test Explorer

Open **Testing** panel (Ctrl+Shift+T) for interactive test discovery and debugging.

### VS Code Tasks

Use "Terminal → Run Task" for common operations:

**Testing Tasks:**
- `pytest: unit tests only` ⭐ (default, ~5s)
- `pytest: smoke tests` (~10s)
- `pytest: integration tests`
- `pytest: api integration`
- `pytest: websocket integration`
- `pytest: analyzer integration` (requires Docker)
- `pytest: web ui integration`
- `pytest: all tests` (full suite, ~2-5 min)

**Analysis Tasks:**
- `analyzer: start services`
- `analyzer: stop services`
- `analyzer: service status`
- `analyzer: service health`

**Maintenance Tasks:**
- `flask: run dev server`
- `db: run migrations`
- `scripts: sync generated apps`
- `scripts: fix task statuses`

### Command Line

```bash
# Fast unit tests (recommended for development)
pytest -m "not integration and not slow and not analyzer"

# Smoke tests
pytest tests/smoke/

# Specific test file
pytest tests/unit/test_analyzer_manager.py

# With coverage
pytest --cov=src --cov-report=html
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `unit` | Fast unit tests |
| `smoke` | Critical path health checks |
| `integration` | Requires services |
| `slow` | Long-running tests |
| `analyzer` | Requires Docker analyzers |
| `api` | API endpoint tests |
| `websocket` | WebSocket protocol tests |
| `web_ui` | Web UI interaction tests |
| `async` | Asynchronous tests |

## Code Style

### Python

- Follow PEP 8
- Type hints encouraged
- Docstrings for public functions

```python
def analyze_app(model_slug: str, app_number: int) -> dict:
    """Run analysis on a generated application.
    
    Args:
        model_slug: Normalized model identifier
        app_number: Application number (1-indexed)
        
    Returns:
        Analysis results dictionary
    """
```

### Linting

```bash
# Ruff (fast)
ruff check src/

# MyPy (type checking)
mypy src/
```

## Adding a New Analyzer Tool

1. **Update service handler** in [analyzer/services/{service}/](../analyzer/services/):

```python
async def run_new_tool(self, source_path: str) -> dict:
    # Implementation
    return {"status": "success", "findings": [...]}
```

2. **Register in tool map** within service's main handler

3. **Update aggregation** in [analyzer/analyzer_manager.py](../analyzer/analyzer_manager.py):
   - Add to `_collect_normalized_tools()`
   - Add to `_aggregate_findings()` if needed

4. **Add tests** in `tests/unit/` and `tests/integration/analyzer/`

## Adding a New API Endpoint

1. **Create route** in [src/app/routes/api/](../src/app/routes/api/):

```python
@api_bp.route('/new-endpoint', methods=['POST'])
@require_auth
def new_endpoint():
    data = request.get_json()
    # Implementation
    return jsonify({"result": "..."})
```

2. **Register blueprint** (if new file) in [src/app/factory.py](../src/app/factory.py)

3. **Add tests** in `tests/integration/api/`

4. **Document** in [api-reference.md](./api-reference.md)

## Database Migrations

Using Flask-Migrate:

```bash
# Create migration
flask db migrate -m "Description"

# Apply migrations
flask db upgrade

# Or via VS Code task: "db: run migrations"
```

## Debugging

### Flask Debug Mode

Set in `.env`:
```
FLASK_DEBUG=1
LOG_LEVEL=DEBUG
```

### VS Code Launch Configurations

See `.vscode/launch.json` for debugger configs:
- **Flask App** - Debug web server
- **Pytest** - Debug tests
- **Analyzer Manager** - Debug CLI

### Common Issues

| Issue | Solution |
|-------|----------|
| Port 5000 in use | `./start.ps1 -Mode Stop` or kill process |
| Analyzer connection failed | `python analyzer/analyzer_manager.py start` |
| Database locked | Restart Flask, check for zombie processes |
| Import errors | Activate venv, reinstall requirements |

## Environment Variables

Key variables for development:

```bash
# Required
OPENROUTER_API_KEY=sk-or-v1-your-key

# Development Mode
FLASK_ENV=development
FLASK_DEBUG=1
LOG_LEVEL=DEBUG

# Task Execution
USE_CELERY_ANALYSIS=true  # Use Celery (recommended with Docker)
CELERY_BROKER_URL=redis://redis:6379/0  # Docker: redis, Local: localhost
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Analyzers
ANALYZER_ENABLED=true
ANALYZER_AUTO_START=false

# Database
DATABASE_URL=sqlite:///src/data/thesis_app.db  # Local
DATABASE_URL=sqlite:////app/src/data/thesis_app.db  # Docker

# Timeouts (seconds)
STATIC_ANALYSIS_TIMEOUT=1800
SECURITY_ANALYSIS_TIMEOUT=1800
PERFORMANCE_TIMEOUT=1800
AI_ANALYSIS_TIMEOUT=2400
TASK_TIMEOUT=1800
```

### Docker vs Local Configuration

| Variable | Docker Value | Local Value |
|----------|-------------|-------------|
| `USE_CELERY_ANALYSIS` | `true` | `false` (or `true` with Redis) |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | `redis://localhost:6379/0` |
| `DATABASE_URL` | `sqlite:////app/src/data/thesis_app.db` | `sqlite:///src/data/thesis_app.db` |
| `IN_DOCKER` | `true` | Not set |

## Git Workflow

```bash
# Feature branch
git checkout -b feature/my-feature

# Make changes, commit
git add .
git commit -m "feat: add new feature"

# Push and create PR
git push -u origin feature/my-feature
```

Commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Related

- [Architecture](./ARCHITECTURE.md)
- [Background Services](./BACKGROUND_SERVICES.md)
- [API Reference](./api-reference.md)
- [Analyzer Guide](./ANALYZER_GUIDE.md)
- [Deployment Guide](./deployment-guide.md)
- [Troubleshooting](./TROUBLESHOOTING.md)
