# The generation library

The library holds every versioned instrument a sample depends on (spec sections 3.3, 3.4 and 9): tasks, stack
profiles, starters, styles, guidance, prompt templates, prompt sets, harness presets, sampling presets, budget
presets (resource policies), eval suites and pattern detectors. Code: `backend/generation/library/`
(service, lint, seeding, packages, API) and `backend/generation/prompts/` (renderer, render contexts, API).

## Versions are immutable

* Every entity is immutable per `(slug, version)` and carries `content_hash = H({kind, schema, content})` over its
  normalized content. Editing creates version+1 with `derived_from` = the base; archiving replaces deleting.
* Content may name other entities as `slug@v`; they are stored pinned, `slug@v#hash12`, so a content hash commits
  to what it references. A pinned ref whose hash does not match is refused (409 `hash_mismatch`).
* Studies pin exact versions. "Latest" is only ever chosen at plan time, never at run or evaluation time.

## Ownership and visibility

* A user sees system, shared and their own entities; staff see everything.
* A slug belongs to the creator of its latest version. Only its owner (or staff) may create versions; anyone who
  can see an entity may **fork** it into a new slug they own (`derived_from` set; a forked budget preset becomes
  tier `custom`).
* System slugs are written only by `seed_library`; edit one by forking it. Archive and visibility changes apply to
  every version of a slug (archive one version with `{archived, version}`).

## Lint

`library.lint.lint(kind, content)` returns registered issues (`lint_*` codes in `contracts/issues.py`). A create or
version with `error` issues is refused with 422 and the issues; warnings are shown. Rules by kind:

| Kind | Rules |
|---|---|
| every kind | content validates; every ref exists and names the right kind and role |
| task | feature vocabulary and CWE surfaces (`data/tasks/vocab.yaml`); unique ids; entity, endpoint and owner references; `auth_flow`; system tasks have all three prose variants; standard and exhaustive prose mention every endpoint as `METHOD /path`; minimal prose at most 120 words; no forbidden phrases (`evaluat`, `test suite`, `graded`, `admin/admin`, `default password`); checks stated in no variant (info) |
| prompt | the template parses; only whitelisted filters; declared, used and documented variables agree (fragments may read only the base variables) |
| prompt_set | the roles its mode needs; app sets pin every `auth_contract.*` and `network.*` fragment; role, mode and fragment-role agreement |
| stack, style, guidance | fragment refs are `fragment` templates; a security hint other than `none` has a fragment; known frontends |
| starter | every file blob is stored; the composer's check passed; WP-05's rule list (`build/starter_rules.py`: forbidden content, denied security libraries, digest-pinned `FROM`s, lockfiles, the tree matching its stack) |
| detector | a regex compiles; a Semgrep rule file parses with a non-empty `rules` list |

**Statedness** (section 8.2). Each check is tagged with the prose variants that state it
(`TaskSpecVersion.derived.check_statedness`): the variant must mention the check's endpoint, and then, by category,
the behaviour (auth, ownership, pagination and filter keywords; for validation and negative checks the failing
status code or its wording). The table is versioned (`STATEDNESS_VERSION`); `lint.tag_checks` tags compiled checks.

## Prompts

Templates use the engine `jinja2-sandbox@1`: a sandboxed Jinja with `StrictUndefined`, no autoescape, item lookup
only (no attribute or method access), the filters `indent, join, upper, lower, title, trim, wordwrap, replace,
length, sort, default, tojson, bullet_list, endpoint_table`, and only `range` as a global. Output is NFC, LF and
ends with exactly one newline. Rendering is deterministic.

The render context (`GET /prompts/variables/?mode=app|slot` documents it) holds `task`, `stack`, `starter`, `style`,
`auth_contract`, `guidance`, `harness`, `network`, `allowed_bases`, `budget_neutral` and, in slot mode, `slot` and
`context`; `stack_fragment`, `style_fragment`, `guidance_fragment` and `run_contract` are aliases. Fragment texts
are rendered from the pinned templates the entities and the prompt set name, with the same base context. The
context never holds budget numbers, seeds or ids: the only policy value it reads is the `network_policy` enum, so
two budget presets render the same stimulus (a test pins this).

Every rendered span carries its owner (`sources`: template ref, owner kind and ref, role, character span); the
spans partition the text. `POST /prompts/render/` previews a prompt set (and draft templates) for one cell's levels;
`POST /prompts/diff/` diffs two renders per role.

**Editing rendered text.** An edit of a span is saved by `library.service.fork_for_override(owner_kind, owner_ref,
new_template, ...)`: the template that owns the text is forked, then the entity that pins it (prompt set, stack,
style or guidance), and the matching study factor level is swapped (`OVERRIDE_SWAPS`). A user's own slug gets a new
version; anything else is forked to `<slug>--<hash8>`. The same edit made twice reuses the forks.

## Seeding

`manage.py seed_library [--data-dir DIR] [--kind K] [--dry-run] [--strict] [--json]` loads
`backend/generation/data/` (setting `GENERATION_LIBRARY_DATA_DIR`) in dependency order:

* an unchanged content hash is a no-op; a new one creates version+1 as a system entity;
* a slug owned by a user is never touched (skipped and reported); system content may not reference user entities;
* starters listed in `data/starters/compose.yaml` are composed by `build.starters.compose` (phase-B entries are
  reported as deferred until their stacks exist); `starters/<slug>/starter.yaml` + `files/` is a pre-composed starter;
* `name`, `description` and `is_archived` follow the files; seed files may write `slug@latest` to pin the newest
  system version at seed time;
* entries with lint errors are seeded and reported (`--strict` fails on them), so one bad file does not cascade.

A second run reports `changes: 0`. With `GENERATION_SEED_ON_MIGRATE=true` the post-migrate hook seeds after
`migrate` (off by default; tests seed explicitly).

`manage.py lint_library [--data-dir DIR] [--db] [--kind K] [--fail-on error|warn]` lints the seed files in memory
(no database writes; refs resolve to the other files) or, with `--db`, the stored latest versions.

## API summary

Library (`/api/generation/library/`): list (`?q=&tag=&archived=&mine=&page=&page_size=`, latest visible version
per slug), create, detail, one version, new version (409 `stale_base`; `unchanged: true` for an equal hash),
archive, visibility, validate, usage, diff, fork, task oracle (read; queue for staff), starter tree, blobs, and
package export/import ([TEMPLATE_PACKAGES.md](TEMPLATE_PACKAGES.md)). Prompts (`/api/generation/prompts/`):
variables, render, diff. Errors are `{detail, code, issues?}`; operation ids are `library_*` and `prompts_*`.
