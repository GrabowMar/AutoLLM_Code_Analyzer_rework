# Evaluation (EvalSuite v2)

How a generated sample is measured: lock, build, start, functional checks, security probes, adherence and the
starter baseline. Spec: `docs/design/SAMPLE_GEN_V3.md` sections 4.8, 6.10-6.12 and 8. Code:
`backend/generation/evaluation/`, images `backend/generation/images/{eval-tester,adherence-checker}/`.

The rule behind every choice below: **a measurement that did not happen is "not measured", never clean and never
0.** A tester crash, a level that skips a stage, a probe whose precondition failed, a starter that never starts:
each is labelled as such and stays out of pass rates.

## 1. Stages and outcomes

`evaluation.suite.evaluate(run, suite, *, only_checks, probes, mode, image_override, overlay_files)` writes one
`RunEvaluation` (append-only; a pending re-evaluation row created by `POST /runs/{id}/evaluate/` is filled
instead of creating a second one). `run_eval_stage(run)` is what the eval worker calls; it picks the suite and
level, dispatches slot runs to `slot.evaluate.evaluate_variant`, reuses an identical tree's evaluation when the
budget preset allows it (`caches.reuse_eval_by_tree`), sets the run's outcome, adds the validity notes
(`lock_source:*`, `adherence:*`, `eval_reused`), mirrors the result into `run.metrics["eval"]` (with
`slot_pass_rate` for slot runs) and ends the stage.

| Stage | Runs at level | Result | Outcome |
|---|---|---|---|
| lock (`lock.resolve_lock`) | static and above | `stages.lock` ok / fail / error | `lock_unresolvable` -> `build_fail`; registry/docker outage -> `infra_error` (`lock_infra`) |
| build (`sample_builds.build_sample`) | build and above | `stages.build` | failure -> `build_fail`; `infra_suspected` retried once, a repeat -> `infra_error` (`build_infra`); `base_not_allowed` -> `build_fail`; `allowed_bases_changed`, `variant_not_assembled`, `lock_incomplete` -> `infra_error` |
| start | functional and above | `stages.start` (`credentials_ref`) | not healthy within `timeouts.start_s` -> `start_fail` |
| tester | functional and above | `stages.tester` (`only_checks`, `probes`, `image_override`, `overlay`), `result.checks` | a tester crash is retried once inside the stage; a repeat -> `RunEvaluation.status=error`, outcome `infra_error` (`tester_crash`) |
| probes | security and above | `stages.probes`, `result.probes` | below that level the task's probes are listed `skipped(level)` |
| adherence | static (static part), build and above | `RunEvaluation.adherence` | never an outcome; a column and a note |
| baseline | functional and above | `metrics.baseline_pass_rate`, `over_baseline` | `None` when no baseline exists |

`outcome_of(evaluation)`: a measured tester -> `tested`; a level without the tester (`none`, `static`, `build`)
-> `tested` with `outcome_reason=not_tested:<level>` and `result.measured=false` (the pass rate is *not
measured*); anything that errored -> `infra_error` with the failing stage's reason. A run whose generation
ended `output_invalid` is never built. The eval stage never calls `transitions.infra_retry` (it would wipe the
collected tree); a failed evaluation is retried with the runs API.

`mode=overlay` (slot variants, spec 4.8) skips lock and build and starts the parent image with the slot files
copied in; `mode=static_only` runs no app.

