# Sample app builds

Evaluation and previews share immutable Docker images. The first build prepares
the job's pinned stack, applies its selected healing mode, and builds an image.
An unchanged sample from the same owner can reuse that image for seven days.
Every evaluation and new preview still starts a fresh container: database files,
administrator passwords and conformance-test mutations are never cached.

## Build and refresh

The normal **Build** and **Re-evaluate** actions reuse a valid image. A cached
preview starts immediately without waiting for an evaluation slot. Cache misses
are persisted as build actions and admitted by the executor alongside evaluations,
with evaluations taking priority. The normal **Start** action still resumes an
existing stopped container, including its existing data.

**Refresh dependencies** replaces a preview container and rebuilds its image.
**Re-evaluate with refreshed dependencies** refreshes the image used for a new
evaluation without changing an already-running preview. Both refresh base images
and rerun install steps; package-download caches remain available. Exact pins and
lockfiles can still select the same dependency versions.

The API exposes this behavior through optional query parameters:

```text
POST /api/runtime/jobs/<job-id>/build/?force_rebuild=true
POST /api/generation/jobs/<job-id>/evaluate/?force_rebuild=true
```

The default is `false`. Reusing an image also reuses the package versions installed
when that image was built. A refresh creates a new immutable generation; running
or stopped previews keep their original image until replaced.

## Builder and memory

Sample builds use an explicit Docker Buildx `docker-container` builder on the
same apps daemon as the Docker SDK. Local deployments keep that builder inside
apps-dind, using its existing mutual TLS and firewall boundary. Production uses
the configured host daemon. No additional network or Docker socket is exposed
to generated apps.

The builder retains its cache in a named Docker volume and stops after each build
to release idle RAM. Bootstrap starts it again on the next miss. Builds are
serialized across processes and have one parallel build step, a 1536 MiB hard
memory cap and a two-CPU cap by default. Preview cache misses share evaluation
capacity, so the default 4 GiB apps-dind configuration still alternates builds
and agent sandboxes. Build waits observe executor lease cancellation.

New builtin stack versions cache npm/pip downloads, set source ownership during
copying, and leave frontend source out of the Python runtime image. Package caches
are namespaced by owner and kept separate from infrastructure image builds.
Existing pinned stack versions and the generated user-stack Dockerfile renderer
retain their definitions.

## Configuration and retention

Set these in the Django/executor environment, normally `.envs/.local/.django`
or `.envs/.production/.django`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `SAMPLE_BUILD_BACKEND` | `buildkit` | Explicit Buildx backend; `legacy` is a compatibility option for older recipes |
| `SAMPLE_BUILDER_NAME` | `llmlab-samples` | Stable builder name and cache-volume identity |
| `SAMPLE_BUILD_MEMORY` | `1536m` | Builder memory and swap cap; included in evaluation-slot memory accounting |
| `SAMPLE_BUILD_CPUS` | `2` | Builder CFS CPU quota |
| `SAMPLE_BUILD_TIMEOUT_S` | `1200` | Maximum build duration |
| `SAMPLE_BUILD_CACHE_BYTES` | `5368709120` | BuildKit cache GC budget |
| `SAMPLE_IMAGE_CACHE_BYTES` | `5368709120` | Retained sample-image budget |
| `SAMPLE_IMAGE_CACHE_TTL_S` | `604800` | Reuse period from successful build, in seconds |

The executor sweeps images hourly while build workers are idle, first removing
expired unused generations, then evicting least recently used ones to meet the
image budget. References from running/stopped containers and startup reservations
take precedence over the budget. Docker layers can be shared, so logical image
sizes are conservative and do not equal physical reclaimed disk space. Images
left behind by interrupted exports are also eventually collected. Blanket
apps-dind image pruning excludes managed sample artifacts.

BuildKit's own volume has a separate cache budget. Registry cache export is not
required for reuse on this single persistent daemon. Removing Docker volumes
(`just prune`) removes those caches and makes subsequent builds cold.

Builder driver limits and GC configuration apply when the builder is created.
To change them, drain the executor, wait for active builds to finish, then remove
the builder using `docker buildx rm --keep-state llmlab-samples` against the apps
daemon. The next build recreates it using current settings and its retained
volume. Renaming the builder instead starts a separate cache.

## Upgrade and verification

Rebuild the Django/executor images to include Docker CLI and Buildx, apply database
migrations, and restart the executor. Migrations seed new builtin stack versions;
existing job snapshots keep their old versions. Recreate apps-dind while idle to
pick up the sample-image prune exclusion. Its persistent volumes must be retained.

The backend selection is configurable for staged rollout. New builtin recipes
require BuildKit, so `legacy` rollback also requires selecting an older pinned
stack version. The legacy API supports memory limits and relative CPU shares;
only BuildKit provides the hard aggregate CPU cap. Evaluator version 3 records
the new build procedure alongside image identities and resolved packages.

Preview metadata and evaluation build results record cache reuse, preparation,
queue/build/startup time, image size, base-image identities, and best-effort CPU
time and sampled peak builder memory. A cache hit has zero build seconds and no
new builder resource measurement. Compare cold builds, source-only edits,
dependency changes and cache hits separately; package download caches and image
reuse measure different kinds of work.

Run targeted regression tests with:

```bash
just test backend/runtime/tests backend/execution/tests backend/generation/tests/test_conformance.py
```

Use the opt-in `benchmark_sample_builds` management command with representative
job IDs to collect repeated measurements without changing generation outcomes.
Run it while the executor is drained: it builds images directly and consumes
the same sandbox resources as evaluation.

```bash
python manage.py benchmark_sample_builds <job-uuid> [<another-job-uuid>] \
  --repeats 3 --output /tmp/sample-build-benchmark.json
```

The jobs must be completed. Omit `--output` to write JSON to stdout. Each
repetition measures forced refresh, unchanged-image reuse, backend/frontend
source edits, and a requirements-manifest edit. Inputs are unsaved copies and
comments preserve application behavior and the dependency graph. The manifest
edit tests install-layer invalidation and package-cache reuse; it does not measure
adding a package. Missing input types are reported as skipped, and build failures
remain explicit in the report. No app containers, startup checks or conformance
tests are run. A forced refresh is not a true cold build: existing package-download
caches may still be used. Image generations remain subject to normal cache GC.
