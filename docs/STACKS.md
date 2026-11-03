# Stacks, starters, build context and dependency caches

Owner: WP-05 (spec `docs/design/SAMPLE_GEN_V3.md` sections 4.2, 6.5, 6.10, 11.4). Code:
`backend/generation/build/`, data: `backend/generation/data/{stacks,starters}/` and
`backend/generation/data/prompts/{stack-*,run-contract-free}.*`, command: `manage.py build_starters`,
caches: the `depcache` block of `docker-compose.local.yml` and `compose/local/depcache/`.

## Stack profiles

A StackProfile (`data/stacks/<slug>.yaml`, `{name, description, content}`) says what the app is built with,
the run contract every sample follows, the stack fragment of the brief and the sandbox minimums.

| Backend \ Frontend | react | vue | svelte | none | Phase |
|---|---|---|---|---|---|
| flask (Python 3.13) | `flask-react` | `flask-vue` | `flask-svelte` | `flask-none` (Jinja pages) | A |
| fastapi (Python 3.13) | `fastapi-react` | `fastapi-vue` | `fastapi-svelte` | `fastapi-none` | A |
| django 5.2 (Python 3.13) | `django-react` | `django-vue` | `django-svelte` | `django-none` (templates) | A |
| express 5 (TypeScript, Node.js 24) | `express-react` | `express-vue` | `express-svelte` | `express-none` | A |
| go 1.25 (stdlib `net/http`, framework `go-stdlib`) | `go-react` | `go-vue` | `go-svelte` | `go-none` (`html/template`) | B (wave 3) |
| spring-boot 4.1 (Java 21, Maven) | `spring-boot-react` | `spring-boot-vue` | `spring-boot-svelte` | `spring-boot-none` (Thymeleaf named in the brief) | B (wave 3) |

