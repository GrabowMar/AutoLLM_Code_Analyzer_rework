# API Reference

> **Summary**: REST API endpoints, authentication, WebSocket events, and request/response formats.
> **Key files**: `src/app/routes/api/*`, `src/app/routes/websockets/api.py`
> **See also**: [Development Guide](development-guide.md)

REST API documentation for ThesisAppRework.

## Authentication

All API endpoints require Bearer token authentication:

```
Authorization: Bearer <token>
```

Generate tokens via **User → API Access** in the web UI, or see [src/app/routes/api/tokens.py](../src/app/routes/api/tokens.py).

### Verify Token

```http
GET /api/tokens/verify
```

**Response:** `200 OK` with token info, or `401 Unauthorized`.

## Automation Pipeline Endpoints

### Start Pipeline

```http
POST /api/automation/pipelines
Content-Type: application/json

{
  "name": "Optional pipeline name",
  "config": {
    "generation": {
      "mode": "generate",
      "models": ["openai_gpt-4"],
      "templates": ["crud_todo_list"],
      "options": {
        "parallel": true,
        "maxConcurrentTasks": 2
      }
    },
    "analysis": {
      "enabled": true,
      "profile": "comprehensive",
      "tools": [],
      "options": {
        "parallel": true,
        "maxConcurrentTasks": 2,
        "autoStartContainers": true,
        "stopAfterAnalysis": true
      }
    }
  }
}
```

**Response:** `201 Created` with `pipeline_id` and pipeline data.

### Get Pipeline Status

```http
GET /api/automation/pipelines/{pipeline_id}
```

### Get Detailed Pipeline Status

```http
GET /api/automation/pipelines/{pipeline_id}/details
```

### Cancel Pipeline

```http
POST /api/automation/pipelines/{pipeline_id}/cancel
```

### List Pipelines

```http
GET /api/automation/pipelines?limit=20
```

### Get Active Pipeline

```http
GET /api/automation/pipelines/active
```

### Pipeline Settings

```http
GET  /api/automation/settings
POST /api/automation/settings
GET  /api/automation/settings/{settings_id}
PUT  /api/automation/settings/{settings_id}
DELETE /api/automation/settings/{settings_id}
POST /api/automation/settings/{settings_id}/default
```

### Tools + Analyzer Containers

```http
GET /api/automation/tools
GET /api/automation/analyzer/status
POST /api/automation/analyzer/start
GET /api/automation/analyzer/health
```

## Analysis Endpoints

### Run Analysis

```http
POST /api/analysis/run
Content-Type: application/json

{
  "model_slug": "openai_gpt-4",
  "app_number": 1,
  "analysis_type": "comprehensive",
  "tools": ["bandit", "eslint"],
  "priority": "normal",
  "container_management": {
    "start_before_analysis": false,
    "build_if_missing": false,
    "stop_after_analysis": false
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model_slug` | string | Yes | Target model (e.g., `openai_gpt-4`) |
| `app_number` | int | Yes | Application number |
| `analysis_type` | string | Yes | `comprehensive`, `security`, `static`, `dynamic`, `performance`, `ai`, `unified` |
| `tools` | array | No | Specific tools to run (default: all available) |
| `priority` | string | No | `low`, `normal`, `high` (default: `normal`) |
| `container_management` | object | No | Container lifecycle options (all default `false`) |

**Response:**
```json
{
  "success": true,
  "task_id": "task_abc123",
  "message": "Analysis task created successfully",
  "data": {
    "task_id": "task_abc123",
    "model_slug": "openai_gpt-4",
    "app_number": 1,
    "analysis_type": "unified",
    "status": "pending",
    "created_at": "2025-12-13T10:00:00",
    "tools_count": 2,
    "priority": "normal",
    "container_management": {...}
  }
}
```

### Analyze Specific App

```http
POST /api/app/{model_slug}/{app_number}/analyze
Content-Type: application/json

{
  "analysis_type": "security",
  "tools": ["bandit", "safety"],
  "priority": "normal"
}
```

Shorthand for running analysis on a specific generated app.

### Get Task Status

```http
GET /api/analysis/task/{task_id}
```

**Response:**
```json
{
  "task_id": "task_abc123",
  "status": "RUNNING",
  "progress_percentage": 45,
  "started_at": "2025-12-13T10:00:00Z",
  "service_name": "static"
}
```

