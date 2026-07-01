# Automation Workflows

Automation lets you compose generation, analysis, and reporting work as a **node-based, DAG-shaped workflow**. Each pipeline you build can be triggered manually, scheduled with cron, or fanned out as a parameter-matrix batch — all from the same pipeline definition.

> Web UI: `/automation`
> API base path: `/api/automation/`

## At a glance

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ Generate     │──▶│ Analyze      │──▶│ Report       │
│ model+tmpl   │   │ ✓ Bandit     │   │ comprehensive│
│              │   │ ✓ LLM Review │   │              │
└──────────────┘   └──────────────┘   └──────────────┘
```

- **Visual editor** — drag/tap nodes, connect their handles to express `depends_on` relationships
- **Read-only view** — every saved pipeline has a canvas preview on its detail page
- **Live run overlay** — run-detail pages re-render the same canvas with node-by-node status

## The hub: `/automation`

A grid of workflow cards. Each card shows:

| Element | Meaning |
| --- | --- |
| Title | Pipeline name |
| Status badge | `draft` (slate), `active` (emerald — schedulable), `archived` (orange) |
| Tags | Free-form labels for filtering |
| Updated stamp | Relative time since last edit |
| Version | Incremented each time you save |
| Action row | Run · View · Edit · Clone · Delete (each 40×40 — touch-friendly) |

Search by name and filter by status above the grid. **Batches** and **Schedules** are no longer separate routes — they're inline on each pipeline's detail page.

## The canvas editor: `/automation/create` and `/automation/[id]/edit`

Powered by [`@xyflow/svelte`](https://svelteflow.dev/) (the Svelte port of React Flow). The route is client-only (`ssr=false`) so the canvas mounts entirely in the browser.

### Desktop layout (≥ 768 px)

```
┌────────────────────────────────────────────────────────────────┐
│  ← Back │ [name field]   ……   [JSON] [Metadata] [Save]        │
├──────────┬─────────────────────────────┬───────────────────────┤
│ Palette  │       Workflow canvas        │  Properties panel    │
│ Generate │                              │  (selected node)     │
│ Analyze  │   ○ ──▶ ○ ──▶ ○             │                      │
│ Report   │                              │  name, kind, retries │
│ Wait     │   ┌── minimap ──┐            │  + per-kind config   │
│ Notify   │   └─────────────┘            │                      │
│ Script   │                              │                      │
└──────────┴─────────────────────────────┴───────────────────────┘
```

- **Drag** a palette tile onto the canvas to drop a node
- **Click** a node to select it; the properties panel updates instantly
- **Drag** from a node's right handle to another node's left handle to express a dependency
- `Delete`/`Backspace` removes selected nodes and edges
- Pan with the mouse; pinch/scroll to zoom; the **Controls** in the corner re-fit the view

### Mobile layout (< 768 px)

Side panels are replaced by two **floating action buttons**:

- **+** (bottom-left) — opens a bottom-sheet palette where you **tap to add** a node (drag-drop is unreliable on touch)
- **⚙** (bottom-right, appears when a node is selected) — opens the properties panel as a bottom sheet

A **JSON** toggle in the toolbar gives you raw access to the DSL on either platform.

### Metadata drawer

Toggle the `Metadata` chip to edit description, status, and tags inline above the canvas — saved together with the workflow.

## Node types

Every node renders the same shell (header + handles + run-status ring) with a kind-specific body.

| Node | Accent | Required config | Optional | Notes |
| --- | --- | --- | --- | --- |
| **Generate** | violet | `model_id`, `template_slug` | `app_num`, `prompt` | Runs the LLM code generator |
| **Analyze** | blue | — | `analyzers[]`, `generation_job`, `live_target` | Spawns an `AnalysisTask` |
| **Report** | amber | (varies by type) | `template_slug`, `model_id`, `title` | See *Report types* below |
| **Wait** | slate | `seconds` | — | Sleeps the worker thread |
| **Notify** | emerald | — | `channel` (default `general`), `message` | Publishes an SSE event |
| **Script** | gray | — | `code` (operation name) | Allowlisted ops only — arbitrary code is rejected |

### The Analyze node and its tool inventory

Inside an Analyze node, individual analyzer tools render as toggleable badges grouped by category:

- **Static** — `bandit`, `eslint`, `pylint`
- **Dynamic** — `zap`, `port_scanner`
- **Performance** — `lighthouse`
- **AI** — `llm_review`

Toggle them in the properties panel; the node body updates live. See [ANALYZER_GUIDE.md](ANALYZER_GUIDE.md) for what each tool covers.

### Report types

The Report node's *Report Type* selector maps directly to `backend.reports.services.generators.GENERATORS`:

| `report_type` | Required config | Description |
| --- | --- | --- |
| `comprehensive` | — | All-purpose snapshot across analyses |
| `model_analysis` | `model_id` | Per-model performance and finding breakdown |
| `template_comparison` | `template_slug` | Compare runs sharing a template |
| `tool_analysis` | — | Aggregate per-analyzer signal |
| `generation_analytics` | — | Cohort stats on generation jobs |

### Script operations

The `script` step never executes free-form Python or shell — only operations in `_ALLOWED_SCRIPT_OPERATIONS` in `dispatchers.py` (currently `noop`, `log_message`, `record_metric`). Unknown values are silently treated as `noop`.

## Pipeline DSL format

Pipelines are stored as JSON in `Pipeline.config`. The visual editor round-trips bidirectionally with this shape:

```json
{
  "steps": [
    {
      "name": "gen_1",
      "kind": "generate",
      "order": 0,
      "config": {
        "model_id": "openai/gpt-4o",
        "template_slug": "todo-react",
        "app_num": 1,
        "_position": { "x": 100, "y": 120 }
      },
      "depends_on": [],
      "max_retries": 0
    },
    {
      "name": "analyze_1",
      "kind": "analyze",
      "order": 1,
      "config": {
        "analyzers": ["bandit", "eslint", "llm_review"],
        "live_target": false,
        "_position": { "x": 400, "y": 120 }
      },
      "depends_on": ["gen_1"],
      "max_retries": 1
    }
  ]
}
```

Key rules:

- `depends_on` references other steps **by name** (legacy id-based references still validate).
- `_position` is the canvas coordinate of the node. It lives inside `step.config` so the backend `JSONField` stores it transparently — no schema changes required.
- Unknown keys in `config` are preserved on save; dispatchers ignore what they don't need.

### Validation rules (`services.validate_pipeline_dsl`)

| Check | Trigger |
| --- | --- |
| `steps` must be a list | Reject if anything else |
| Each step needs `name` and `kind` | Empty name or kind ⇒ error |
| `kind` must be one of the six known kinds | Typos flagged with the valid set |
| Required per-kind fields | Only `generate.model_id`+`template_slug` and `wait.seconds` |
| Duplicate step `name` (or `id`) | Both flagged |
| `depends_on` references unknown step | Flagged (matched against names ∪ ids) |

Everything else is left to the dispatcher's runtime defaults.

## Pipeline detail page: `/automation/[id]`

Two side-by-side cards above the run history:

1. **Details** — version, timestamps, node/edge counts, tags
2. **Workflow** — read-only canvas (no handles, no edits) showing the current shape

Below them, the inline panels that **replaced the dedicated batches/schedules routes**:

- **Schedules** — list of cron triggers for this pipeline with enable/pause toggle and quick presets
- **Batches** — recent matrix-batch runs with a "New Batch" form that accepts arbitrary parameter JSON

Then **Run History** as a sortable table (desktop) / card stack (mobile).

The header has these one-click actions: **Refresh · Edit · Clone · Run · Delete**.

## Run detail page: `/automation/runs/[id]`

Three tabs:

| Tab | Content |
| --- | --- |
| **Workflow Canvas** | The pipeline's canvas with **live status overlay** — pending (slate ring), running (blue ring, pulsing), succeeded (emerald), failed (red), cancelled (orange) |
| **Step Timeline** | Vertical timeline with per-step status, duration, attempt count, and expandable output JSON |
| **Logs** | Aggregated log output; falls back to step output JSON if `/runs/{id}/logs/` is unavailable |

Real-time updates come from the `automation:{run_id}` SSE channel — no polling, the page re-fetches when the run emits an event.

## Mobile accessibility

| Concern | What we did |
| --- | --- |
| Touch targets | Hub action buttons sized 40 × 40; FABs 48 × 48; minimum 44 px on interactive controls |
| Drag-drop | Tap-to-add inside the bottom-sheet palette (drag stays available for desktop) |
| Side panels | Replaced by bottom sheets on `< md`; canvas takes the full viewport |
| Reduced motion | All pulse/spin indicators respect `prefers-reduced-motion`; button transforms disabled too |
| Focus visibility | `focus-visible:ring-2 focus-visible:ring-primary` on cards, tiles, and all `<Button>`s |
| Double-tap zoom | `touch-manipulation` applied globally via the `<Button>` base class |

## REST endpoints

All routes live under `/api/automation/`. Authentication is the standard session/token used elsewhere in the app.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/pipelines/` | Paginated list (filters: `status`, `tag`, `search`, `owner_me`) |
| `POST` | `/pipelines/` | Create (rejects on DSL validation failure) |
| `GET` | `/pipelines/{id}/` | Detail (includes `steps[]`) |
| `PUT` | `/pipelines/{id}/` | Update (bumps version) |
| `DELETE` | `/pipelines/{id}/` | Remove |
| `POST` | `/pipelines/{id}/clone/` | Deep-copy with new name |
| `POST` | `/pipelines/{id}/runs/` | Trigger; returns the new `PipelineRun` |
| `GET` | `/pipelines/{id}/runs/` | Run history |
| `GET` | `/runs/{id}/` | Run detail + per-step state |
| `POST` | `/runs/{id}/cancel/` | Mark cancelled |
| `POST` | `/runs/{id}/retry/` | Re-run with same params |
| `GET` | `/runs/{id}/logs/` | Aggregated logs |
| `GET/POST/DELETE` | `/schedules/`, `/schedules/{id}/` | Cron scheduling (no per-pipeline list filter — clients filter client-side) |
| `PATCH` | `/schedules/{id}/enabled/?enabled=true` | Toggle |
| `GET/POST/DELETE` | `/batches/`, `/batches/{id}/` | Parameter-matrix batches |
| `POST` | `/batches/{id}/cancel/` | Cancel all in-flight runs |