**Platform CPU faults (`platform_cpu`).** A step killed by `SIGILL` -- "Illegal instruction", BuildKit's `exit
code: 132`, the legacy builder's `returned a non-zero code: 132`, a Go/Rust `SIGILL: illegal instruction`,
gunicorn's `was sent SIGILL`, or an app process/container exit code 132 (`-4` from `Popen.poll`) -- ran a
prebuilt binary compiled for a newer x86-64 level than the evaluation host (Ivy Bridge: AVX, no AVX2/BMI2/FMA).
It is never the sample's fault: the lock step (`LockInfraError`), the build (before `lock_incomplete`,
`build_infra` and `build_fail`; never retried, it is deterministic), the start (instead of `start_fail`) and the
app during the tester (the checks are discarded) all end `stages.<stage>.status=error`, `detail.reason=
platform_cpu`, `detail.signal=SIGILL`: `RunEvaluation.status=error`, `result.measured=false`
(`not_measured_reason=infra:platform_cpu`), outcome `infra_error:platform_cpu` -- not usable, attrition, no pass
rate (never 0). `suite.platform_cpu_fault(log, exit_code=)` is the detector; `suite.stored_platform_cpu_fault(
evaluation)` reads a stored row's failed stages (operator scans). First seen 2026-11-09:
`@rolldown/binding-linux-x64-gnu@1.2.18` (vite 8's bundler) ships an x86-64-v3 mimalloc.

*What qualifies* (so a sample's own failure is never relabelled): only what the OS, a shell, a runtime or a
builder prints for a process that died of SIGILL -- sh/dash `Illegal instruction (core dumped)` or `Illegal
instruction` ending its line, bash `<pid> Illegal instruction  (core dumped) <cmd>`, `Fatal Python error: Illegal
instruction`, `SIGILL: illegal instruction`, `signal SIGILL` / `signal: 'SIGILL'`, `<Signals.SIGILL: 4>`, `was sent
SIGILL`, exit status 132 (`exit code: 132`, `returned a non-zero code: 132`, `exit status 132`, `exited with code
132`). The words in the sample's own text do not count: a quoted string (`throw new Error("Illegal instruction")`
in a compiler excerpt), an exception message (`Error: Illegal instruction`), `signal.SIGILL` in a traceback.
**Build:** the failed step decides (`suite.build_platform_cpu_fault`): its exit status 132 is SIGILL; with any
other status a SIGILL report counts only inside that step's own output (BuildKit `#N` lines of the `#N ERROR`
step, the legacy builder's last `Step`), so a SIGILL that an earlier step survived (a native probe with a
fallback, `|| true`) never relabels a later `build_fail`; a build that timed out (no step status) counts a SIGILL
report only in the step that was still printing. **Lock:** a resolver killed by SIGILL is never
`lock_unresolvable`, even when its output also says "No solution found". **Tester:** the checks are discarded only
when the tester did not measure or a check failed/errored; a clean measured result stands
(`stages.tester.detail.sigill_in_app_logs=true`). Every `platform_cpu` stage records `detail.host_cpu` (CPU model,
`x86_64_level`, the x86-64-v3 flags it lacks) as evidence. *Residual ambiguity:* a sample's own native code can
also die of SIGILL (`ud2` from `__builtin_trap`/undefined behaviour in C, C++, Rust or Zig it compiled) or call
`exit(132)`; logs cannot tell that apart from a missing CPU feature (only the kernel's `trap invalid opcode ...
in <file>` line names the faulting binary). Such runs are attrition, not 0, and the operator scan
(`reevaluate_platform_failures`, §9) lists every one; `infra_error_by_reason` in the attrition table reports them.

Postgres stacks (`database: postgres`, phase B) get a database sidecar on the eval network: the docker runtime's
`start_database("postgres")` starts `base_images.yaml` `sidecars.postgres` (pinned by digest) as alias `db` with a
fresh password, waits for `pg_isready` and passes `DATABASE_URL=postgresql://app:<pw>@db:5432/app` to the app; the
sidecar outlives app restarts and is removed with the evaluation. A sidecar that cannot start (or a runtime without
one, such as the local runtime) is `infra_error` (`postgres_sidecar_unavailable`), never `start_fail`. The
`plaintext_password_db` probe on postgres stacks reads the sidecar: after the tester, `dump_database()` runs
`pg_dump --data-only` in the sidecar, split into one entry per table (`postgres:public.users`), and the verdict
(`probes.plaintext_password_database_verdict`) looks in those rows and in the app's `./data` for user_a's
password, verbatim or hex-encoded (a `bytea` column dumps as `\x<hex>`). A clean `not_exploited` needs user_a's
username in some table or data file (the account was stored where we looked); otherwise the probe is
`skipped(precondition_failed)`. A failed dump is a probe `error` (never `not_exploited`, not counted in
`probes_tested`).

## 2. The platform lock (spec 6.10)

The first stage of every evaluation, once per sample. A run that already has a lock keeps it: a
re-evaluation never re-resolves. If every manifest of the collected tree has a complete lock written by the
model, `lock_source=model` and the files are used as written; a starter lock next to a manifest the model
changed (`stale_starter_lock`) or a lock that misses a direct requirement (`not_covered`) is not complete.
Otherwise the platform resolves the manifest in a container of the sandbox base image (`uv pip compile
--generate-hashes`, `npm install --package-lock-only`) and stores the lock at the starter-convention path
(`<dir>/requirements.lock`, `<dir>/package-lock.json`), so the starter Dockerfiles install under it;
`lock_source=platform`, `registry_snapshot_at` = now. For npm the tree's own `package-lock.json` (the model's or
the starter's, lockfile v2+) goes in next to `package.json` as the seed: npm keeps every locked version the
manifest still accepts and re-resolves only what changed; a seed npm cannot use (resolver error, or a result that
does not cover the manifest) falls back to the manifest-only resolve, so a broken model lock is never
`lock_unresolvable` by itself.

Go (phase B): a `go.mod` whose `go.sum` is missing or does not cover it gets the platform Go lock: `go mod
download all` in the pinned `base_images.yaml` `go` image with `GOTOOLCHAIN=local` (the toolchain the stack
profile names; never a newer one), on `go.mod` plus the tree's `go.sum`, written to `<dir>/go.sum`; `go.mod` must
come out byte-identical. A `go.mod` with no `require`/`replace` (standard library only) needs no `go.sum` and is
not locked. Model-attributable failures (`lock_unresolvable`, `build_fail`): an unknown module or version (proxy
404/410, `unknown revision`, `invalid version`), a `go` directive or dependency newer than the pinned toolchain
(`requires go >=`), a `go.mod` the go tool refuses or would have to update, a `go.sum` with a wrong checksum;
anything else (network, proxy, docker) is `lock_infra`. A `go.mod` with `replace` directives cannot be coverage
checked offline, so its downloaded `go.sum` is used as produced. Maven never reads its lock record
(`dependency-list.txt`) and is not platform-locked.

## 3. The tester (spec 8.1-8.5)

`images/eval-tester/tester.py`, standard library only (`http.client`); the same file runs in-process in the
backend tests and the conformance kit. Version constants: `VERSION=1`, `TESTER_RULES_VERSION=1`,
`PROBE_RULES_VERSION=1`.

**Plan.** `acceptance.compile(task_version, auth_mode, stack)` (`COMPILER_VERSION=1`) writes `/in/plan.json`: the
`AcceptancePlan` contract plus `health_path`, `signed_routes`, `mock_host`/`mock_port`, `has_frontend`
(`acceptance.TesterPlan`). It never contains a secret: `ADMIN_PASSWORD` and `WEBHOOK_SECRET` reach the tester
through its environment. The generated check ids are listed in the module docstring
(`<endpoint>.ok`, `.anon_401`, `.other_user`, `.non_admin_403`, `.invalid.missing|too_long|enum`, `.duplicate`,
`.oversize`, `.page_size`, `.filter.<param>`, `.search`, `.persists`, and the infra checks `health`,
`index_served`, `spa_fallback`). Every generated check depends on `health`. A check that needs a resource
gets setup steps that create it through the collection's `POST` endpoint (recursively for nested paths); a
check whose resource cannot be created that way is not generated. When an endpoint has no `request_schema`,
the body is derived from the written entity's fields (and no validation check is generated: the task states
no rule). A request body field named `username` (outside the auth endpoints) names `user_b`. Hand-written
`acceptance` checks are merged after the compiled ones; an id collision means the hand-written check wins.
Compiled checks are tagged with `library.lint.tag_checks` (`stated_in`), hand-written ones read
`TaskSpecVersion.derived.check_statedness`.

