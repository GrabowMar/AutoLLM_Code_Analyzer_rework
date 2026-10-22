# Template packages

A template package is a portable JSON or YAML file of Generation Library content: app specs, content blocks, generation profiles, user-authored stacks and run presets. Packages move that content between installs and users. Starter packages, shipped with the app, use the same format. In the UI it is Library → Packages; the code is `backend/generation/services/packages/`.

## The format (v3)

```yaml
kind: llm-lab-template-package
template_package_schema_version: 3
exported_at: "2026-10-22T09:00:00+00:00"   # informational
assets:
  app_specs: []   # what to build
  blocks: []      # prompt material
  profiles: []    # blocks + stack + llm_config, for template scaffolding
  stacks: []      # user-authored stacks only; builtins ship with every install
  presets: []     # run presets: harness, sampling, budget
```

Each section is a list; a missing section counts as empty.

| Section | Fields |
| --- | --- |
| `app_specs` | `name`, `slug`, `category`, `description`, `backend_requirements`, `frontend_requirements`, `admin_requirements`, `api_endpoints`, `data_model`, `admin_api_endpoints`, `difficulty` (`""` \| `basic` \| `standard` \| `advanced`) |
| `blocks` | `block_type`, `slug`, `version`, `name`, `description`, `content`, `metadata` |
| `profiles` | `name`, `slug`, `version` (informational), `description`, `scaffolding_slug`, `block_refs: [{type, slug, version?}]`, `llm_config` |
| `stacks` | `slug`, `name`, `description`, `has_frontend`, `default_port`, `patch_profile`, `frontend_component`, `backend_filename`, `backend_base_image`, `frontend_base_image`, `server_kind` (`python` \| `uvicorn`), `files` (path → text) |
| `presets` | `kind` (`harness` \| `sampling` \| `budget`), `slug`, `name`, `description`, `config` (see [Run presets](/docs/GENERATION_PROCESS#run-presets)) |

**Older versions** are upgraded on import. v2 had no `presets`. v1 called app specs `app_templates` and profiles `bundles`, had no `stacks`, and may carry a `prompt_templates` section for a removed model, which is skipped with a warning.

**Bundle package.** A single profile and the blocks it references, `{"kind": "llm-lab-template-bundle", "bundle_package_schema_version": 1, "bundle": {...}, "blocks": [...]}`, comes from `GET /api/generation/profiles/<slug>/export/` and goes back in through `POST /api/generation/profiles/import/`. It imports under the same rules as below.

## Export

`POST /api/generation/packages/export/?format=json|yaml` with:

```json
{
  "app_template_slugs": ["crud_todo_list"],
  "bundle_slugs": ["my-profile"],
  "block_refs": [{"type": "prompt_tone", "slug": "my-tone", "version": 2}],
  "stack_slugs": ["my-stack"],
  "preset_refs": [{"kind": "harness", "slug": "aider-whole-file"}]
}
```

- Only assets you can see are exported (system rows and your own; for stacks, approved stacks and your own).
- A profile exports at its latest version and brings along the blocks it references and the **user stack it targets**, so the package imports elsewhere without a dangling stack. Builtin stacks are never exported.
- A referenced block you cannot see (another user's private block, or one that no longer exists) is left out, and the package lists it in a top-level `omitted_blocks: [{profile, type, slug, version, reason}]` (present only when something was left out; bundle packages carry it too). The profile keeps the reference, so importing it elsewhere needs that block to exist there. The Library's Packages dialog warns before the export. Creating or editing a profile refuses such refs (400 `Unknown block <slug> v<version>`).
- Presets export at their latest visible version.
- An export with nothing in it is a 400.

## Import

`POST /api/generation/packages/import/` with `{"package_text": "<the JSON or YAML>", "conflict_strategy": "rename" | "overwrite" | "error"}`.

The response names the rows the package resolved to, created or reused, per section: `{"app_specs": [slug], "blocks": ["slug:version"], "profiles": [slug], "stacks": [slug], "presets": ["kind/slug:version"]}`. Any problem is a `400 {detail}` and nothing is written, because the whole import is one transaction.

Assets import in dependency order: app specs → blocks → stacks → profiles → presets. Everything is validated as if it were created through the API:

- **Profiles.** `llm_config` follows the same rules as `POST /profiles/`. Every block ref must resolve to a block in the package or a block you can see. A ref without a `version` takes the package's own block of that slug, else the latest system version, else your latest own version. A ref without a `type` takes the block's type. A missing `scaffolding_slug` means `GENERATION_DEFAULT_STACK`.
- **Stacks** are re-validated against this install's base-image allowlist and file rules, always use a generated Dockerfile, and go through the approval queue like locally created ones.
- **Presets** are normalized and validated like `POST /presets/`. For example, a sampling preset may not set `provider`, `stop` or `response_format`, and options must be ones the harness offers.

### Conflicts

One rule holds for every asset: content you can already see that is **identical** is reused, never duplicated. Otherwise the strategy decides:

| Asset | `rename` | `overwrite` (your own item) | `error` |
| --- | --- | --- | --- |
| App spec (one mutable row per slug) | new slug `slug-2` | updated in place | refused |
| Block (`slug` + `version`) | new slug, same version | **new version** of the slug; the package's profiles follow it | refused |
| Profile | new slug, version 1 | **new version** | refused |
| Stack | new slug `slug-2` | renamed too (stack versions are immutable) | refused |
| Preset | new slug `slug-2` | **new version**; a name/description difference alone updates the metadata | refused (`slug already in use`) |

- `overwrite` only ever changes your own items. A conflict with a system row or another user's row is renamed under `overwrite` as well.
- A stack renamed on import is **remapped** into the imported profiles' `scaffolding_slug`, as renamed blocks are remapped into their `block_refs`.
- Your own identical stack is reused, so re-importing your own export does not multiply stacks.
- **Preset slugs are global per kind** (any owner) and `*-default` plus the system preset slugs are reserved. A taken or reserved slug is renamed (`team-default` → `team-default-2`) or, with `error`, refused without saying whose slug it is. Renaming never splits results: method keys hash a preset's content, not its slug.
- A harness preset whose options are all at their defaults *is* that harness's system preset (`aider-default`, …), which is reused.
- A preset that is identical to the latest version of the same slug (system or your own) is reused. An archived preset is never reused: the import creates a renamed copy, or refuses under `error`.

## Starter packages

Starters are files in `backend/generation/data/packages/<slug>.yaml`: a v3 package plus a header.

```yaml
kind: llm-lab-template-package
template_package_schema_version: 3
starter:
  slug: fastapi-stack-starter       # must equal the file name
  name: FastAPI starter pack
  description: ...
assets:
  app_specs:
    - content_file: requirements/analytics_campaign_monitor.json
  profiles:
    - slug: system-fastapi-vue-standard
      name: FastAPI + Vue standard
      scaffolding_slug: fastapi-vue
      block_refs:
        - {type: prompt_stage, slug: fastapi-backend-system}   # no version
```

Two shortcuts keep a starter from copying data that seeding already reads, so it cannot drift from it:

- `content_file: <path relative to backend/generation/data/>`. An app spec entry *is* that JSON or YAML file, and inline keys override it. A block entry takes its `content` from the file. Paths must stay inside `data/`. Only starters can use this; an uploaded package can never read server files.
- **Block refs without a version** resolve at import time to the latest system version of the slug, the same version the seeded system profiles pin.

| Endpoint | |
| --- | --- |
| `GET /api/generation/packages/starters/` | `[{slug, name, description, app_spec_count, block_count, profile_count, stack_count, preset_count, app_template_count, bundle_count}]`, by slug. `app_template_count` and `bundle_count` are the pre-v3 names of the app-spec and profile counts |
| `POST /api/generation/packages/starters/<slug>/import/` | `{conflict_strategy}`. Imports like an uploaded package; an unknown slug is a 404 |

Shipped starters:

| Starter | Contents |
| --- | --- |
| `agent-benchmark-starter` | Run presets for agent comparisons. Harness: `aider-whole-file` (`edit_format: whole`), `aider-no-repo-map` (`map_tokens: 0`), `mini-long-commands` (`command_timeout_seconds: 120`). Sampling: `temperature-zero` (`temperature: 0`), `reasoning-high` (`reasoning: {effort: high}`). Budget: `smoke` (30 requests, 60 min, $3) and `extended` (250 requests, 180 min, $25), whose USD and wall-clock caps stay generous safety nets so the request budget binds |
| `fastapi-stack-starter` | Three richer app specs (campaign analytics, incident centre, subscription billing) and the FastAPI + React / FastAPI + Vue profiles, built from the system blocks. A default install already seeds all of it, so importing reuses those rows |

Because preset slugs are global, a second user importing `agent-benchmark-starter` on the same install gets `aider-whole-file-2` and so on. Their runs still share method keys with the first user's.

To add a starter, drop a file into `data/packages/` and run the lint.

## Lint

`just manage lint_generation_data` (also run by the test suite through `backend/generation/tests/test_data_lint.py`) checks generation data without a database:

- **Starters:** the current schema version, a header whose slug is the file name, `content_file` paths that exist inside `data/`, app specs valid against `data/requirements/schema.json`, and block refs that name system blocks by slug (versions only for the package's own blocks) with the blocks' real types. Profiles must use known stacks and a valid `llm_config`, and a profile that reuses a `catalog.yaml` slug must equal that entry, or import would duplicate it. Presets must be valid, unreserved and unique, and harness presets must change at least one option.
- **System presets** (`data/presets/`): valid configs, files named `<kind>-<slug>.yaml`, and a default preset for every harness, for sampling and for budget.
- **Catalog and manifests** (`data/blocks/catalog.yaml`, `data/requirements/manifests/`): refs to system blocks by slug only, at most one default profile per stack, a `default_bundle_slug` that names a catalog profile, and known apps and base profiles.
- **Requirement specs** and the stage/role coverage of every frontend stack's catalog profiles.
