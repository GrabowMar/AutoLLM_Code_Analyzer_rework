# Library packages (format v4)

A library package is a portable YAML file of versioned library entities: tasks, stacks, starters, styles,
guidance, prompt templates, prompt sets, harness, sampling and budget presets, eval suites and detectors.
Packages move entities between installs and accounts with their content hashes intact. The code is
`backend/generation/library/packages/`; the endpoints are `POST /api/generation/library/packages/export/` and
`POST /api/generation/library/packages/import/` (spec section 9.1). See [LIBRARY.md](LIBRARY.md) for the entities
themselves.

The v1-v3 *template packages* (app specs, content blocks, profiles, v2 stacks, run presets) describe v2 entities
that no longer exist. They are refused on import with a clear error; there is no converter (clean break, D3).

## The format

```yaml
kind: llm-lab-library-package
package_schema_version: 4
exported_at: "2026-11-03T09:00:00+00:00"   # informational
entities:                                   # dependency order: prompts first, tasks last
  - kind: prompt
    slug: stack-flask-react
    version: 1
    name: Stack fragment, Flask + React
    description: ""
    schema_version: 1
    content_hash: 3f1c...                     # H({kind, schema, content}); verified on import
    content: {role: fragment, engine: jinja2-sandbox@1, template: "...", variables: [], modes: [app, slot]}
    derived_from: null                        # "slug@v" of the version it was edited from (informational)
    archived: false
  - kind: stack
    slug: flask-react
    version: 1
    content: {..., brief_fragment: "stack-flask-react@1#3f1c0a9b2d4e"}   # refs are always pinned
    ...
  - kind: resource_policy
    tier: custom                              # budget presets only
    ...
blobs:                                        # every starter file, base64
  - {sha256: 9a0b..., media_type: application/octet-stream, data: "RlJPTSBweXRob24..."}
omitted:                                      # referenced entities the exporter could not see
  - {ref: "prompt:private-fragment@2#...", reason: "missing or not visible to you"}
```

`content` is the stored, normalized content (contract defaults filled in), so its hash is reproducible. Refs
inside content (`brief_fragment`, `fragment`, `stack_profile`, prompt-set `roles` and `fragments`) are pinned
`slug@v#hash12`.

## Export

`POST /library/packages/export/` with `{refs: ["kind:slug@v", ...], include_dependencies: true}` returns the
YAML (`application/x-yaml`, as an attachment). Every requested ref must be visible to you (else 404). With
`include_dependencies` (the default) every entity the requested ones reference is added, transitively, plus
every blob a starter needs. A dependency you cannot see (another user's private template) is not exported; it is
listed in `omitted` without saying whether it is private or missing.

## Import

`POST /library/packages/import/?mode=dry_run|apply&on_conflict=rename|error` with the YAML as a multipart `file`
(or as the raw request body). The response is `{mode, created, reused, conflicts}`; each row is
`{kind, ref, as}` (the package ref and the ref actually used) and conflicts add `reason` and `resolution`.

Before anything is written the importer verifies every `content_hash` against its content and every blob against
its sha256 (400 on a mismatch: the file was edited by hand). Then, in one transaction and in dependency order:

| Situation | Result |
|---|---|
| You can see an entity with the same kind, slug and content hash, or a system entity with the same content hash | **reused** (nothing is created) |
| The slug is free | **created** as v1, owned by you, private |
| The slug is yours | **created** as the next version (`derived_from` = your latest) |
| The slug belongs to someone else or to the system library | **conflict**: renamed to `<slug>-imported` (`-2`, ...) with `on_conflict=rename` (default); with `on_conflict=error` nothing is imported (409 with the conflict list) |

Refs inside imported content are rewritten to the rows actually used, so a renamed or reused dependency keeps the
pinned hashes true. Several versions of one slug in a package become consecutive versions. An entity whose content
fails validation or has lint errors aborts the import (422 with the issues). `mode=dry_run` (the default) does all
of it and rolls back, so the response previews exactly what `apply` would do. Blobs are written in both modes
(the store is content-addressed; unreferenced blobs are collected by `gc_blobs`).

## Limits

Packages are capped at 32 MiB. Imported entities are always user-owned and private: share them afterwards with
`POST /library/{kind}/{slug}/visibility/`. System entities only come from `seed_library`.