The login check (`<login endpoint>.ok`) also requires the auth mode's credential, not just a 2xx: a non-empty
string at `token_json_path` for bearer modes, a `Set-Cookie` header for cookie modes. Without it, an app whose
catch-all route answers every request with the SPA's `index.html` (200) passed the login check (found on the
Django minimal starter in the wave-3 conformance run).

**Phases.** Persistence checks (`restart_before`) need the app restarted between their setup and their request.
Phase `pre` runs every other check plus those setups and saves the state; the suite restarts the app; phase
`post` logs the actors in again, runs the deferred checks, then the probes.

**Conventions the seed checks rely on** (WP-04):

- `json_contains` is a recursive subset match; a list matches when the observed list holds, in the same
  relative order, one element matching each listed element (an ordered subsequence).
- `capture: {name: "$.json.path"}` saves a value; later steps and the check's own request substitute `{name}`
  in the path and in any JSON string value that is exactly `"{name}"` (keeping the captured type). `{rand}`
  (per check) is substituted inside strings, `{oversize}` expands to 12 MiB, and `{user_a_username}`,
  `{user_b_username}`, `{admin_username}`, `{admin_password}` name the actors. A check that uses
  `{user_a_username}` or `{user_b_username}` registers and logs in that actor first (the named account must exist;
  if it cannot log in, the check is `skipped` with `auth:<actor>`).