## Architectural notes

- **Frontend** — SvelteKit 2 + Svelte 5, Tailwind 4, Bits UI 2. The canvas wrapper (`WorkflowCanvas.svelte`) dynamically imports the `@xyflow/svelte` bundle only in the browser; the SvelteKit pages opt out of SSR via `+page.ts` so the editor never tries to render canvas code server-side.
- **Position storage** — node coordinates live in `step.config._position`. No backend migration was needed; dispatchers ignore the key.
- **Engine** — see [BACKGROUND_SERVICES.md](BACKGROUND_SERVICES.md) for how the runner picks pipeline runs off Celery (or daemon-thread fallback) and processes the DAG; the per-kind dispatchers live in `backend/automation/engine/dispatchers.py`.

## Where to look

| Concern | File |
| --- | --- |
| Canvas wrapper (SSR-safe) | `frontend/src/lib/components/workflow/WorkflowCanvas.svelte` |
| Custom node renderers | `frontend/src/lib/components/workflow/nodes/*.svelte` |
| Properties panel | `frontend/src/lib/components/workflow/PropertiesPanel.svelte` |
| Tap-to-add palette | `frontend/src/lib/components/workflow/NodePalette.svelte` |
| DSL ↔ flow conversion | `frontend/src/lib/utils/pipeline-flow.ts` |
| Hub page | `frontend/src/routes/(app)/automation/+page.svelte` |
| Run overlay | `frontend/src/routes/(app)/automation/runs/[id]/+page.svelte` |
| Schedules / Batches panels | `frontend/src/lib/components/workflow/{Schedules,Batches}Panel.svelte` |
| Backend DSL validation | `backend/automation/services.py::validate_pipeline_dsl` |
| Dispatchers | `backend/automation/engine/dispatchers.py` |
| Runner DAG executor | `backend/automation/engine/runner.py::execute_run` |
