# Slot mode (`slot@1`)

Slot mode measures **pattern prevalence** reliably: a completed agentic app (the *parent*) is frozen, the
researcher picks one *part* of it (a file, a line region, a symbol, an endpoint handler or a UI component), the
part is validated and leak-scanned, and then it is regenerated N times (25-100) while the rest of the app stays
byte-frozen. Every variant is assembled back into the parent, built, tested with the part's discriminating
checks and probes, and handed to the pattern analysis (WP-13).

Spec: `docs/design/SAMPLE_GEN_V3.md` sections 7.1-7.8, 7.10 and 9.6. Code: `backend/generation/slot/`
(WP-12). Tests: `backend/generation/tests/wp12_slot/`.

## Lifecycle

```
parent pool (usable app_agentic runs, outcome tested, per task)        slot/parents.py
  -> draw (random, seeded, stratified by parent model) or pick          slot/parents.py
  -> freeze -> SlotSet (tree hash pinned, symbol index, parent eval)   slot/freeze.py, slot/index/
  -> select a part -> SlotTarget (address, defaults, links)             slot/selection.py
  -> validate (original 3x, holed 1x) + leak scan                       slot/validate.py, slot/leak.py
  -> slot study: N variants per (condition, target)                     study (WP-10)
       single shot (llm lane, optionally batched) or restricted agent  slot/executors/
  -> assemble (slot rows only) -> evaluate (discriminating checks)      slot/assemble.py, slot/evaluate.py
  -> pattern analysis (normalize, cluster, detectors, prevalence)      patterns/ (WP-13)
```

## Parents

* **Pool**: `parents.pool(PoolQuery)` = usable `app_agentic` runs with outcome `tested` of a task (filters:
  stack, models, harnesses, studies, min pass rate). `beyond_plan` runs are never parents.
* **Draw**: `parents.draw(ParentSelection)` ranks candidates by `sha256(f"{seed}:{run id}")` (not
  `random.shuffle`, whose algorithm may change between Python versions), so a draw is reproducible forever.
  With `stratify_by="parent_model"` (default) the picks rotate over the parent models in seeded order. The dry
  draw endpoint returns the seed it used; a study derives the seed from `base_seed`.
* **Freeze**: `freeze(parent, name=..., user=..., selection=...)` checks the parent (usable, `tested`, tree
  hash set, every blob present, has a manifest; `build_fail`/`start_fail` only with `allow_unbuilt_parent`),
  builds the symbol index once per `(run, INDEX_VERSION)` and links the latest evaluation as `parent_eval`.
  Freezing copies nothing (blobs are immutable, PROTECT FKs pin the parent).

## Symbol index (`INDEX_VERSION = 1`)

Tree-sitter with the grammars pinned by `tree-sitter-language-pack==0.13.0` (python, javascript, typescript,
tsx, go, java, html, css, vue, svelte; the `<script>` blocks of Vue/Svelte files are parsed as js/ts by their
`lang`). Symbols are functions, methods, classes, components, route handlers and `<module>`; ids are
`f"{path}::{qualname}"` (`#n` for duplicates). Files with syntax errors are indexed but listed as unparsed.