Plus `free-choice` (`choice_mode: free`, bare starter only: the model picks everything and writes its own
Dockerfile from the allowed bases). Every fixed profile above uses SQLite under `./data`. Phase B also adds
the PostgreSQL variants `fastapi-react-postgres`, `django-react-postgres` and `express-react-postgres`
(`database: postgres`; `spring-boot-react-postgres` from the spec's "if time allows" is not shipped).
Frontends are Vite + TypeScript.

Run contract (all profiles): port 8000, API under `/api`, `GET /api/health` → `{"status": "ok"}`, the
frontend build (or the server-rendered pages) at `/`, data in `./data`, started by the root `Dockerfile`.
A `database: postgres` profile additionally gets **`DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db>`**
pointing at a PostgreSQL sidecar (`base_images.yaml` `sidecars.postgres`, `postgres:18-trixie@sha256:…`,
published as `build.dockerfiles.sidecar_images()`); the README and the stack fragment state it, and
`/api/health` must not depend on the database (the minimal starters never touch it there).

`resources`: 2g / 1 CPU for the phase-A fixed profiles and the go profiles; 3g / 2 CPUs for `free-choice` (a
JVM stack may be picked) and the spring-boot profiles; 3g / 1 CPU for the postgres variants (the sidecar
shares the sandbox). A study's sandbox is the maximum over its profiles (spec 4.2).

Brief fragments: `stack-<slug>@1` (one neutral paragraph: frameworks, languages, where the code lives) and
`run-contract-free@1`, the free-choice fragment, which renders the allowlist (`allowed_bases` context
variable). No fragment mentions security, evaluation or budget.

## Starters

`build/starters.py: compose(stack_profile, level) -> StarterContent` merges

1. the backend fragment `data/starters/backend/<framework>/<level>/` (`backend/free/bare/` for free choice;
   `backend/<framework>/<level>-<database>/` from `minimal` up for a non-SQLite profile, e.g.
   `backend/django/minimal-postgres/` whose manifest adds the driver; there is no fallback to the SQLite
   fragment, a missing one is a `StarterCompositionError`),
2. the frontend fragment `data/starters/frontend/<framework>/<level>/` (minimal only; bare has no code),
3. the checked-in lockfiles of those fragments (`data/starters/lock/`),
4. the rendered `data/starters/dockerfiles/<backend>.Dockerfile.jinja2` (no Dockerfile for free choice).

Files ending in `.jinja2` are rendered with the run contract and `has_frontend` (a template that renders to
nothing is left out, which is how one fragment serves `-react` and `-none` profiles); every other file is
copied byte for byte. Composition is deterministic: equal inputs give an equal `tree_hash`
(`tests/wp05_build/starter_hashes.json` freezes all 55: 33 phase A + 22 phase B). `compose.yaml` lists the
(stack, level) pairs; the composed trees are not checked in. Its `phase` label is provenance only: every
phase in `build.starters.ACTIVE_PHASES` (`A`, `B`) is composed, checked, locked and seeded.

| Level | Contents |
|---|---|
| `bare` | `Dockerfile` (installs `backend/` and `frontend/` manifests when present, runs the entry file by convention), `.dockerignore`, `README.md` with the run contract. No manifests, no code. |
| `minimal` | The framework installed and locked (manifest with only framework, server, the DB driver where the language has none built in, and the frontend build tool, plus its lock), `GET /api/health`, SPA serving of `frontend/dist` with the fallback to `index.html` (or a `GET /` placeholder page for `-none`), a one-component frontend shell rendering "App". No auth, no security or UI libraries, no comments addressed to the model. |
| `full` | Optional (spec 11.4); not shipped. `find_starter(profile, "full")` is `None` → `starter_missing`. |

DB drivers in `minimal`: none for SQLite on Python (`sqlite3`) and Node.js 24 (`node:sqlite`);
`modernc.org/sqlite` v1.59.0 (pure Go, the newest release whose `go` directive is ≤ 1.25) for go;
`org.xerial:sqlite-jdbc` (version managed by the Spring Boot BOM, no `spring-boot-starter-jdbc`, so nothing
connects at startup) for spring-boot; `psycopg[binary]` 3.3.7 / `pg` 8.23.1 for the postgres variants.

Entry conventions (bare and minimal):

| Backend | Code | Bare start | Minimal start |
|---|---|---|---|
| flask | `backend/app.py` | `python backend/app.py` | `gunicorn --pythonpath backend app:app` |
| fastapi | `backend/main.py` | `python backend/main.py` | `uvicorn --app-dir backend main:app` |
| django | `backend/manage.py`, `backend/config/` | `migrate` + `runserver` | `migrate` + `gunicorn config.wsgi:application` |
| express | `backend/src/server.ts` (run by Node.js 24 directly) | `node backend/src/server.ts` | same |
| go-stdlib | `backend/` = Go module, package `main` (`main.go`, `db.go`, `templates/`) | `go mod init app && go mod tidy` when there is no `go.mod`, `go mod download all` when there is no `go.sum`, then `go build` → `server` | `go mod download`, `go build` → `server` (run from `/app`, sources copied to `/app/backend` for templates) |
| spring-boot | `backend/` = Maven project (`pom.xml`, `src/main/java/app/`) | `mvn package` when `backend/pom.xml` exists, then `java -jar app.jar` (first jar in `target/`) | `mvn dependency:resolve`, `mvn package`, `java -jar app.jar` |

The Go and Spring Boot templates build in the language image (`golang`, `maven`) and run on a slim one
(`debian:trixie-slim` with the binary, `eclipse-temurin:21-jre-noble` with the jar). Go builds use
`GOTOOLCHAIN=local` (never a toolchain download at build time) and leave cgo on (the build stage has gcc and the
runtime the same glibc, so cgo drivers such as `mattn/go-sqlite3` work; no unstated build rule). Postgres variants reuse
their SQLite stack's Dockerfile template (Django's `migrate` then needs the sidecar at start).

Base images (`data/starters/base_images.yaml`) are pinned by index digest: `python:3.13-slim`,
`node:24-slim` (phase A); `golang:1.25-trixie`, `debian:trixie-slim`, `maven:3.9-eclipse-temurin-21`,
`eclipse-temurin:21-jre-noble` (phase B). The sandbox and eval runtime use the same Debian slim family; no
Alpine.