- `header_contains: {"Retry-After": ""}` means the header is present; otherwise a case-insensitive substring.
- Multipart: `request.files: {"<field>": {"filename", "content", "content_type"}}` plus `form` fields.
- Setup steps without `actor` run as the check's actor; `actor: anon` runs without credentials (an anonymous
  client starts every check with an empty cookie jar).
- Webhooks: requests to an endpoint tagged `webhook` are signed with `WEBHOOK_SECRET` (the suite sets it for the
  app and the tester): `X-Timestamp: <unix seconds>`, `X-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw
  body>">`, setup steps included. The main request of an `auth`-category check is never signed: it tests the
  signature requirement itself (e.g. `webhooks.unsigned` of `webhook_receiver_signed` expects 401).
- Password reset links are read from `GET /api/outbox?to=<email>` (the in-sandbox mail stand-in).

**Auth drivers** (8.4): `contract_bearer` registers `user_a`/`user_b`, logs in and sends `Authorization: Bearer
<token>`; `admin` logs in with `ADMIN_PASSWORD`. `cookie_session` keeps a cookie jar per actor and sends
`X-CSRF-Token` equal to the `csrf_token` cookie on state-changing requests. `spec_defined` follows the task's
`auth_flow` (endpoint ids resolved to paths). An actor that cannot log in makes its checks `skipped`
(`skipped_due_to: ["auth:<actor>"]`), which counts as a fail.

**Tolerances** (`TESTER_RULES_VERSION=1`):

1. One same-origin trailing-slash redirect (301/302/307/308 to the same path with or without the trailing slash,
   e.g. Django `APPEND_SLASH`) is followed by retrying the canonical path, for any method. A check that expects
   a 3xx is never redirected.
2. An expected 400 or 422 accepts either (FastAPI answers 422).
3. "Anonymous" expects 401 per the auth contract; a 403 is a contract failure.
4. Oversize bodies accept 413 from the framework or the app. Bodies over 1 MiB are streamed in 64 KiB chunks and
   the tester stops sending as soon as the app answers: many servers (gunicorn/Flask `MAX_CONTENT_LENGTH`, an
   upload-size middleware) answer 413 from the headers and close the connection, and writing the rest would turn
   that correct 413 into a broken pipe (`error`).
5. Wrong-method behaviour (405 vs 404) is never checked.

