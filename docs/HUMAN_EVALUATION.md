# Human evaluation

A human-evaluation task is a blinded study in which human participants use, test and grade generated apps on a separate evaluation portal.

The platform analyses their judgements with the app as the unit of analysis, under an analysis plan frozen when the task opens. The apps come from completed [generation runs](/docs/GENERATION_PROCESS), usually the current runs (first usable attempt per cell) of a [study](/docs/STUDIES). They run as ordinary runtime containers ([Architecture](/docs/ARCHITECTURE)), and results can be set against the automated conformance suite and the static analysers ([Analysis pipeline](/docs/ANALYSIS_PIPELINE), [Study tables](/docs/STUDY_TABLES)). The code is the `backend/human_eval` app. Researchers use `/api/human-eval/`; participants use `/api/human-eval/portal/` (see [API summary](#api-summary)).

## In the UI

Researcher pages are under **Evaluate → Human eval** in the sidebar.

| Page | What it does |
|---|---|
| `/human-eval` | Your tasks (staff: everyone's), newest first. Columns: task, mode, status, apps, participants, progress (submitted ratings toward the target) and last update. Filters live in the URL (`?status=`, `q`, `page`). **New task** opens the wizard. |
| `/human-eval/new` | The task wizard, in six numbered sections: **1 Start from** (a preset, or **From scratch** with no blocks), **2 Basics** (title, description for the join page, Markdown instructions with a preview, mode), **3 App pool** (a study and/or individual completed runs, with functional status and a running badge), **4 Design** (blocks, consent, pre/post questions, analysis plan), **5 Settings** and **6 Review and create**. The design is validated live (`validate-design/`), and **Create draft** saves a private draft. Unsaved input is kept in `sessionStorage`. |
| `/human-eval/{id}` | One task. The header has the status, the lifecycle actions (Open, Pause, Close, Reopen, Archive, each confirmed), **Preview** (once the task is open), **Duplicate** and **Delete**. Seven tabs, kept in `?tab=` (below). |

| Tab (`?tab=`) | What it shows |
|---|---|
| `overview` (default) | Open blockers, validity warnings, data-collection KPIs, assignments by status, coverage toward the target, environment, the record (seed, design hash, owner), the protocol log and **Reset preview** |
| `design` | The design editor. After the first open it is read-only, and only the settings that stay editable after freezing can change |
| `apps` | The de-blinded pool (label, model, condition, app, functional status, ratings), the hosting state with Start / Stop / Pin / Unpin / Rebuild / Open, an active toggle, **Start all** / **Stop all**, and **Add apps** while the task is a draft. Polls every 5 s while an app is changing state |
| `participants` | Invite links with a copyable full URL, **Generate codes**, **Add accounts**, and the participant table (status, work, last seen, quality flags; row actions: exclude / include with a reason, note, new access code, new set-password link, withdraw, delete) |
| `responses` | Three views in `?view=`: assignments, observations, `http` (the console log). `?assignment=<id>` opens the detail sheet: answers per block, reports with screenshots, finding verdicts and HTTP exchanges |
| `results` | `?group=` (condition, model, app) and `?excluded=1` (include excluded data); warnings, per-block figures and tables, contrasts against the baseline, and the quality table |
| `export` | Every CSV, the ZIP bundle and the results JSON |

Participants use the portal, a separate route group with a minimal shell. It has no sidebar, only **Sign out** for accounts and **Leave** for cookie sessions. Participants cannot open the in-app docs; the portal serves the task's own instructions.

| Portal route | What it does |
|---|---|
| `/eval` | Home. It lists this browser's participations (the cookie session plus the signed-in user's account and preview tasks) with progress and Continue, an **Enter an access code** form (resume), and **Sign in with an account** (`/auth/login?next=/eval`). Restricted evaluator accounts that open any researcher page are redirected here. |
| `/eval/join/{token}` | Invite landing page: the task title and description, the consent text with a checkbox, and **Join**. Joining then shows the participant's access code once, to copy and keep. A browser that already joined the task sees "You have already joined". |
| `/eval/t/{taskId}` | Task page. It shows the instructions, consent, the "About you" pre-questionnaire (editable until the first app is handed out), progress, and **Evaluate apps** (start the next app or continue the current one). **Finish** asks the post-questionnaire, then shows the completion code. Participants can also withdraw here, optionally deleting their data. |
| `/eval/t/{taskId}/a/{assignmentId}` | The workbench. Left: the app viewer, with the hosting state, start and queue messages, and the app in a sandboxed frame (reload, new tab, width toggles). Pairs get A/B tabs or a side-by-side view. A spec panel, the source viewer and the HTTP console appear when enabled. Right: one card per block, each autosaved 800 ms after a change, plus Skip (with a reason) and Submit, which lists the server's gaps per block. A heartbeat goes out every 30 s, and the hosting state is polled every 3 s until the app runs. The workbench is read-only once the assignment is submitted, skipped or expired, or when the task stops accepting work. |

The app frame uses `sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads"` (no top navigation) and `referrerpolicy="no-referrer"`. Only https apps on an origin other than the portal's are framed; anything else opens in a new tab (`rel="noopener noreferrer"`). Untrusted strings, such as reports, locations and console output, are rendered as text, and only http(s) values become links. Markdown (instructions, consent, scenario descriptions) goes through the docs viewer's sanitizing renderer.

## Concepts

### Data model

`backend/human_eval/models.py`:

| Model | Meaning |
|---|---|
| `EvalTask` | The study. Holds the title, description (join page), Markdown instructions, `mode` (`single` or `pairwise`), `status`, `design` and `settings` (JSON validated by `services/design.py`), `seed`, an optional source `study` and the `opens_at` / `closes_at` window. Freezing adds `design_hash`, `frozen_at`, `opened_at`, `closed_at`, `first_closed_at` (set once, never overwritten) and `protocol_log`. |
| `EvalTaskApp` | One generated app (a `GenerationRun`, FK attribute `job`) in the pool. It holds the blinded `label` (`App 07`), `group_key`, `position`, `is_active`, the `snapshot` (the de-blinding key), the hosting state (`container`, `started_by_task`, `pinned`, `last_activity_at`, the queue and in-flight markers, `hosting_error`) and the frozen finding sample (`finding_sample`, `finding_frame`). |
| `EvalItem` | The unit a participant evaluates: one app (single mode), or an unordered pair `app_a` / `app_b` ordered by app id (pairwise). Items are built once, at first open. |
| `EvalInvite` | A share link `/eval/join/<token>` with `label`, `max_uses`, `uses`, `expires_at` and `is_active`. |
| `Participant` | One rater in one task, labelled `P001`. `kind` is `invite`, `code`, `account` or `preview`. `status` goes `invited → active → completed`, or ends `withdrawn`. Secrets are stored only as sha256 hashes (access code, session token, device cookie). Also holds the consent time, pre/post answers, completion code, a manual exclusion and a researcher note. |
| `Assignment` | Participant × item. Status goes `assigned → in_progress`, then `submitted`, `skipped` or `expired`. Also holds `position`, `swapped` (pairwise sides), the frozen `context`, timings (`active_seconds`, `wall_seconds`, `app_views`), the skip reason and note, and a manual exclusion. |
| `Response` | The answers to one design block within one assignment: an autosaved draft, validated in full at submit. |
| `Observation` / `Attachment` | A bug, vulnerability or note against one app slot, with the tester's own severity and up to five screenshots. |
| `FindingVerdict` | A participant's TP / FP / unsure on one sampled automated finding, with a snapshot of the finding as it was shown. |
| `HttpExchange` | One request sent through the HTTP console, with its response excerpt. |
| `EvaluatorProfile` | Marks a user as an evaluator account. A restricted one can only reach the portal. |

### Lifecycle and freezing

| Status | Actions (`TaskDetail.allowed_actions`) |
|---|---|
| `draft` | `open` (only without open blockers) |
| `open` | `pause`, `close` |
| `paused` | `open`, `close` |
| `closed` | `reopen` (→ open), `archive` |
| `archived` | `reopen` (→ closed) |

`open_blockers`, which apply to drafts only, cover:

- an invalid design or invalid settings;
- no block, or a pairwise task without a pairwise block;
- no access method enabled;
- no active app, or active apps whose generation run was deleted;
- a pairwise pool in which no pair can be formed, or vs-baseline pairing with no baseline condition.

The first open runs in one transaction, so a failure leaves the draft untouched. It:

1. re-takes every app's snapshot;
2. relabels the apps with a seeded shuffle (`App 01` … `App NN`, zero-padded to `max(2, digits of N)`);
3. builds the items;
4. draws the finding samples and scans the sampled findings for model names;
5. sets `frozen_at`, `opened_at` and `design_hash` (sha256 of the canonical JSON of the mode, the design and the settings minus the keys that stay editable).

After freezing, changes to the mode, design, study or a frozen settings key answer 409 `design_frozen`. Title, description, instructions and the window stay editable. A change to the title, description or instructions appends `{at, by, field, old_sha256, new_sha256}` to `protocol_log`. Every assignment records the sha256 of the instructions it was opened with.

- **Close** sets `closed_at` (and `first_closed_at` the first time), unpins every app and stops the apps the task started.
- **Pause** stops no apps; the idle reaper does that.
- **Delete** works in any status and stops the task's apps first.
- **Duplicate** creates a draft "Copy of …" with the same design, settings and apps (fresh snapshots, new labels and a new seed), without participants or invites.
- **Removing an app** (`DELETE …/apps/{id}/`) is a draft-only action; after the first open, deactivate the app instead. Deactivating also deactivates its items, and open assignments on them may finish.

A task *accepts* work while it is open and inside its `opens_at` / `closes_at` window. A researcher preview also works while the task is paused and ignores the window. Assignment mutations on a task that does not accept work answer 409 `read_only`.

### Snapshots and blinding

`EvalTaskApp.snapshot` is the de-blinding key. It is taken when the app is added and taken again, in full, at first open. Analysis and exports read only the snapshot, never live run data. Everything in it is read through `generation.runs.view` (`RunView`).

| Key | Content |
|---|---|
| `model_id`, `model_name`, `provider` | The model |
| `condition_id`, `condition_label`, `study_id`, `study_name`, `task_ref`, `prose_variant`, `stack`, `auth_mode`, `unit_key`, `repeat_index`, `kind`, `harness` | The study cell and run kind |
| `run_id`, `app_slug`, `app_name`, `spec_content_hash`, `run_fingerprint` | The task (slug and title) and the run's `task_hash` (task version and prose variant), so apps built from different task versions or prose variants are never paired; the run's identity fingerprint |
| `functional` | `{status, pass_rate, deployed, source}` from the latest `RunEvaluation` (`source` is `eval_suite`, or null when the run was never evaluated; an unmeasured evaluation has `pass_rate: null`, never 0), plus `by_category` at first open |
| `evaluator_version`, `tester_version` | The evaluation suite (`slug@version`) and the tester version that graded the run; compare pass rates only within one suite version |
| `checks` (first open) | The suite's endpoint checks: `name` (`METHOD /path`), `category`, `passed` |
| `static_findings` (first open) | `{count, loc, loc_model, tools, by_tool}`: non-dependency findings from static, non-AI tools in the latest measured `ToolResult` per (run, tool) (a not-applicable tool is not measured, never a zero); `loc` is the app-scope source LOC (`RunView.loc("app")`), `loc_model` the model's own files; either is null without a collected tree; not deduplicated |
| `blinding_scan` | Model and provider tokens found in the generated source and in the sampled findings |

`group_key` is `f"{app_slug}:{spec_content_hash[:12]}"`. Pairs are formed only within one group key, so only apps built from the same task version and prose variant are compared. An app without a task gets its own app id as group key and is never paired.

Blinding is always on and cannot be turned off. Participants never receive model names or ids, condition labels, run, study or task-app ids, container ids or names (apart from the host in the app URL), or the expected answers of attention checks. Three settings decide what else they see:

- `blinding.show_spec` (default on): the app's specification in a side panel.
- `blinding.show_source_code` (default off): a file browser over the generated code, capped at 512 KB per file. Finding paths are mapped to those names.
- `blinding.show_admin_credentials` (default on): the admin login the platform's test deployment provides.

Two open warnings guard blinding:

- `model_name_in_source`: a model, model id or provider token appears in shown source code or in sampled findings.
- `layout_reveals_condition`: source is shown while the pool mixes generation modes or harnesses, so the file layout itself gives the condition away.

### Items and pairing

In single mode every active app is one item. In pairwise mode `settings.pairing` decides the pairs within each group key:

| Strategy | Pairs |
|---|---|
| `all_pairs` (default) | Every unordered pair. With `cross_condition_only` (default on), pairs whose apps share a condition are dropped; without a condition, apps sharing a model are dropped. |
| `random_k` | Seeded: shuffle the group and pair neighbours, repeatedly, until every app is in at least `k` pairs (1..20) or its eligible pairs are used up. Never a duplicate pair. |
| `vs_baseline` | Each non-baseline app against the baseline app(s) of its group, preferring the same `repeat_index`. The baseline is `pairing.baseline_condition_id`, else the study's baseline condition. |

Item order is a seeded shuffle.

### Allocation

`POST portal/tasks/{id}/next/` (`services/allocation.py`) runs under a row lock on the task:

1. Expire stale open assignments: no activity for `allocation.assignment_timeout_minutes` (default 60). Nothing expires under strategy `all`, and expired work is kept.
2. Return the participant's open assignment, if any (`resumed`).
3. Check the gates: the task accepts work (`not_accepting`), the participant is not finished (`done`), consent was given (`consent_required`), the required pre-questions are answered (`pre_questions_required`), and `max_items_per_participant` is not reached, counting submitted plus skipped (`limit_reached`).
4. Collect the candidates: active items the participant has no assignment for, plus items of their own expired assignments. Under `exclusive` isolation, items with an app in another participant's open assignment are dropped. Under `balanced`, items must have a load below `ratings_per_item`. When none is left, every item stays a candidate if `allow_overflow` is on (and always for previews).
5. Re-open the participant's own expired assignment first, keeping its context, answers and timings.
6. Otherwise create a new assignment:
   - **`balanced`** (default): the lowest load, then the group (condition) the participant has rated least, then a seeded hash per participant and item.
   - **`all`**: every participant rates every item, in a per-participant seeded order interleaved round-robin across groups.
7. With no candidate left, the reason is `done` (every active item was submitted or skipped by this participant) or `no_items_available`.

*Load* counts submitted plus open assignments that are not excluded, from participants who are neither previews nor excluded. Pairwise sides are balanced per item: `swapped` = (number of earlier non-preview assignments of the item is odd) XOR a seeded per-item coin, and `swapped` means `app_b` is shown as A.

When an assignment is first opened (workbench GET or first heartbeat) it becomes `in_progress`, and its `context` is frozen and never changes afterwards:

- per app slot, the requirement items, with stable ids so the same spec yields the same ids for every app;
- the findings to review;
- the fixed questions of standard questionnaires;
- the instructions hash;
- later, the hosting provenance.

### Human evaluation and the review sample

The [manual review sample](/docs/STUDY_TABLES#manual-review-sample) (`export_review_sample` / `score_review_sample`) is the offline counterpart. A seeded, stratified sample of runs is exported as a ZIP of code, requirement lists and blank label sheets; one or a few reviewers fill them in, and the scorer reports precision (Wilson), Cohen's κ and Spearman ρ against the automated verdicts.

This module covers the same questions online, with many raters working on the *running* apps:

- the finding-review part of a security block corresponds to `static_issues.csv` / `ai_findings.csv`;
- the requirements block corresponds to `requirements.csv`;
- rating criteria correspond to `apps.csv`;
- compliance against the conformance pass rate corresponds to the sample's `compliance` score.

On top of that, it allocates and blinds the work, records timing, app provenance and console traffic, and reports inter-rater agreement and CIs with the app as the unit under a frozen analysis plan.

The two do not share data. The review sample groups precision by the normalized severity; this module reports it only by the tool's own label (`tool_severity`). Use either one, or both as independent checks.

## Designing a task

A design (`task.design`, TS `TaskDesign`) has `version: 1`, ordered `blocks`, the `analysis` plan, `consent`, `pre_questions` and `post_questions`. The wizard's **From scratch** starts with no blocks and the default settings. `POST tasks/` without a preset or a design uses the default design (also served as `environment/`'s `default_design` for single mode). It has consent on (a neutral template), three pre-questions on experience (years of programming, web development, security testing) and one block:

- single mode: a rating block on functionality, usability and overall quality, 1..7;
- pairwise mode: a preference block with an overall preference.

The primary metric is pre-registered: `composite` (single) or `overall` (pairwise).

### Blocks

Single mode allows rating, requirements, security, scenarios, questionnaire and bugs blocks; pairwise mode allows pairwise and bugs. Opening a pairwise task needs at least one pairwise block.

| Type | What participants do | Answers (`Response.answers`) | Primary metric |
|---|---|---|---|
| `rating` | Rate criteria on integer scales (`min`, `max`, `step`, anchors, optional per-value labels). `allow_na` adds "can't judge". `comment` is off, optional or required. | `{criteria: {id: int or "na"}, comment}` | `composite` or a criterion id |
| `requirements` | Judge each requirement as met, partial, not met, blocked (an app defect prevents the test) or can't test. Items come from the spec `sources` (`backend`, `frontend`, `admin` prose; `api`, `admin_api` endpoints as `METHOD /path`) plus `custom` items shared by every app. `note` is off, optional or `required_unless_met`. | `{items: {req_id: {verdict, note}}}` | `compliance` |
| `security` | Up to four activities, at least one enabled: a guided `checklist` (vulnerable / not vulnerable / not tested, with help on how to test; defaults to ten OWASP Top 10 2021 items), vulnerability `reports`, `finding_review` (TP / FP / unsure on sampled automated findings) and the `http_console`. | `{checklist: {item_id: {outcome, note}}}`, plus observations and verdicts | `vulnerable_share` (needs a checklist) |
| `scenarios` | Usability tasks with a description and success criteria. `timed` adds start/stop timers; `ease_question` asks the Single Ease Question (1..7) unless the scenario was not attempted. Outcomes: success, partial, failure, not attempted. | `{scenarios: {id: {outcome, seconds, ease, note}}}` | `success_rate` |
| `questionnaire` | A standard `instrument` with fixed server questions (`sus`: SUS, ten items 1..5; `umux_lite`: two items 1..7; `nasa_tlx`: raw TLX, six subscales 0..100 in steps of 5), or `custom` questions. | `{answers: {question_id: value}}` | `score` (standard instruments) |
| `bugs` | Log each bug as a report. `min_reports` (0..20) is enforced per app slot; with 0 the participant may instead confirm "none found" for that app. At most one bugs block per design. | `{none_found: {slot: bool}}`, plus observations | `bugs_per_rater` |
| `pairwise` | Compare the two apps per criterion on `ab_tie` (A / tie / B) or `five_point` (−2 = A much better … 2 = B much better), plus an optional `overall` preference and a `rationale` (off, optional, required). Values are stored as displayed. | `{criteria: {id: value}, overall, rationale}` | `overall` or a criterion id |

Finding review (`finding_review`) settings:

- `max_findings`: 1..50, default 10.
- `categories`: `Finding.category` values to sample from; empty means all.
- `include_ai`, `include_dependency`, `include_dynamic`: all off by default.

Only one security block may review findings. The sample is drawn once per app at first open, so every rater of an app reviews the same findings. It is drawn from the latest completed result per tool:

- `max_findings` seats are allocated across tools in proportion to their populations (largest remainder, at least one per tool);
- findings are drawn uniformly within each tool, seeded;
- `finding_frame = {tool: {population, sampled}}` records the frame for weighting.

Findings are shown with the tool's own label (`tool_severity`, "unrated" when blank), never the normalized severity.

Reports (`Observation`) carry:

- a slot that cannot change later;
- a title, description, steps and a location (a relative path or an http(s) URL);
- a category from the bug or OWASP list;
- the tester's own severity (critical … info, labelled "Your severity");
- a CWE and the checklist item;
- up to five screenshots (PNG, JPEG, GIF or WEBP, detected from the file content).

The limits are 100 observations per assignment and 50 MiB of attachments per participant (409 `quota_exceeded`).

### Questionnaires, consent and attention checks

Question kinds are `text`, `textarea`, `single`, `multi`, `scale`, `number` and `attention`:

- `single` and `attention` need at least two options; `multi` needs at least one.
- An attention question needs an `expected` option. It is never sent to the portal, and a wrong answer flags `attention_failed`.

`pre_questions` are asked once before the first item (the "About you" form) and lock when the first item is handed out. `post_questions` are required before **Finish**; their ids may not reuse a pre-question id.

`consent.enabled` with a Markdown `text` requires agreement before joining (invite links) and before the first item (all kinds). The default template states voluntariness, what is recorded, pseudonymity and withdrawal.

Instrument scoring (`services/instruments.py`) uses no imputation: a response with any item missing has no score.

- SUS: (Σ odd (x − 1) + Σ even (5 − x)) × 2.5
- UMUX-Lite: ((a − 1) + (b − 1)) / 12 × 100
- raw NASA-TLX: the mean of the six subscales

### Analysis plan

`design.analysis` is pre-registered and frozen with the design:

| Field | Meaning |
|---|---|
| `primary` | `{block_id, metric}` or null; the metric must be valid for the block (see the table above). Without it nothing is confirmatory. |
| `planned_ratings_per_item` | Balanced allocation: the planned sample (null means the frozen `allocation.ratings_per_item`). |
| `planned_participants` | Strategy `all`: the planned number of participants (null means the sample is unplanned). |
| `exclusion_rules` | `speeding`, `attention_failed`, `straightlining`, `app_not_viewed`. Applied by the analysis, reported in `rule_exclusions`, never written to rows. |
| `unevaluable_policy` | `exclude` (complete case, the default) or `floor`: apps with no included submission and at least one `app_broken` skip count at the floor (rating scale minimum, composite 0, compliance 0). |

### Settings

`task.settings` (TS `TaskSettings`) is deep-merged over the defaults. "After open" says whether a key may still change once the design is frozen.

| Key | Default | After open |
|---|---|---|
| `blinding.show_spec` / `show_source_code` / `show_admin_credentials` | on / off / on | frozen |
| `allocation.strategy` | `balanced` (or `all`) | frozen |
| `allocation.ratings_per_item` | 3 (1..50) | frozen |
| `allocation.allow_overflow` | off | editable |
| `allocation.max_items_per_participant` | null (unlimited; 1..10 000) | editable |
| `allocation.assignment_timeout_minutes` | 60 (5..1440) | editable |
| `allocation.allow_skip` | on | editable |
| `pairing.strategy`, `k`, `cross_condition_only`, `baseline_condition_id` | `all_pairs`, 2, on, null | frozen |
| `access.allow_invite_links` / `allow_access_codes` / `allow_accounts` | on | editable |
| `hosting.on_demand` | on | editable |
| `hosting.idle_stop_minutes` | 20 (5..240) | editable |
| `hosting.max_running_apps` | 3 (1..20; also capped server-wide) | editable |
| `hosting.isolation` | `shared` (or `exclusive`) | frozen |
| `hosting.reset` | `none` (or `per_assignment`, which requires `exclusive`) | frozen |
| `quality.min_seconds_per_item` | 60 (0..7200, 0 = off) | editable, unless `exclusion_rules.speeding` is on |

### Presets

`GET presets/` returns ready-made designs; the wizard's first section lists them. Every preset enables consent, asks the default pre-questions, adds an optional final-comments post-question and pre-registers a primary metric. Rating criteria use 1..7 scales anchored at 1 / 4 / 7, with "can't judge".

In the table, "Ratings" means planned / allocated ratings per item (`planned_ratings_per_item` / `allocation.ratings_per_item`), and "Speeding" is the `quality.min_seconds_per_item` threshold.

| Slug | Mode | Blocks | Primary | Ratings | Speeding | Other settings |
|---|---|---|---|---|---|---|
| `app-grading` | single | ratings on six criteria, bug log | ratings / composite | 3 / 3 | 180 s | |
| `requirements-audit` | single | requirements (all sources, note unless met), bug log | requirements / compliance | 2 / 2 | 300 s | |
| `security-assessment` | single | security: checklist, reports, finding review of `security` and `secrets` findings, HTTP console | security / vulnerable_share | 2 / 2 | 600 s | `exclusive` + `per_assignment`, 120 min timeout |
| `usability-test` | single | three timed scenarios with SEQ, SUS | sus / score | 3 / 3 | 240 s | spec hidden |
| `ab-comparison` | pairwise | preference on four criteria plus overall (A / tie / B) | preference / overall | 3 / 3 | 240 s | `all_pairs`, cross-condition only |
| `full-evaluation` | single | requirements, ratings, security with finding review, bug log, SUS; an attention check in the post-questionnaire | ratings / composite | 2 / 2 | 900 s | excludes `attention_failed`, `exclusive` + `per_assignment`, 180 min timeout |

### Validation limits

`services/design.py` checks both documents, and the error paths are prefixed `design.` / `settings.`:

- Ids match `^[a-z0-9_]{1,40}$` and are unique within their list.
- A design has at most 30 blocks, and each list at most 50 entries.
- Labels are 1..300 characters; instructions, descriptions and consent text at most 20 000.
- Scale spans are 1..100, the step divides max − min, and label keys must be scale values.
- A standard questionnaire must leave `questions` empty; a custom one needs at least one question.
- A pairwise block needs a criterion or the overall preference.

Saving needs a valid design. "No blocks yet" is an open blocker, not a validation error.

## Participants and access

Each access method can be switched on or off in `settings.access`. Creating a link or a code while its method is off answers 409 `access_disabled`. Participants are numbered max + 1 under a lock on the task row.

### Invite links

`POST tasks/{id}/invites/` creates a share link `/eval/join/<token>` with an optional label, `max_uses` and `expires_at`. The token is stored in plain text, since it is a share link. `GET portal/join/{token}/` reports the link's `availability`, checked in this order: `inactive` (the link, or invite access, is off), `expired`, `full`, `not_open` (draft, paused, or before `opens_at`), `closed` (closed, archived, or after `closes_at`), else `open`.

Joining creates an `invite` participant (`active`) and returns the participant's access code once. That code is also the resume code, for another browser or after the session ends. A browser that already holds a session for the task gets `already_joined`: the GET reports it and the POST answers 409.

### Access codes

`POST tasks/{id}/participants/codes/` (`count` 1..500) creates `code` participants (`invited`) and returns the plaintext codes once, for example `K7QM-4XT9-BW2R`:

- 12 characters from a 30-letter alphabet without look-alikes, about 59 bits;
- stored as the sha256 of the normalized code (upper case, separators removed) plus a 4-character hint.

A participant enters the code at `/eval` (`POST portal/resume/`), which rotates the session token. A code participant becomes `active` the first time they resume. `regenerate-code/` (code and invite participants) replaces the code and ends any cookie session. Resuming on a closed task still works (read-only), so participants can see their completion code. Every failure answers the same 404 "Code not recognised".

### Evaluator accounts

`POST tasks/{id}/participants/accounts/` takes up to 200 emails per call, with `restricted` (default true) and `send_email` (default false). Each email gets its own result:

- **Existing user**: linked as an `account` participant and left untouched (no profile, no token changes). A user already in the task gets an error for that email.
- **Missing user, `restricted: true`**: a new **restricted evaluator** is created with:
  - an unusable password;
  - a verified primary allauth email;
  - `EvaluatorProfile(is_restricted=True, created_by=you)`;
  - a set-password link, `setup_path` = `/auth/password/reset/<key>`, built with allauth's reset-token generator.

  If the users app would make the new user staff or superuser (it promotes the first account on an instance, for example), that email is rolled back with "cannot create this account".
- **Missing user, `restricted: false`**: an error for that email. `false` only means "link existing users".

Per researcher and UTC day, accounts are capped at 200 new users and 200 emails (409 `quota_exceeded`, checked before anything is written). With `send_email`, a plain invitation goes out after commit, in a thread; failures are logged, never raised. Its links use `FRONTEND_PUBLIC_ORIGIN` (else `https://DJANGO_DOMAIN`), never the request's Host.

`POST …/participants/{pid}/setup-link/` issues a fresh link. It works only for accounts you created that have never signed in; anything else answers 409 `forbidden`. The link expires after `PASSWORD_RESET_TIMEOUT` (Django's default is 3 days) or at the first sign-in.

Restricted evaluators sign in at `/auth/login` and can reach only the portal. `EvaluatorScopeMiddleware` is default-deny and allows exactly:

- `/_allauth/browser/v1/` (prefix);
- `/api/human-eval/portal/` (prefix);
- `GET` / `PATCH /api/users/me/`.

Everything else answers 403 `{"detail": "Evaluator accounts can only use the evaluation portal.", "code": "evaluator_only"}`, including the admin, `/_allauth/app/v1/` and `/api/users/me/activity/`. Bearer tokens of restricted users are refused everywhere, and their tokens are revoked whenever the profile is saved. Staff are never restricted.

An account participant becomes `active` on their first visit to the task page.

### Preview

`POST tasks/{id}/preview/` creates the researcher's own preview participant (`kind: preview`) and returns the portal path. A draft answers 409 `not_frozen`; open the task first. Previews:

- work while the task is open or paused, ignoring the window;
- never add allocation load or hold apps under exclusive isolation, and always may overflow;
- never count in progress, results or exports.

The Responses browser does list them, so a pilot run can be checked. `DELETE preview/` (the Overview tab's reset preview) deletes the preview participant and its data.

### Sessions, cookies and throttling

| Cookie | Name (https origin / otherwise) | Attributes |
|---|---|---|
| Session | `__Host-llml_eval` / `llml_eval` | `Path=/`, `HttpOnly`, `SameSite=Lax`, `Secure` iff `FRONTEND_PUBLIC_ORIGIN` is https (not tied to `DEBUG`), no `Domain`, no `Max-Age` (browser session) |
| Device | `__Host-llml_eval_device` / `llml_eval_device` | The same, plus `Max-Age` of 1 year |

- **Sessions.** The session cookie carries a random token; the database stores only its sha256. The session ends after `HUMAN_EVAL_SESSION_IDLE_HOURS` (24) without activity, and on logout, withdrawal, code regeneration or deletion.
- **Duplicate joins.** The device cookie is set on join; its sha256 (`device_hash`) flags `possible_duplicate` when two participants of a task share it.
- **CSRF.** Join, resume and logout enforce Django's CSRF check (403 without `X-CSRFToken`), so a cross-site form cannot plant a participant session. Every other unsafe portal request is CSRF-checked by `PortalAuth`.
- **Throttling.** Only *failed* invite-token and access-code lookups count, never successful ones. The limit is 60 failures per 10 minutes per client address; when `TRUSTED_PROXY_CIDRS` is empty there is also a global ceiling of 600 failures per hour. A blocked client gets 429 `rate_limited` before any lookup, so even a correct code waits out the window.
- **Client address.** The address is the right-most `X-Forwarded-For` hop outside `TRUSTED_PROXY_CIDRS` (`inference.relay.views.client_ip`). Behind the frontend or Caddy with `TRUSTED_PROXY_CIDRS` empty, every participant shares the proxy's address. The environment then reports the warning `shared_client_ip`, and one participant's typos can block everyone, so set `TRUSTED_PROXY_CIDRS` to the proxy addresses.
- **Counter storage.** Throttle and console counters live in the Django cache: Redis in production, per-process memory in the local stack.

### Withdrawal, exclusion and deletion

| Action | Effect |
|---|---|
| Withdraw (participant, `withdraw/`) | Status `withdrawn`, open assignments expire, the session ends. With `delete_data` the participant's assignments (responses, observations and screenshot files, verdicts, HTTP exchanges) and pre/post answers are deleted too. |
| Withdraw (researcher, `PATCH … {status: "withdrawn"}`) | The same, without deleting data. |
| Exclude / include (researcher, participant or assignment) | `excluded` with a reason, `excluded_at` and `excluded_by`. Excluded rows stay in exports and are dropped from results unless `include_excluded`. An exclusion made after the first open is post hoc (see [Confirmatory results](#confirmatory-results)). |
| Delete participant (researcher) | Deletes the participant and all their data. |

Attachment files are deleted after the deleting transaction commits, cascades included.

## Running apps for testers

Task apps are ordinary runtime containers (`llm-<hex>`) built from the run's collected tree by `runtime.container_service`. `services/hosting.py` decides when to build, start, stop or reset them. All Docker work is dispatched after the deciding transaction commits, in a daemon thread of that process: the `django` web process for requests, the Celery worker for the periodic reaper. A `django` restart or autoreload can therefore strand a build; the in-flight marker expires after 10 minutes and **Rebuild** recovers a stuck app ([Background services](/docs/BACKGROUND_SERVICES)).

> [!WARNING]
> Participants can open apps only with **subdomain routing**: `APPS_SUBDOMAIN_PROXY=True` (a wildcard `*.<APPS_DOMAIN>` edge route) or the Traefik bridge (`TRAEFIK_DYNAMIC_DIR`). Path mode (`APPS_PROXY_PATH`) is owner-only and breaks SPAs, and direct ports are not public. Otherwise every participant sees the state `unavailable`, the environment reports `apps_not_reachable` and the Overview tab shows the warning. Researchers still see the real container state. See [Deployment guide](/docs/deployment-guide).

| Topic | Behaviour |
|---|---|
| Pre-start | **Start all** (`POST tasks/{id}/hosting/ {action: "start_all"}`) pins and starts every active app in position order; apps beyond capacity queue and start as slots free up. A pinned app is never idle-stopped or evicted. **Stop all** unpins and stops what the task started. Per app: start, stop (forced, even a container the task did not start), pin, unpin, rebuild (remove the container and build a new one). |
| On demand | With `hosting.on_demand` (default on), the workbench's **ensure** call starts a stopped app or builds a missing one. When it is off, unpinned apps show "This app has not been started yet — ask the researcher". A failed app is not retried on every participant click; the researcher restarts or rebuilds it. Jobs without code are never built (`no_code`). |
| Capacity | A slot is a distinct container that is running, building or starting, or an in-flight start, among task-started apps. The limits are `HUMAN_EVAL_MAX_RUNNING_APPS` (server-wide, default 4), `hosting.max_running_apps` (per task, default 3) and, for builds, `HUMAN_EVAL_MAX_CONCURRENT_BUILDS` (default 1). When full, the least recently active task-started, unpinned, unshared app idle for 3 minutes or more is evicted. Otherwise the app joins a FIFO queue, and the participant sees "Waiting for a free app slot (n of N in use) — number k in the queue". Unpinned queue requests nobody polled for 10 minutes are dropped. |
| In-flight marker | `start_dispatched_at` is set under a row lock when a build, start or reset is dispatched; nothing is dispatched again while it is under 10 minutes old. The reaper clears stale markers. |
| Idle stop | Task-started, unpinned running apps whose `last_activity_at` is older than `hosting.idle_stop_minutes` (default 20) are stopped, and so are the started apps of closed and archived tasks. The reaper runs from Celery beat (`human_eval: reap idle apps`, every 120 s) and lazily, at most once a minute, from ensure, heartbeat and the hosting panel; the lazy run covers every task, but without beat idle apps are stopped only while some task is in use. Heartbeats from a viewed app keep it alive. Containers the task adopted (already running, `started_by_task` false) are never stopped by the task. |
| Adopted containers | If the run already has a running container, for example one started from `/runtime`, the task uses it without counting it as task-started. |
| Shared containers | One run's container can serve several tasks. The reaper, eviction, stop-all, close and non-forced stops leave a container alone while another open or paused task's app on it is pinned or active within its idle window. Rebuild and reset answer 409 `shared_container`. Activity on one task's app refreshes every app on the same container. |
| Heartbeats | The workbench beats every 30 s while open. A beat is honoured when the tab is visible, or while hidden for at most `assignment_timeout_minutes` since the last visible beat, so an abandoned tab lets the assignment expire. Honoured beats set `last_activity_at` and add the gap since the last beat to `wall_seconds` (at most 60 s; gaps over 5 minutes add nothing). Visible beats add up to 60 s to `active_seconds`. Viewed slots add to `app_views[slot].viewed_seconds` and set `first_running_at` once their app runs. |
| Provenance | The first time a slot's app is seen running for an assignment, `context.hosting[slot]` records `container_id`, `container_name`, `image`, `reset_at`, `prior_users` (other assignments that used this container state since its last reset or build) and `first_running_at`. Skips store each slot's hosting state in `context.skip_hosting`. |

### Isolation and reset

With `hosting.isolation = "shared"` (default), several participants can use one app at once. `exclusive` hands an app to at most one open assignment at a time; it slows collection and needs as many running apps as there are concurrent participants.

`hosting.reset = "per_assignment"` requires `exclusive`. Before a new assignment uses an app whose container state an earlier assignment was handed, the container is recreated from its **existing image**: same name, port and network, no rebuild, so the URL stays valid. Until then the participant sees `idle` ("A fresh copy of this app will be started for you"), and their ensure call performs the reset. A reset never runs while another open assignment uses the container.

Isolation and reset are frozen at open. Presets with a security block use `exclusive` + `per_assignment`.

> [!WARNING]
> Without a reset, apps keep state between raters: accounts, records, uploaded files, and payloads such as stored XSS left by earlier testers. Later raters may judge an app partly by what earlier raters did. The task warns `shared_app_state` before opening (a security block without per-assignment reset), and results warn when apps are not reset and any app has more than one submitted assignment. The provenance columns in `assignments.csv` (`prior_users`, `reset_at`) let you check or control for it.

The admin login shown to participants (`admin` / `admin`, from `generation.services.evaluation.contract`) comes from the platform's test deployment, not from the generated app. The default security checklist help, the security preset instructions and the codebook say it is out of scope, so it must not be reported as a weak or default password. Hide it with `blinding.show_admin_credentials`.

### HTTP console

With `http_console` on a security block, participants can send requests to their app from the workbench (`POST portal/assignments/{id}/console/`):

- **Target.** The URL is always the running `llm-*` container's internal upstream plus the participant's path. The path must start with `/`, is at most 2048 characters, and may not contain whitespace, control characters or `#`.
- **Request limits.** Methods are `GET POST PUT PATCH DELETE HEAD OPTIONS`. At most 30 headers; host, hop-by-hop and length headers are dropped. The body is at most 256 KiB.
- **Sending.** No proxy environment is used and redirects are not followed. The response is read up to 512 KiB within a 25 s wall clock.
- **Rate limits.** 30 requests per minute and 2 concurrent requests per participant (429 `rate_limited`), and at most 1000 logged exchanges per assignment (409 `quota_exceeded`).
- **Logging.** Every request is logged as an `HttpExchange` with request and response excerpts of up to 16 KiB, errors included.

## Results and statistics

`GET tasks/{id}/results/?group_by=condition|model|app&include_excluded=false` returns TS `TaskResults`, computed by `services/analysis.py` on every request from frozen data only: snapshots, finding frames, assignment contexts and stored answers.

- **Included data.** Submitted assignments of non-preview participants. Unless `include_excluded` is set, manually excluded assignments and participants and those removed by a pre-registered exclusion rule are dropped. `counts` and `rule_exclusions` describe the plan's exclusions in both views.
- **Groups.**
  - `condition`: `snapshot.condition_id`, labelled with the condition label; apps without a condition fall back to their model.
  - `model`: `snapshot.model_id`.
  - `app`: every app on its own.
- **Unit of analysis: the app.** An app's value is the mean over its raters, or its share of rater judgements, and a group's value is the mean of its apps' values. CIs are seeded percentile bootstraps over apps (2000 draws, from the task seed) and are null below three apps. Rater-level counts are descriptive only; Wilson intervals are not used for pooled rater judgements.
- **Unevaluable apps.** An app with no included submission and at least one `app_broken` skip is unevaluable. `ResultGroup` reports `n_apps_pool`, `n_apps_rated` and `n_apps_unevaluable`. `GroupScore.floor` repeats the statistic with unevaluable apps at the floor; under `unevaluable_policy: floor` the primary mean uses it. Only ratings (the scale minimum), the composite (0) and compliance (0) have a floor; questionnaire, scenario, bug and security metrics do not.

| Block | Per app | Per group | Agreement (Krippendorff's α) |
|---|---|---|---|
| rating | Mean, sd, median and `na` count per criterion. `composite` = mean of the criteria rescaled to 0..1 (`na` ignored) | Mean of app means, bootstrap CI, floor | Per criterion over apps; ordinal up to 10 scale points, else interval |
| requirements | `compliance` (met 1, partial 0.5, not met and blocked 0; can't test excluded), `compliance_strict` (can't test = 0), by source, blocked and can't-test counts, conformance pass rates from the snapshot | Mean compliance, CI, floor (0) | Over (app, requirement), ordinal (not met = blocked < partial < met) |
| security | Checklist: per item the app's vulnerable / (vulnerable + not vulnerable). Reports: totals by tester severity and category. Console: request counts | Checklist `vulnerable_share` (mean over apps, CI). Reports `per_rater_mean` (mean over apps of reports per submitted assignment, CI) | Checklist items over apps, nominal (not tested excluded); findings, nominal TP/FP (unsure excluded) |
| scenarios | Success share (partial is not success; not attempted excluded), median seconds, mean ease | `success_rate` mean over apps, CI. The pooled `success_rate` is descriptive | none |
| questionnaire | Instrument score per response (0..100); custom questions as a distribution plus a mean for numeric kinds | Mean of app means, CI | none |
| bugs | Reports, `per_rater_mean`, by tester severity, `none_found` | Mean of `per_rater_mean`, CI (no floor) | none |
| pairwise | Bradley–Terry `by_app`, win / tie / loss | Bradley–Terry `by_group` | Per criterion over items, ordinal in canonical orientation |

`percent_agreement` is the share of agreeing rater pairs. `n_units` counts units with at least two ratings.

### Requirements against conformance

`vs_conformance` correlates human compliance with the conformance suite on **within-spec percentile ranks**, so spec difficulty does not drive ρ. Only apps graded by the modal (`evaluator_version`, `tester_sha256`) count. Apps that are not rated, have no graded result, were graded by another evaluator version, or are the only rated app of their spec are listed in `excluded_apps`. There are two scopes:

- `endpoints` (primary): `api` and `admin_api` items against the pass rate of the api and admin checks;
- `overall` (secondary, a different construct).

The Spearman CI is a bootstrap with the task seed.

`endpoint_agreement` matches each `METHOD /path` requirement to the conformance check of the same name and compares the human majority (met = pass) with the check's result: the counts and Cohen's κ. Majority ties and unmatched items are counted in `n_unmatched`.

### Finding precision

The primary estimator is a per-finding soft label: the TP votes divided by the TP + FP votes, with unsure votes excluded. It is averaged with inverse-probability weights from the sampling frame (population / sampled per tool and app) for `overall`, `per_group` and `per_tool_severity`; `per_tool` is unweighted. The CI is a bootstrap over apps. Secondary statistics are `majority_precision` (ties and unsure dropped) and `n_split` (findings whose votes disagree). `per_tool_severity` groups by the tool's own label ("unrated" when blank); the normalized severity is never used.

### Bradley–Terry

Displayed sides are mapped back to apps with `swapped`. For `five_point`, the sign decides the winner. Every comparison is put in canonical orientation (+ = `app_a` better).

- **Model.** Strengths are maximum likelihood (Newton–Raphson on log strengths, MM as fallback). Ties count half a win for each side. A weak prior adds 0.5 wins and 0.5 losses against a virtual opponent of fixed strength 1, which is not part of the normalisation or the output. Strengths are normalized to geometric mean 1.
- **Components.** `by_app` is fitted per connected component of the comparison graph (`BtEntry.component`); strengths and ranks are comparable only within one component. `by_group` uses comparisons between groups only.
- **CIs.** Cluster bootstraps on log strength, 1000 draws. `by_app` resamples items with all their comparisons; `by_group` resamples apps within each group and keeps the comparisons among the resampled apps.
- **Position bias.** Wins of the displayed-left app against the right are tested with an exact two-sided binomial test (ties excluded). The warning `position_bias` appears when p < 0.05.

### Human against automated

`human_vs_automated` correlates the composite of the first rating block with two automated measures, on within-spec percentile ranks:

- the conformance pass rate, from the modal evaluator version;
- static findings per kLOC, counted only over tools present in every app's snapshot, not deduplicated, with AI, dynamic and dependency tools excluded.

Each row's `note` lists the excluded apps.

### Contrasts

With `group_by=condition`, every metric of the analysis-plan vocabulary compares each non-baseline condition with the baseline:

- the metrics are rating composite and criteria, compliance, vulnerable share, success rate, questionnaire score, bugs per rater, and the pairwise preference share ((wins + ties / 2) / comparisons);
- the baseline is `pairing.baseline_condition_id`, else the study's baseline; without one there are no contrasts and the warning `no_baseline` appears.

When at least three (`app_slug`, `repeat_index`) cells are common to both conditions, the contrast is paired: a bootstrap CI of the mean delta and a Wilcoxon signed-rank p. Otherwise it is unpaired: a bootstrap CI of the difference of group means, with no p. Holm adjusts across all contrasts of the task, and `primary` marks the plan's metric.

### Confirmatory results

`confirmatory` is true only when `deviations` is empty. The possible deviations are:

| Deviation | Cause |
|---|---|
| `provisional` | The task is not closed or archived |
| `reopened_after_close` | The task was reopened after its first close (`first_closed_at` ≠ `closed_at`) |
| `stopped_early` | The planned sample was not reached. Balanced: every active item needs the planned number of submitted, non-excluded ratings, where `app_broken` skips count. All: the planned number of non-excluded participants completed or did every active item |
| `no_planned_sample` | Strategy `all` without `planned_participants` |
| `post_hoc_exclusions` | A manual exclusion was made after the first open |
| `no_primary` | No primary metric was registered |

Rule exclusions do not count against the plan: they are fixed in advance. P-values (contrasts, position bias) are withheld while the results are provisional (`p_values_withheld`).

### Quality flags

| Flag | Rule (`services/quality.py`) |
|---|---|
| `speeding` | A submitted assignment where both `active_seconds` and `wall_seconds` are below `quality.min_seconds_per_item`. Without heartbeat wall time, `submitted_at − started_at` stands in |
| `straightlining` | A rating block with at least four numeric answers that are all identical |
| `attention_failed` | An attention question (pre/post or in a custom questionnaire) answered with something other than its expected option |
| `app_not_viewed` | A slot with 0 viewed seconds and no `first_running_at` |
| `possible_duplicate` | Another participant of the task shares the device hash |

Flags describe the data and never change rows. The rules in `exclusion_rules` turn the first four into exclusions. The `quality` table lists, per participant, submitted and skipped counts, the median seconds per item and the flag counts.

### Warnings

| Code | When |
|---|---|
| `provisional` | The task is still collecting data |
| `low_n` | Fewer than 5 rated apps, or fewer than 3 raters per app on average |
| `low_agreement` | α below 0.667 with at least 5 units |
| `position_bias` | Exact binomial p < 0.05 for left against right (closed or archived tasks only) |
| `unbalanced_coverage` | The most-rated item has more than twice the ratings of the least-rated |
| `app_unavailable` | Skips with reason `app_broken` or `platform_unavailable`, plus how unevaluable apps were treated |
| `mixed_evaluator_versions` | Apps were graded by several conformance evaluator or tester versions |
| `shared_app_state` | No reset between assignments and an app with more than one submission |
| `instructions_changed` | Assignments saw different versions of the instructions |
| `no_baseline` | No baseline condition (or it has no apps), so no contrasts |

The task-level `open_warnings` (`shared_app_state`, `mixed_spec_versions`, `model_name_in_source`, `layout_reveals_condition`) are shown before opening, on the Overview tab.

## Exports

All exports are owner-scoped researcher routes. Filenames are `human-eval-<slug(title)>-<kind>.csv`.

| File | Rows |
|---|---|
| `items.csv` | Every item, including inactive and never-rated ones |
| `responses.csv` | Raw answers: one row per assignment and block (JSON as stored, sides as displayed) |
| `ratings.csv` | One row per assignment and criterion (value, `na`, scale, comment) |
| `requirements.csv` | One row per assignment and requirement shown (verdict, score, strict score, note) |
| `security_checklist.csv` | One row per assignment and checklist item |
| `pairwise.csv` | One row per assignment and criterion (plus overall): `app_a_label`, `app_b_label`, `displayed_left_label`, `swapped`, `raw_value`, `canonical_value` (+ = app_a better), `winner_label` |
| `scenarios.csv` | One row per assignment and scenario (outcome, success, seconds, ease) |
| `questionnaires.csv` | One row per assignment and question, with `instrument_score` |
| `observations.csv` | Bug, vulnerability and note reports (`tester_severity` is the participant's own) |
| `finding_verdicts.csv` | TP / FP / unsure verdicts with the finding as shown (`tool_severity` only) |
| `participants.csv` | One row per participant, one column per pre/post question, attention pass/fail columns |
| `assignments.csv` | Status, timings (`active_seconds`, `wall_seconds`), exclusions and, per slot, hosting provenance and viewing |
| `apps.csv` | The de-blinding key: model, provider, condition, study, task (`task_ref`, `prose_variant`) and `spec_content_hash`, functional (eval suite) results, static findings, finding sample size and frame, assignment counts |
| `http_log.csv` | Every console request and its response excerpt |

`GET tasks/{id}/export/bundle.zip` contains every CSV plus `design.json`, `settings.json`, `results.json` and `codebook.md`. `export/results.json` takes `group_by` and `include_excluded`. `export/{kind}.csv` accepts `include_excluded` but ignores it.

- **Every row.** CSVs always hold every row: excluded ones, unfinished work (`assignment_status`) and never-rated items. The columns `excluded`, `exclusion_reason`, `participant_excluded`, `quality_flags` and `rule_excluded` let a re-analysis reproduce or vary every exclusion. `rule_excluded` uses the same rule matching as the results. Previews are never exported.
- **Format.**
  - UTF-8 without BOM, `\n` line ends;
  - `true` / `false`; an empty cell means missing;
  - ISO 8601 UTC timestamps; lists joined with `;`; canonical JSON cells.
  - Text starting with `=`, `+`, `-`, `@`, tab or CR that is not a number gets a leading `'`; numbers are written as numbers.
- **Privacy.** Participants appear only as `P001` labels. No email address, user id, access code or hint, invite token, device hash, completion code or IP address is exported.
- **Codebook.** `codebook.md` documents:
  - the study record (task id, seed, design hash and the keys left out of it, freeze, open and close times);
  - the pre-registered plan, exclusion rules and flag definitions;
  - the value codings;
  - the hosting isolation and reset, and the out-of-scope admin login;
  - the protocol log;
  - every column of every file.
- **De-blinding.** Every file uses the blinded labels. Join on `app_label` with `apps.csv` (pairwise: `app_a_label`, `app_b_label`, `displayed_left_label`) to recover model and condition. Keep `apps.csv` away from raters.

## Validity checklist (decide before opening)

The first open freezes these choices. Make them before inviting anyone:

1. **Primary metric and planned sample.** Set `analysis.primary`, plus `planned_ratings_per_item` (balanced) or `planned_participants` (all). Without them nothing is confirmatory.
2. **Exclusion rules.** Decide which of `speeding`, `attention_failed`, `straightlining` and `app_not_viewed` exclude data, and the speeding threshold (frozen once `speeding` is a rule). Exclusions decided later are post hoc.
3. **Unevaluable apps.** Choose `exclude` or `floor` for apps that are too broken to rate.
4. **App state.** Security testing or anything that writes data needs `exclusive` + `per_assignment`, or accept and report `shared_app_state`.
5. **Blinding.** Decide on showing source code (check `model_name_in_source` and `layout_reveals_condition`), the spec and the admin login.
6. **Pairing.** Check that same-spec, cross-condition pairs exist and that the baseline is right; check `mixed_spec_versions`.
7. **Allocation.** Set `ratings_per_item` and the strategy, and whether overflow is allowed.
8. **Instructions and consent.** Edits after opening are logged and warned about in the results.
9. **Hosting.** The environment must report `apps_reachable`, with no `shared_client_ip` warning in production. Pre-start pinned apps, or check that on-demand capacity fits the expected number of concurrent participants.
10. **Pilot.** Open the task, run a researcher **Preview** end to end, then reset the preview. Preview data never reaches results.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HUMAN_EVAL_MAX_RUNNING_APPS` | `4` | Server-wide cap on task-started apps running, building or starting at once (each task also has `hosting.max_running_apps`) |
| `HUMAN_EVAL_MAX_CONCURRENT_BUILDS` | `1` | Image builds at once for task apps |
| `HUMAN_EVAL_ATTACHMENT_MAX_BYTES` | `5242880` (5 MiB) | Largest screenshot a participant may attach |
| `HUMAN_EVAL_SESSION_IDLE_HOURS` | `24` | A portal cookie session ends after this long without activity |

Derived settings, not read from the environment: `HUMAN_EVAL_COOKIE_SECURE` (`FRONTEND_PUBLIC_ORIGIN` starts with `https://`), `HUMAN_EVAL_COOKIE_NAME` and `HUMAN_EVAL_DEVICE_COOKIE_NAME` (the `__Host-` names when secure).

Related settings:

- `FRONTEND_PUBLIC_ORIGIN`: cookie security, email links.
- `TRUSTED_PROXY_CIDRS`: the client address for throttles.
- `APPS_SUBDOMAIN_PROXY` / `TRAEFIK_DYNAMIC_DIR` and `APPS_DOMAIN`: participant-reachable apps.
- `PASSWORD_RESET_TIMEOUT`: the lifetime of the set-password link.
- The email backend: `send_email`.

## API summary

Researcher routes are under `/api/human-eval/`, with the usual Bearer token or session auth. They are owner-scoped: another user's task, or a malformed id, is a 404, never a 403, and staff see every task. Every nested object (app, participant, invite, assignment, attachment) is looked up within its task, so a mismatch is a 404. Refusals are `{"detail", "code", "errors": [{path, message}]}`, for example:

- `design_frozen`, `open_blocked`, `invalid_transition`, `not_frozen`;
- `access_disabled`, `quota_exceeded`, `forbidden`;
- `shared_container`, `hosting_busy`, `no_code`, `task_closed`.

Full shapes are in `frontend/src/lib/api/types/human-eval.ts` and the OpenAPI UI ([API reference](/docs/api-reference)).

| Route | Purpose |
|---|---|
| `GET presets/`, `POST validate-design/`, `GET candidate-runs/`, `GET environment/` | Presets; validate `{mode, design, settings}`; completed runs you can add (filters, `current_only`); routing, caps, warnings, defaults, instruments, default checklist |
| `GET/POST tasks/` | List (`status`, `search`, `page`, `per_page`) and create (`preset`, `study_id`, `run_ids`) |
| `GET/PATCH/DELETE tasks/{id}/`, `POST …/duplicate/`, `POST …/status/` | Detail, update, delete; duplicate; `{action}` open, pause, close, reopen, archive |
| `GET …/items/`, `POST/DELETE …/preview/` | Items with counts; the researcher's preview session |
| `GET/POST …/apps/`, `PATCH/DELETE …/apps/{app_id}/` | The pool: list; add (`run_ids`, `study_id`, `current_only`; draft only); activate (any time) or relabel (draft only); remove (draft only) |
| `GET/POST …/hosting/`, `POST …/apps/{app_id}/hosting/` | Hosting panel; `start_all` and `stop_all`; per app `start`, `stop`, `pin`, `unpin`, `rebuild` |
| `GET …/participants/`, `POST …/participants/codes/`, `POST …/participants/accounts/` | Participants (filters `kind`, `status`, `search`); create access codes; add accounts |
| `PATCH/DELETE …/participants/{pid}/`, `POST …/regenerate-code/`, `POST …/setup-link/` | Exclude, note, withdraw, delete; new access code; new set-password link |
| `GET/POST …/invites/`, `PATCH/DELETE …/invites/{invite_id}/` | Invite links |
| `GET …/progress/` | Counts by status, coverage histogram, timeline, median minutes per item |
| `GET …/assignments/`, `GET/PATCH …/assignments/{aid}/` | Responses browser; detail; manual exclusion |
| `GET …/observations/`, `GET …/attachments/{att_id}/`, `GET …/http-log/` | Reports, screenshot bytes, console log |
| `GET …/results/` | `TaskResults` (`group_by`, `include_excluded`) |
| `GET …/export/{kind}.csv`, `…/export/bundle.zip`, `…/export/results.json` | Exports |

Portal routes are under `/api/human-eval/portal/` and are not exposed through the MCP server. The router's `PortalAuth` accepts the participant session cookie and/or a signed-in session user (an evaluator account, or the researcher's preview). It enforces CSRF on every unsafe method and answers 401 when neither identity is present. Bearer tokens are not accepted. Everything a participant reaches is looked up through their own participant (404 otherwise), and portal payloads never contain task-app ids. Assignment mutations answer 409 `read_only` once the assignment is submitted, skipped or expired, the task stops accepting work, or the participant withdrew.

| Route | Auth | Purpose |
|---|---|---|
| `GET/POST join/{token}/` | Public (POST: CSRF, throttled) | Invite info and availability; join (sets the cookies, returns the access code once) |
| `POST resume/` | Public (CSRF, throttled) | Continue with an access code; rotates the session |
| `POST logout/`, `GET me/` | Public (POST: CSRF) | End the cookie session; who this browser is and its participations |
| `GET tasks/{id}/`, `POST …/consent/`, `PUT …/pre-answers/`, `PUT …/post-answers/` | Portal | Task page; consent; questionnaires |
| `POST …/next/`, `POST …/finish/`, `POST …/withdraw/` | Portal | Next item (`NextResult.reason`); completion code; withdraw (`delete_data`) |
| `GET assignments/{aid}/`, `PUT …/responses/{block_id}/`, `POST …/submit/`, `POST …/skip/` | Portal | Workbench (the first open starts it); autosave (400 `invalid_answers`); submit (400 `incomplete` with per-field errors); skip (`app_broken`, `platform_unavailable`, `cannot_evaluate`, `conflict_of_interest`, `other`) |
| `GET/POST …/observations/`, `PATCH/DELETE observations/{oid}/`, `POST …/attachments/`, `GET/DELETE attachments/{att_id}/` | Portal | Reports and screenshots (served inline with the detected type, `nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`) |
| `PUT …/finding-verdicts/{finding_id}/` | Portal | TP / FP / unsure on a finding of this assignment's context |
| `GET …/source/`, `GET …/source/file/` | Portal | Source tree and file (only with `show_source_code`) |
| `POST …/heartbeat/`, `POST …/apps/{slot}/ensure/`, `POST …/console/` | Portal | Timing and liveness; start or queue the slot's app; HTTP console |