**Go version (lead decision, 2026-11-03).** Go stays at the release that built and smoke-started in wave 3:
`golang:1.25-trixie` at its recorded digest (go1.25.14), in the starter Dockerfiles, the lock image and the
agent sandbox (`images/agent-base`, checked by `wp07_harnesses/test_images.py`); profiles say `runtime: go1.25`.
Do not move it to a newer Go without the same verification (new digests in both places, new starter hashes, the
`go-*-minimal` docker tests, fragment text). Go 1.25 is out of upstream support, and `go mod tidy` picks the newest
module releases, some of which already need Go ≥ 1.26 (`modernc.org/sqlite` ≥ v1.60). The brief says Go 1.25 and
the agent runs `go` 1.25 with `GOTOOLCHAIN=local` in its sandbox, so such a failure shows up during the run,
where the agent can pin an older release (as the minimal starter does with v1.59.0).

### Lockfiles

`manage.py build_starters --lock` runs one container per manifest in the pinned base image (files copied in
and out through the Docker API, so it works on apps-dind or any `--docker-host`):

* PyPI: `uv pip compile --generate-hashes` of `requirements.txt` → `requirements.lock`. Dockerfiles install
  `pip install -r requirements.txt -c requirements.lock` (the lock is a pip constraints file).
* npm: `npm install --package-lock-only --ignore-scripts` → `package-lock.json` (`npm ci`).
* Go: `go mod tidy` → `go.sum`. The container gets every plain file of the module directory (tidy reads the
  imports; `*.jinja2` templates are left out, so third-party imports live in plain `.go` files such as
  `db.go`), and the step fails if tidy would change `go.mod` (the checked-in `go.mod` must be tidy). The index
  also records `inputs_sha` (tree hash of those files); a changed source makes the lock stale.
* Maven: `mvn dependency:resolve` + `mvn dependency:list -DincludeScope=runtime -Dsort=true` →
  `dependency-list.txt`. `pom.xml` pins the Spring Boot parent (whose BOM manages every version); the list is
  a record of the resolution, Maven never reads it back.

`build.locks.check_tree_locks` (the build seam) checks the locks a build enforces: pip, npm and Go
(`lock_covers(..., ecosystem="go")`: the `/go.mod` checksum of every requirement and the module checksum of
every direct one; a `go.mod` with `replace` is never covered). A starter `go.sum` is not origin bound (extra
lines are harmless), so it is judged by coverage alone. Maven's record is not checked (it cannot fail a
build). The platform's Go lock is `go mod download all` in the go image: it completes `go.sum` from `go.mod`
alone and never edits `go.mod`.

`lock/index.yaml` records the manifest sha, lock sha, image and tool per lock. Composition refuses a lock
whose manifest changed (`StaleLockError`); `build_starters --check` fails CI-style on that.

**Conventions the platform lock step (WP-09, spec 6.10) must follow** so starter Dockerfiles use its locks:
write Python constraints to `<dir>/requirements.lock` next to `<dir>/requirements.txt`, node locks to
`<dir>/package-lock.json` and Go checksums to `<dir>/go.sum` (the templates copy each with a `*`, so a
missing lock falls back to an unlocked install instead of failing the build).

### Starter rules (`build/starter_rules.py`, reported by library lint as kind `starter`)

| Code | Rule |
|---|---|
| `lint_starter_forbidden_content` | no file contains `LLM:`, `_comment` or `TODO: model` (any case) |
| `lint_starter_security_library` | no manifest or lockfile names a package of `data/starters/security_deny.yaml` (PyPI names PEP 503-normalized; Go module sub-paths included) |
| `lint_starter_unpinned_image` | every external `FROM` has `@sha256:`; `base_images` lists exactly those |
| `lint_starter_file_missing` | the Dockerfile (fixed stacks) and every listed lockfile are in `files`; every `minimal`/`full` manifest has a lock next to it; an unparseable manifest is reported, never skipped |
| `lint_starter_stack_mismatch` | free choice: bare only, no Dockerfile; `frontend: none`: no frontend project; minimal: the frontend project exists and the manifests name the profile's frameworks (spring-boot: `spring-boot-starter-webmvc`; go-stdlib has no framework package) |