**Results and metrics** (`metrics.compute`): `agent_pass_rate` = passed / counted checks, where counted means every
non-infra check of the selection minus the suite's `flaky_checks`; `error` (app connection reset or refused) and
`skipped` (a `depends_on` failed) count as fail. No counted check gives `None`. A tester-side exception makes the
whole result `measured=false`. `stated_pass_rate`/`unstated_pass_rate` split the counted checks by the run's
prose variant; `common_pass_rate` is computed by the study results (it needs the study's auth modes).

## 4. Probes (spec 8.3)

Kinds and their `requires` tags: `evaluation/probes_catalog.yaml` (`GET /eval/probes/catalog/`). Each probe runs in
three steps: (1) **precondition**: every check in `requires_checks` (default: the endpoint's `.ok` check, or
`auth.login.ok` for task-level probes) passed, else `skipped(precondition_failed)`; (2) **positive control**: a
benign request (default: the endpoint's valid request; for `default_credential` the real admin login) must
succeed, else `skipped(positive_control_failed)`; (3) the **attack**, giving `exploited` or `not_exploited` with
evidence. Only step 3 counts in `probes_tested`. No severity is assigned. `plaintext_password_db` is read by
the suite after the run: from the app's data directory (`docker cp`), and on postgres stacks also from a
`pg_dump` of the sidecar (section 1). Probes that change shared state
(`mass_assignment`, `rate_limit_missing`, `default_credential`) run last.

**Validity.** Every kind has a mutant pair in `evaluation/conformance/probe_mutants/`: one standard-library
reference app implementing the probe-kit task (`kit_task.yaml`: the `crud_todo_list` API plus one endpoint per
probe surface) whose `LLMLAB_VULN=<kind>` variant is vulnerable on purpose. `manage.py oracle_validate --kit`
runs each kind on the mutant (must be `exploited`) and on the fixed app (must be `not_exploited`) and writes
`conformance/probe_validation.json` (tied to the tester and probe-rules versions). A kind without a passing pair
is compiled `disabled` and every result of it is `skipped(disabled)` (`probe_unvalidated`). All 17 kinds pass
on the bundled app (2026-11-03); mutants on the real reference stacks follow in wave 3.

Known limits: `xss_stored` and `xss_reflected` only see payloads served as `text/html` (the tester does not run
the SPA's JavaScript); `sqli_time` needs a 2 s delay over the baseline; `csrf_missing` applies to cookie sessions
only (a bearer app skips it on the precondition).

## 5. Adherence (spec 8.7)

`images/adherence-checker/checker.py` (`CHECKER_VERSION=1`; the image adds `syft` for the SBOM) reads the tree and
`expect.json` (`{stack, style, auth_mode, runtime}`, where `runtime.auth` is what the tester saw at login). It
detects backend (imports, manifests, the `Server` header), frontend (dependencies, file extensions), database
(drivers, connection strings, DB files), styling (Tailwind class share, CSS modules, component-library imports)
and auth (cookie vs bearer). Status: `compliant` (no deviation), `partial` (styling or structure only, or a
mixed styling system), `violated` (backend, frontend, database or auth differs); free-choice stacks are always
`compliant` and the detected stack is reported as an outcome. A dimension that cannot be detected is not a
deviation. Adherence never filters a sample.

Phase-B stacks: `go.mod` (`require` blocks and single-line requires, indirect ones included; every Go require is
pinned), `pom.xml` (string scanning, no XML parser; comments, `<build>` plugin dependencies and
`<dependencyManagement>` are cut out, `${prop}` versions resolved from `<properties>`; a dependency without a
version is pinned when a `<parent>` with a literal version, a BOM import or a managed entry pins it; ranges,
`LATEST`/`RELEASE` and `-SNAPSHOT` are unpinned) and `build.gradle(.kts)` (string coordinates) are parsed into
`packages` (ecosystems `go`, `maven`; Maven names are `groupId:artifactId`). Go backend: a router module (`gin`,
`echo`, `fiber`, `chi`, `gorilla`, `httprouter`, `beego`; go.mod or import) is that framework, otherwise
`net/http` imports are `go-stdlib` (with the `go.mod` as second evidence kind). Java backend: Spring Boot
(`org.springframework.boot:*` dependencies, `org.springframework.boot` imports), Quarkus, Micronaut, Javalin,
Spark, Dropwizard, Vert.x. Database: Go driver modules/imports and `sql.Open("<driver>")`, Maven driver
artifacts and `jdbc:<kind>:` URLs in Java sources and `.properties`/`.yml` files.

## 6. Baselines, oracles, conformance, test-retest (spec 8.5)

- **Starter baseline.** `baseline.ensure_baseline(starter, task_version, auth_mode, suite)` evaluates the bare
  starter (a `reference` run, `reference_of=starter_baseline`) inline; `queue_baseline` does it through the eval
  queue. Only a measured evaluation becomes an `EvalBaseline` row; a starter that does not build or start gives
  no baseline and `baseline_pass_rate` stays `None`.
- **Oracles.** `data/tasks/<slug>/reference/<stack>/` holds a reference implementation. `POST
  /library/tasks/{slug}/versions/{v}/oracle/` calls `baseline.queue_oracle_runs`; a stack without a tree is
  recorded `missing`. `manage.py oracle_validate [--task] [--stack] [--runs 3]` runs each reference three times
  (every run must pass 100%) and the bare starter once (must score below 0.5); checks with mixed results become
  the suite's `flaky_checks` (a new suite version). The verdict goes to `TaskSpecVersion.oracle[<stack ref>]`.
- **Conformance kit.** `manage.py conformance_kit` evaluates the kit tasks' references (`crud_todo_list`,
  `team_roles_multitenant`, `file_share_multipart`) on every fixed stack; each must pass 100% with no exploited
  probe (`TaskSpecVersion.oracle["kit:<stack ref>"]`). `conformance.coverage() -> {framework: bool}` feeds the
  study resolver's `conformance_missing` issue. Until the reference trees are in `data/tasks/` (WP-04), every
  framework is reported not covered and confirmatory app studies stay blocked.
- **Wave-3 run on real starters (2026-11-03).** Kit references for the four phase-A frameworks (one `<fw>-none`
  stack each; 12 trees built on the minimal starters, held at `~/.cache/llmlab-v3-work/w3g9/refs/` for WP-04 to
  adopt) ran through the docker runtime with the real `conformance_kit` command: flask, fastapi, django and
  express each pass all three kit tasks (`agent_pass_rate == 1.0`, every probe `not_exploited`). They cover the
  tolerances on real frameworks: Django `APPEND_SLASH` 301s (URLs with a trailing slash), FastAPI 422s, a
  framework 413 (Flask `MAX_CONTENT_LENGTH`) and app 413s (FastAPI middleware, Django and Express
  `Content-Length` checks), multipart through Flask, python-multipart, Django and the WHATWG `formData()` in Node,
  and a platform lock (python-multipart added to a starter manifest). The starter baselines (`<fw>-none` and one
  frontend stack per framework, minimal and bare) build, start and measure: minimal starters score 0.0 on
  `crud_todo_list` (django-svelte scored 0.04 before the login-credential fix), bare starters end `start_fail`
  (no app). Phase B: `go-none`, `spring-boot-none` and the three `*-react-postgres` minimal starters build, start
  (postgres through the sidecar) and measure 0.0; go and spring-boot have no kit references yet, so
  `coverage()` reports them not covered. `oracle_validate --runs 3` passes all 12 references with no flaky check.
  The 17 probe kinds' mutant pairs validate on the docker runtime as well as locally. Two seed checks
  needed an explicit setup actor (WP-04): `file_share_multipart` `shares.revoked` and `team_roles_multitenant`
  `projects.cross_tenant`.
- **dev1 E2E run (2026-11-04, CHANGES E2E-25).** Kit references for go-stdlib and spring-boot (`go-none`,
  `spring-boot-none`; ports of the Flask references, written by an AI coding assistant, see their PROVENANCE.md)
  pass `conformance_kit`, so `coverage()` is true for all six frameworks. Oracle references now also exist for
  `notes_markdown_render` (all six `-none` stacks) and for `crud_todo_list`, `file_share_multipart` and
  `notes_markdown_render` on `flask-react` and `express-react` (the react starter plus the `-none` backend, the
  starter's placeholder UI); every one passes `oracle_validate --runs 3`. Validating `notes_markdown_render`
  found a compiler defect: the setup of a public (`anon`) check sent the boolean sample `false`, so it created an
  unpublished note and expected its public page to answer 200. The setup of an `anon` check now sends visibility
  flags (`published`, `is_active`, `public`, ... `_VISIBILITY_FLAGS`) as `true`; owner-only checks are unchanged,
  and so are the snapshot tasks' plans.
- **Test-retest.** `retest.run_retests(study)` re-evaluates a seeded random `retest_fraction` of the study's
  tested runs under the same suite and selection (`retest_of` set); `GET /eval/retest/?study=` reports per-check
  and per-run agreement and Cohen's kappa (`None` when nothing was compared).

## 7. Where evaluations run

`evaluation/runtime.py`, chosen by `EVAL_RUNTIME` (read at call time):

- `docker` (default): the apps daemon builds through `sample_builds`; each evaluation gets an internal bridge
  network (no egress); the app runs as alias `app` with memory/CPU caps (`EVAL_APP_MEM_LIMIT`, `EVAL_APP_CPUS`),
  all capabilities dropped; the tester runs as alias `mock.internal` (the `ssrf_mock` server). The tester and
  checker images (`EVAL_TESTER_IMAGE`, `EVAL_ADHERENCE_IMAGE`) must match the digests pinned in the suite
  (`EvalSuiteContent.tester.image_digest`, `adherence_checker.image_digest`), else `infra_error`
  (`image_digest_mismatch`).
- `local`: only the bundled standard-library apps (a `llmlab-local.json` marker at the tree root): the probe-kit
  validation and the WP-09 tests. It refuses any other tree, so a model's code never runs outside a sandbox.

Rebuilding an image: `docker build --provenance=false --sbom=false -t llmlab-v3-wt/eval-tester:dev
backend/generation/images/eval-tester` (same for `adherence-checker`), then re-pin the image ids in
`backend/generation/data/eval_suites/eval-v2.yaml` (a new suite version on re-seed). Without
`--provenance=false`, BuildKit attaches an attestation manifest with a timestamp, so on a containerd-store daemon
every rebuild of the same source gets a new image id and the pin breaks. The pinned ids are those of the host
daemon (containerd snapshotter, where the image id is the manifest digest). A daemon with the classic store
reports the config digest as the image id instead, so the images must be rebuilt and re-pinned on the daemon that
runs evaluations.

## 8. API (spec 9.9)

| Method | Path | Operation id |
|---|---|---|
| GET | `/api/generation/eval/suites/{ref}/checks/?task=&auth_mode=&stack=` | `eval_suite_checks` (plan preview, disabled probes) |
| GET | `/api/generation/eval/baselines/?task=&starter=` | `eval_baselines` |
| GET | `/api/generation/eval/probes/catalog/` | `eval_probe_catalog` |
| GET | `/api/generation/eval/conformance/` | `eval_conformance` |
| GET | `/api/generation/eval/retest/?study=` | `eval_retest` |

## 9. Commands

- `manage.py oracle_validate --kit [--kinds a,b] [--dry-run]`: validate the probe kinds (local; no docker).
- `manage.py oracle_validate [--task SLUG] [--stack SLUG] [--suite REF] [--runs 3]`: validate task oracles.
- `manage.py conformance_kit [--stack SLUG] [--suite REF] [--status]`: run or show the conformance kit.
- `manage.py reevaluate_platform_failures [RUN_ID ...] [--study ID] [--relock] [--include-reclassified]
  [--no-force-rebuild] [--note TEXT] [--by NAME] [--apply]`: list (default) or re-evaluate runs whose latest evaluation died
  of a platform CPU fault. `--apply` queues one re-evaluation per run under the superseded evaluation's suite
  (`evaluation.supersede.supersede`) and records `execution.eval_supersessions` (who, when, the superseded
  evaluation/outcome/`metrics.eval`, the new evaluation id, a dropped lock) plus the validity note
  `eval_superseded:platform_cpu` (warn). The run's outcome and `metrics.eval` follow the new evaluation when the
  eval worker finishes it (as for every re-evaluation); old `RunEvaluation` rows never change. `--relock` drops a
  platform lock (never a model lock) so the lock step re-resolves it, seeded with the tree's lock; the dropped
  lock stays in the blob store (`GenerationRun.iter_blob_refs` keeps `eval_supersessions[*].relock.
  previous_lock_blob` and its files from GC). `--relock` re-resolves every ecosystem the platform completed, not
  only npm. `--by` names the operator (default: the OS user). The executor does not autoreload: restart it on the
  new code before `--apply`.