### Task Status Values

| Status | Description |
|--------|-------------|
| `INITIALIZING` | Task created, subtasks being created (unified analysis) |
| `PENDING` | Task created, waiting for execution |
| `RUNNING` | Currently executing |
| `COMPLETED` | Successfully finished |
| `PARTIAL_SUCCESS` | Some subtasks succeeded, some failed |
| `FAILED` | Error occurred (check `error_message`) |
| `CANCELLED` | User-cancelled or timed out |

### Get Task Results

```http
GET /api/analysis/task/{task_id}/results
```

Returns consolidated analysis results. See [Results Format](#results-format).

### Get Tool Details

```http
GET /api/analysis/results/{result_id}/tools/{tool_name}?service=static&page=1&per_page=25&severity=HIGH
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `service` | string | Filter by service (static, dynamic, performance, ai) |
| `page` | int | Page number for pagination (default: 1) |
| `per_page` | int | Items per page (default: 25) |
| `severity` | string | Filter by severity (CRITICAL, HIGH, MEDIUM, LOW, INFO) |

### Get SARIF File

```http
GET /api/analysis/results/{result_id}/sarif/{sarif_path}
```

Returns raw SARIF JSON file for a specific tool.

### List Tasks

```http
GET /api/analysis/tasks?status=COMPLETED&model=openai_gpt-4&limit=10
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status |
| `model` | string | Filter by model slug |
| `app_number` | int | Filter by app number |
| `limit` | int | Max results (default: 50) |
| `offset` | int | Pagination offset |

## Application Endpoints

### List Applications

```http
GET /api/apps
```

### Get Application

```http
GET /api/apps/{model_slug}/{app_number}
```

### Get Application Status

```http
GET /api/app/{model_slug}/{app_number}/status?force_refresh=false
```

Returns container status, port assignments, and health information.

| Parameter | Type | Description |
|-----------|------|-------------|
| `force_refresh` | bool | Force fresh Docker lookup (default: false) |

**Response:**
```json
{
  "success": true,
  "data": {
    "model_slug": "openai_gpt-4",
    "app_number": 1,
    "project_name": "openai_gpt-4_app1",
    "compose_file_exists": true,
    "docker_connected": true,
    "containers": ["backend", "frontend"],
    "states": ["running", "running"],
    "running": true,
    "docker_status": "running",
    "cached_status": "running",
    "last_check": "2025-12-13T10:00:00Z",
    "status_age_minutes": 0.5,
    "status_is_fresh": true
  }
}
```

### Container Operations

```http
POST /api/app/{model_slug}/{app_number}/start
POST /api/app/{model_slug}/{app_number}/stop
POST /api/app/{model_slug}/{app_number}/restart
POST /api/app/{model_slug}/{app_number}/build
```

Build accepts optional body:
```json
{
  "no_cache": true,
  "start_after": true
}
```

### Get Application Logs

```http
GET /api/app/{model_slug}/{app_number}/logs?lines=100
```

### Get Diagnostics

```http
GET /api/app/{model_slug}/{app_number}/diagnostics
```

Returns Docker Compose preflight checks and container status summary.

## Results Format

Analysis results follow this structure:

```json
{
  "metadata": {
    "model_slug": "openai_gpt-4",
    "app_number": 1,
    "timestamp": "2025-12-13T10:30:00Z",
    "analyzer_version": "1.0.0"
  },
  "results": {
    "task": {
      "task_id": "task_abc123",
      "started_at": "...",
      "completed_at": "..."
    },
    "summary": {
      "total_findings": 15,
      "severity_breakdown": {
        "critical": 0,
        "high": 2,
        "medium": 8,
        "low": 5
      },
      "tools_used": ["bandit", "eslint", "semgrep"]
    },
    "tools": {
      "bandit": {
        "status": "success",
        "findings_count": 3,
        "execution_time": 2.5
      }
    },
    "findings": [
      {
        "tool": "bandit",
        "severity": "medium",
        "message": "Possible SQL injection",
        "file": "app.py",
        "line": 42
      }
    ]
  }
}
```

## Health Endpoints

### System Health

```http
GET /api/health
```

### Analyzer Status

```http
GET /api/analyzer/status
```

Returns status of all analyzer containers (ports 2001-2004).

## API Modules Reference

The REST API is organized into the following modules:

| Module | Prefix | Description |
|--------|--------|-------------|
| `analysis` | `/api/analysis/` | Custom analysis operations, tool registry queries |
| `applications` | `/api/app/` | Application CRUD, container operations |
| `container_tools` | `/api/container-tools/` | Container tool operations |
| `core` | `/api/` | Health, status endpoints |
| `dashboard` | `/api/dashboard/` | Dashboard data, statistics |
| `export` | `/api/export/` | Export analysis results |
| `generation` | `/api/gen/` | App generation operations |
| `models` | `/api/models/` | Model management, provider info |
| `reports` | `/api/reports/` | Report generation and retrieval |
| `results` | `/api/results/` | Analysis results retrieval |
| `statistics` | `/api/statistics/` | Aggregated statistics |
| `system` | `/api/system/` | System operations, configuration |
| `tasks_realtime` | `/api/tasks/` | Real-time task updates, SSE |
| `templates` | `/api/templates/` | Template management |
| `tokens` | `/api/tokens/` | API token management |
| `tool_registry` | `/api/tool-registry/` | Available analysis tools |

### Key Endpoints by Module

#### Dashboard (`/api/dashboard/`)

```http
GET /api/dashboard/stats
```

Returns aggregated statistics for dashboard display.

#### Export (`/api/export/`)

```http
GET /api/export/task/{task_id}?format=json|csv|sarif
```

Export analysis results in specified format.

#### Statistics (`/api/statistics/`)

```http
GET /api/statistics/models
GET /api/statistics/tools
```

Returns aggregated analysis statistics per model or tool.

#### System (`/api/system/`)

```http
GET /api/system/config
POST /api/system/maintenance/run
```

System configuration and maintenance operations.

## Error Responses

| Status | Description |
|--------|-------------|
| `400` | Bad request (invalid parameters) |
| `401` | Unauthorized (missing/invalid token) |
| `404` | Resource not found |
| `422` | Validation error |
| `500` | Internal server error |
| `503` | Service unavailable (analyzers offline) |

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

## Rate Limits

No rate limits currently enforced. Consider implementing for production deployments.

## Task Execution

The system uses **Celery with Redis** for distributed task execution. This is the default and recommended approach for both development and production.

| Mode | Environment | Workers |
|------|-------------|---------|
| **Celery** | `USE_CELERY_ANALYSIS=true` (Default in Docker) | Configurable, scalable |
| ThreadPoolExecutor | `USE_CELERY_ANALYSIS=false` (Legacy local dev) | 8 threads |

## WebSocket API

Real-time updates available via SocketIO when `flask-socketio` is installed:

```javascript
const socket = io('/analysis');

// Progress updates
socket.on('task_progress', (data) => {
  console.log(`Task ${data.task_id}: ${data.progress}%`);
});

// Task completed
socket.on('task_completed', (data) => {
  console.log(`Task ${data.task_id} completed`);
});

// Task failed
socket.on('task_failed', (data) => {
  console.log(`Task ${data.task_id} failed: ${data.error}`);
});

// Subtask updates (for main tasks with children)
socket.on('subtask_update', (data) => {
  console.log(`Subtask ${data.subtask_id}: ${data.status}`);
});
```

### Server-Sent Events (SSE) Alternative

For clients that don't support WebSocket:

```http
GET /api/tasks/events?since=2025-12-13T10:00:00Z
```

Returns SSE stream of task updates.

## CLI Alternative

For automation without database tracking, use the analyzer CLI directly:

```bash
# Run analysis
python analyzer/analyzer_manager.py analyze <model> <app> <type> [--tools tool1,tool2]

# List recent results
python analyzer/analyzer_manager.py list-results [--model <model>] [--limit 10]

# Show specific result
python analyzer/analyzer_manager.py show-result <model> <app> [--task <task_id>]

# Batch analysis
python analyzer/analyzer_manager.py batch models.json
python analyzer/analyzer_manager.py batch-models model1,model2,model3
```

Results written to `results/{model}/app{N}/task_{id}/` only (no DB record).

## Related

- [Architecture](./ARCHITECTURE.md)
- [Background Services](./BACKGROUND_SERVICES.md)
- [Development Guide](./development-guide.md)
- [Troubleshooting](./TROUBLESHOOTING.md)