`RULES_VERSION = 1` is `forbidden_content_check.linter_version`. `seed_starters` never stores a starter
that fails a rule.

### Seeding

`build.starters.seed_starters()` composes every entry of an active phase (A and B) against the latest
profile rows: an equal `content_hash` is a no-op, a different one creates `version + 1` (system row,
`derived_from` = previous); a user slug is never touched; unseeded profiles are `skipped`; a starter that
cannot be composed or fails a rule is `error`, whatever its phase. `seed_library` (WP-03)
calls it after stacks; `manage.py build_starters --with-stacks` also seeds WP-05's own fragments and
profiles with the same rules. `find_starter(profile, level)` returns `None` when a plan names a missing
starter (issue `starter_missing`).

```
manage.py build_starters --check                    # compose all in memory + rules (no DB, no Docker)
manage.py build_starters --lock [--all] [--docker-host unix:///var/run/docker.sock]
manage.py build_starters --with-stacks [--stack flask-react] [--dry-run] [--json]
```

## Build context (`build/context.py`, shim `runtime/services/build_context.py`)

* `prepare_build_dir(run, dest, *, report=None)`: the run's app tree (`RunView.files("app")`) verbatim,
  artifacts excluded, deleted paths removed, modes 0644/0755, plus the files of `resolved_lock_blob` (a
  variant uses its parent's). A slot variant gets the parent tree with its `slot` rows over it; a variant
  without rows yet gets its changed files from `slot.assemble.spliced_files(run)` (WP-12, pure). A run
  without a tree raises `ValueError`; a variant that cannot be assembled raises `VariantNotAssembledError`.
* Free-choice stacks: the model's own Dockerfile is checked with `build.dockerfiles.check_bases` against
  `settings.SAMPLE_BUILD_ALLOWED_BASES` (default `data/starters/allowed_bases.yaml`). A `FROM` passes when it
  names an earlier stage, or an allowlisted `image:tag` with no digest or the allowlisted digest. Anything
  else (another image, another digest, an `ARG`-built image, no `FROM`, no Dockerfile) raises
  `BaseNotAllowedError` (`reason == "base_not_allowed"`, outcome `build_fail`). `report.bases` (also in
  `report.to_dict()["bases"]`) holds the verdict and the expected digest of each base for
  `RunEvaluation.stages.build.detail`. Fixed stacks are not checked (their report shape is unchanged).
* **Base pins (never re-resolve a tag).** The allowlist used is the one frozen in the run's manifest
  (`RunManifest.allowed_bases.refs`, digest-pinned; the parent's for a variant; the current file only for a
  run without one). A tag-only `FROM python:3.13-slim` is not rewritten: `report.base_pins` maps it to the
  frozen `ref@digest` and the build passes `report.build_context_args()` (`--build-context
  python:3.13-slim=docker-image://python:3.13-slim@sha256:…`), so BuildKit uses that digest for every build
  and re-evaluation. Undigested manifest refs whose sha differs from the current allowlist raise
  `AllowedBasesChangedError` (`allowed_bases_changed`, an infra error).
* **Lock completeness.** Every manifest with a lock beside it is checked (`build.locks.check_tree_locks`) and
  listed in `report.locks`: a starter lock next to a manifest it was not generated from is
  `stale_starter_lock` (a model-added requirement fails pip's `--require-hashes` mode or `npm ci`), a lock
  that does not pin every direct requirement is `not_covered` (`build.locks.lock_covers`). Both are noted
  `lock_incomplete: …`: the platform lock step must replace the lock, and a build failure behind such a lock
  is never attributed to the model.
* `build_identity(run)` = `{"protocol", "stack": pinned ref, "starter": tree_hash, "lock": sha}` (parent's for
  variants), plus `allowed_bases` (the frozen allowlist sha) for free-choice stacks; the sample-build cache
  key input.
* `allowed_bases_record()` = `{"sha", "refs", "tags"}`: `sha` + `refs` (digest-pinned `ref@sha256:…`, the
  form rendered into the brief) are `RunManifest.allowed_bases`; `sha` is `treatment_hash`'s
  `allowed_bases_sha` for free-choice cells (it covers refs and digests, in rendered order).

### Merge note for `runtime/services/sample_builds.py` (spec 6.10)

Applied inside U1 by WP-02a and kept since:

* the import is `from backend.runtime.services.build_context import BuildContextReport, build_identity,
  prepare_build_dir` (the v2 `runtime.services.scaffolding` is gone);
* `_context_key` hashes `build_identity(run)` under `"build"` instead of `job.resolved_bundle` /
  `snapshot["stack"]`;
* there is no healing concept left: the `healing` kwargs and the constant `"healing": "none"` identity field
  were dropped and `SampleBuildResult.healing` is `build_context` (`report.to_dict()`), so sample-image cache
  keys changed once (clean break);
* `BaseNotAllowedError` / `VariantNotAssembledError` / `AllowedBasesChangedError` propagate out of
  `build_sample` to the eval suite (WP-09), which maps the first to `build_fail` / `base_not_allowed` and the
  others to `infra_error`;
* still open (CHANGES ask to the runview owner): `build_backend.build` must take extra args and
  `build_sample` must pass `report.build_context_args()`.

## Dependency caches (`build/depcache.py`, compose profile `depcache`)

Pull-through and read-only; locks decide versions, so the caches are class O and in no identity key.

| Service | Ecosystem | Read-only by |
|---|---|---|
| `depcache-npm` (verdaccio 7.0.2) | npm | `publish`/`unpublish: $nobody` on every package pattern, `max_users: -1`, web UI off |
| `depcache-pypi` (devpi-server 6.21, built from `compose/local/depcache/devpi`) | PyPI | only the `root/pypi` mirror; `--restrict-modify root`; root password random and never stored |
| `depcache-go` (athens 0.20) | Go | a module proxy has no upload API |
| `depcache-maven` (reposilite 3.6.6) | Maven | one proxied repository, `redeployment: false`, no tokens (phase B) |

`sandbox_config(dependency_cache=..., network_policy=...)` returns what WP-08's sandbox applies:

* registry env (`npm_config_registry`, `PIP_INDEX_URL`/`UV_DEFAULT_INDEX`, `GOPROXY` + `GOFLAGS=-mod=mod`) and
  a Maven `settings.xml` at `/etc/llmlab/maven-settings.xml` (`MAVEN_ARGS=--settings ...`) for the caches
  that answer;
* every package-manager cache path (`npm_config_cache`, `PIP_CACHE_DIR`, `UV_CACHE_DIR`, `GOMODCACHE`,
  `GOCACHE`, the Maven local repository, ...) under `/tmp/llmlab-deps`, inside the container's own
  writable layer, so it dies with the container;
* `volumes == ()` always (constructing one with a volume raises): **no writable shared volume is mounted
  into any agent container**;
* `proxy_hosts` for the gateway egress allowlist;
* the note `depcache_unavailable` (plus `unavailable`) when the policy wants caches and some are not
  deployed (`DEPCACHE_*_URL` unset) or do not answer the probe: those ecosystems use the public registries.

Cache isolation (invariant 13.4) is tested twice: with `FakeSandbox` (run A writes into every cache path,
run B finds nothing) and on Docker (`LLMLAB_DOCKER_TESTS=1`: two containers with the real config, and a real
verdaccio refusing `npm publish`).

Deployment: `docker compose -f docker-compose.local.yml --profile depcache up -d`; set
`DEPCACHE_NPM_URL=http://172.30.98.2:4873`, `DEPCACHE_PYPI_URL=http://172.30.98.3:3141`,
`DEPCACHE_GOPROXY_URL=http://172.30.98.4:3000`, `DEPCACHE_MAVEN_URL=http://172.30.98.5:8080/maven-central`
for the executor. apps-dind must join the `depcache` network and let the nested gateway egress network reach
those addresses (see the CHANGES ask to the apps-dind owner); until then the caches are reported unavailable.

## Settings (`config/settings/components/generation.py`, WP-05 block)

`SAMPLE_BUILD_ALLOWED_BASES` (allowlist path; empty = default), `DEPCACHE_{NPM,PYPI,GOPROXY,MAVEN}_URL`,
`DEPCACHE_PROBE_TIMEOUT_S` (default 2, 0 = no probe), `STARTERS_DOCKER_HOST` (empty = apps daemon).

## Tests

`backend/generation/tests/wp05_build/`; `./.wt-test.sh wp05 -- pytest -q backend/generation/tests/wp05_build`.
Docker-marked tests (`LLMLAB_DOCKER_TESTS=1`, daemon from `STARTERS_DOCKER_HOST`): every minimal starter
builds and answers `/api/health` and serves `/` (and a client-side route for SPA stacks); cache isolation
across containers; verdaccio refuses publish. The starter test runs each app on its own `--internal`
network `v3wt-starter-<slug>` with a probe container (the slim runtime images have no HTTP client) and, for
postgres profiles, the pinned sidecar; `LLMLAB_STARTER_ONLY=<slug>,...` limits it to some starters.

Wave-3 run (2026-11-03, host daemon, one starter per invocation under the shared build lock): all 11 phase-B
minimal starters built and answered `/api/health`, `/` and (SPA) a client route: go-none, go-react, go-vue,
go-svelte (≈140 MB images), spring-boot-none/-react/-vue/-svelte (≈470 MB), fastapi-, django- and
express-react-postgres (with the postgres sidecar; Django runs `migrate` against it). The bare Go (with and
without `go.mod`/`go.sum`) and Spring Boot Dockerfiles were also built from model-like trees and answered
`/api/health`.

## Decisions

1. Starter slugs are `<stack>-<level>`; profiles without a frontend are `<backend>-none`.
2. Frontends are TypeScript; Express runs `.ts` directly on Node.js 24 (type stripping), so the minimal
   manifest is `express` only.
3. Python locks are hashed constraints files (`requirements.lock`) next to an `==`-pinned `requirements.txt`,
   so a model can still add a dependency to `requirements.txt` without breaking `pip`'s hash mode.
4. The free-choice profile's `brief_fragment` is `run-contract-free@1`; there is no separate
   `stack-free-choice` fragment. The free-choice starter is a README only (no Dockerfile, `base_images: []`,
   `dockerfile_template: "none"`).
5. The allowlist accepts an unpinned `image:tag` (the brief lists tags, not digests) and records the digest
   it is expected to resolve to; a pin to another digest is a violation.
6. Lock indices are keyed by fragment, not by starter: one lock serves every profile sharing the fragment.
7. The depcache services are opt-in (compose profile), on an internal `depcache` network with fixed
   addresses; cache volumes belong to the cache containers only.
8. Phase B (wave 3): the Go framework slug is `go-stdlib` (the contract's literal), fragments
   `backend/go-stdlib/`, template `go-stdlib.Dockerfile.jinja2`. Spring Boot is 4.1.2 (current GA; Java 21
   is supported) with `spring-boot-starter-webmvc` (the Boot 4 name of the web starter).
9. `-none` profiles of go and spring-boot serve a static placeholder page in `minimal` (`html/template` for
   go, `static/index.html` for Spring) and ship no template-engine dependency, as express-none does; the brief
   names the server-rendering approach (Thymeleaf), the model adds it.
10. Go 1.25 (spec 11.4) is no longer a supported Go release (1.27 is current): `go mod tidy` in a bare
    starter resolves the newest module versions, some of which already need Go 1.26 (e.g.
    `modernc.org/sqlite` ≥ v1.60), and fails with `GOTOOLCHAIN=local`. The minimal starter pins a compatible
    driver; whether to move the go profiles to Go 1.27 is an open decision (CHANGES).