**Span convention** (used by selection, holes, assembly and the agent check): 1-based inclusive lines, byte
offsets with an exclusive end; a symbol span starts at the beginning of its first line when only whitespace
precedes it (the replacement carries its own indentation) and includes decorators/annotations/`export`.
Region spans are snapped to whole lines (end before the line's newline).

Route extractors (`slot/index/routes.py`):

| Framework | Resolved |
|---|---|
| Flask | `@x.route(methods=...)` (list or tuple), `@x.get/post/...`, `Blueprint(url_prefix=)`, `register_blueprint(bp, url_prefix=)` across files (registration prefix wins, as in Flask) |
| FastAPI | `@router.get(...)`, `api_route(methods=)`, `APIRouter(prefix=)`, `include_router(module.router, prefix=)` |
| Django | `path()`/`re_path()`/`url()`, `include("app.urls")` prefixes, `View.as_view()` methods, `@api_view([...])` / `require_http_methods`, DRF `router.register` ViewSet actions (heuristic) |
| Express | `app.get(...)`, `router.post(...)`, `router.route("/x").get(...)`, `app.use("/prefix", router)` through `require`/`import`; inline handlers become `route_handler` symbols named `"<METHOD> <path>"` |
| Go | `HandleFunc`/`Handle` (Go 1.22 `"GET /x/{id}"` patterns), gin/chi/echo `GET`/`Get`, gin `Group` prefixes; func literals become inline handlers |
| Spring | class `@RequestMapping` + method `@GetMapping`/.../`@RequestMapping(method=...)` |

Confidence: `exact` (literal and resolved), `pattern` (resolved by name only, or a method from a pattern
string), `heuristic` (guessed: unregistered blueprints, Django views without method decorators, DRF
actions, unresolved handlers). Routes are matched to TaskSpec endpoints by `(method, path)` with parameter
names ignored (`/api/todos/{todo_id}` matches `/api/todos/{id}`); an `ANY` route fans out to every spec
endpoint with that path.

Components (`slot/index/components.py`): React functions/classes with an upper-case name that render JSX (or
extend `Component`); `whole_file` when the file defines exactly that one component named like the file. Vue
and Svelte files are whole-file components.

## Selecting a part

`SlotTargetIn` = the spec's fields plus three optional ones for the picker: `component` (a name),
`content_sha` (what the client believes the slice hashes to) and `anchors` (sha of the 3 lines before/after).

* The address is verified against the parent bytes; when `content_sha` does not match the selection, the part
  is relocated by `symbol_id`, then by the anchors in the same file; otherwise 409 `address_stale`.
* Defaults by kind: file `remove`; region, symbol, endpoint `stub`; component `remove` when whole-file, else
  `stub`; allowlist `[path]`; context policy `ContextPolicy()`; edit policy `revert`. The preview lists which
  fields took defaults.
* Linked endpoints: file = endpoints whose handler is in the file; region = handlers whose span intersects;
  symbol = its endpoints, else one hop through the call graph (handlers that call it or are decorated with
  it, e.g. `login_required` links every protected endpoint); endpoint = the matched spec endpoint; component =
  spec endpoints whose static path appears as a string in the component (with or without the stack's API
  prefix; non-GET also needs the method name). Linked checks come from
  `evaluation.acceptance.checks_for_endpoints` (components: functional checks only); linked probes are the
  task's probes on the linked endpoints. Researcher edits are recorded (`linked_checks_source`, diff).
* `match_key` = `endpoint:<spec id>` or `component:<name>`; `match(target, "same_task")` creates the same part
  in the other parents of the task with the same defaults.
* Overlapping targets of one parent are allowed and warned (`overlapping_targets`).
* Creating the same part twice returns the existing target (same `target_hash`).

### Holes (`slot/hole.py`)

* `stub`: decorators and signature stay; every function body in the span becomes the hole marker plus a
  neutral placeholder (`raise NotImplementedError`, `throw new Error("not implemented");`,
  `panic("not implemented")`, `throw new UnsupportedOperationException("not implemented");`). Docstrings and
  in-span comments are stripped unless `keep_docstring`. A span without a function (a region, a Go struct)
  becomes the hole marker line; a Vue component's template is replaced by the marker.
* `remove`: file and whole-file component targets delete the file; other kinds leave the marker line; a region
  is deleted without a marker.
* `rewrite`: nothing is removed; the original stays visible (a separate treatment).

The holed file is always `original[:start] + hole + original[end:]`, and line endings follow the file (CRLF
files get CRLF).

### Context (`slot/context.py`, `CONTEXT_VERSION = 1`)

`resolve_context(target, policy, hole_mode=None, keep_docstring=None)` is deterministic. Order for
`related_files`: the slot file (holed), files it imports (`imports_depth`), model/schema files, the
routes/app entry file, then other files connected by the import graph (nearest first); `full_app` appends every
other source file; `interface_only` shows related files as outlines (imports, decorators, signatures);
`slot_file_only` shows the slot file only. Tests are hidden unless `include_tests`, and so are READMEs that the
leak scan flags. Files are added in order until `max_context_tokens` (estimated as `ceil(chars / 3.6)`); a
file that does not fit is listed in `truncated_paths` and never cut. The context `sha` commits to the files,
their shas and tokens, the policy hash, the hole mode, `keep_docstring` and `CONTEXT_VERSION`; it enters
`treatment_hash` as `resolved_context_sha` (`SlotPolicy.identity`).

### Leak scan (`slot/leak.py`, `LEAK_VERSION = 1`)

8-token shingles of the original (comments stripped) are searched in the context files and, for restricted
agents, in the agent workspace. The slot file is always scanned with the span removed (also for `rewrite`).
`score` = share of shingles found anywhere; a file holding at least 90% of them as one contiguous run is a
verbatim copy: `slot_leak_verbatim` (error; `validate.target_usable` refuses the target). `score > 0.3` is
`slot_leak_high` (warn). Shared boilerplate (decorators, a common `SELECT ... WHERE id = ?`) gives a partial
score, which is reported and exported next to `equal_to_original`.

## Validation (`slot/validate.py`)

On creation a target queues `VALIDATION_RUNS = 3` reference runs of the original (`reference_of=slot_original`,
the parent's rows and tree hash) and one of the holed tree (`slot_holed`; not for `rewrite`), enqueued for
evaluation on commit at priority `-10`. The candidate selection is recorded as the keys of
`validation.original_pass` / `probe_stability` before the runs start. After each reference evaluation the eval
stage calls `on_reference_evaluated(run)`; when the last one is in:

* a check must pass 3/3 on the original (mixed: flaky) and fail on the holed tree (still passing:
  non-discriminating); everything else goes to `dropped_checks` and `linked_checks` becomes the
  discriminating set;
* a probe must give the same status in all three original runs, else it is dropped from `linked_probes`;
* a holed tree that does not build or start fails every check (that is what the hole did); an original that is
  not measured, or a holed evaluation that errors without a build/start failure, makes the validation
  `error`. Nothing is ever assumed to pass;
* no discriminating check left: `uninformative` and `slot_tests_uninformative` (warn exploratory, error
  confirmatory); a linked endpoint that kept no discriminating check gets the same issue code with
  `data.endpoint`.

`validate_target(target)` aggregates; `validate_target(target, synchronous=True)` runs everything inline
(tests, management use). `revalidate` starts again from the automatic links plus the researcher's edits.

## Executors

**Single shot** (`slot/executors/single_shot.py`, llm lane). Reads the frozen manifest and the rendered
stimuli (`RunManifest.stimuli`), makes exactly one call (transport errors retried per
`retries.upstream_retries` with doubling backoff; parse failures never), records `slot_call_0001` and the
attempt, then: parse `ok` -> assemble and `evaluate`; otherwise `output_invalid` with the parse status as
reason (`truncated` adds the validity note). Parsing strips one fence, then requires exactly one JSON object
matching the output schema (`{"code"}` for span kinds; `{"files"}` with exactly the allowlist for file targets
and removed whole-file components). The call is refused before sending (outcome `excluded:config_invalid`) for
sampling `provider_default` or temperature 0, input over `call.max_input_tokens`, or a worst case over
`run.max_cost_usd` (`budget_exhausted:cost`). Structured output is used when the manifest's
`sampling.forced_params.response_format == "json_schema"` (recorded per condition). Every answered call
(including invalid outputs) is recorded in the spend ledger (`budget.ledger.record_usage`, source
`gateway_final`, or `batch` for batch answers, `dedupe_key=call:{run}:{attempt}:1`), and the attempt is then
settled with the same final usage (`settle_attempt`, adjust 0, reservation released).

**Batch** (`slot/executors/batch_submitter.py`). Off until `BATCH_BACKENDS_VERIFIED` (WP-12 settings block).
Renders the same bodies as single shot for the claimed cells of one (condition, target), submits one batch,
records its id in each run's `execution.batch` (a restarted executor resumes polling instead of resubmitting),
polls every `BATCH_POLL_S` and parses with the single-shot code (`serving_tier=batch`). After `BATCH_EXPIRY_S`
unanswered cells end `infra_error:batch_expired` and are not retried automatically.

**Restricted agent** (`slot/executors/restricted_agent.py`, sandbox lane): `prepare`, `hole_overlay` (the
seeder's hook) and `collect_variant` (the collector's hook) for `agentic.runner`; see the module docstring
for the out-of-slot and outside-span rules, `revert` vs `fail`, and the parse statuses.

## Assembly and evaluation

`assemble(run, replacement)` splices the replacement at the span's byte offsets of the original file (or
replaces whole files), normalises line endings to the original, ensures a trailing newline when the original
span ended with one (and drops one when it did not), writes only `origin=slot` rows and sets `run.tree_hash`
over the full assembled tree. Assembling the original back gives exactly the parent's tree hash.

`evaluate_variant(run)` (called by the eval stage for every run with a slot target) runs the discriminating
checks plus the linked probes (probes from level `functional` up; `evaluation.slot.regression` runs the
whole suite). `overlay` mode is used only for interpreted backends, no new dependency and no frontend file;
otherwise it falls back to `full` and records the reason in `run.execution["slot_eval"]`. With
`caches.reuse_eval_by_tree` an identical tree reuses an evaluation of the same target with the full reuse
key (`reuse.find_reusable` with check selection, probes, eval level and the mode actually used; `reused_from`,
note `eval_reused`); validation runs never reuse. `evaluate_original(target)` runs the same selection on variant 0.
`slot_pass_rate` is `None` (n/a) for uninformative targets and for anything not measured, never 0.

## API (`/api/generation/slots/...`, section 9.6)

Operation ids: `slots_parents_list`, `slots_parents_draw`, `slots_sets_create`, `slots_sets_list`,
`slots_sets_get`, `slots_sets_tree`, `slots_sets_symbols`, `slots_sets_endpoints`, `slots_sets_components`,
`slots_sets_file`, `slots_sets_preview_target`, `slots_sets_create_target` (201, or 200 for an existing
target), `slots_targets_match`, `slots_targets_revalidate` (409 once variants exist), `slots_targets_delete`
(204; 409 `in_use` when a study or variants use it), `slots_targets_get`, `slots_targets_render`,
`slots_targets_variants`, `slots_variants_diff`. Errors are `{"detail", "code", "data"?}`. `SlotSetOut`
carries `parent_levels: {stack_profile, starter_level, auth_mode, prose_variant}` (the levels a slot study
cannot vary: the parent's stack pinned ref, its starter's level and its enum values; `null` when unset).

## Decisions

* Validation selection is stored in the existing `TargetValidation` fields (`original_pass` /
  `probe_stability` keys, filled with empty lists) instead of a new column.
* Reference runs copy the parent's `RunFile` rows (same blobs): `RunView` only overlays parents for slot
  variants, and the build reads the run's own tree.
* `SymbolEntry.language` is `tsx` for `.tsx` files (it needs its own grammar); `.jsx` is `javascript`.
* Token counts in the context use `ceil(chars / 3.6)` so the context sha never depends on an optional
  tokenizer.
* Single-shot pre-call refusals (temperature 0, over the call input cap, over the run cost cap) are
  `excluded:config_invalid`: the treatment cannot run as configured, which the resolver should have caught.
* Agent variants whose slot region cannot be located (text around the hole changed) have parse status
  `schema_violation` and outcome reason `edit_violation` under both edit policies (there is nothing to revert
  to).
