# Sample generation v3: cut-over runbook (dev1)

Owner: WP-00 (`SAMPLE_GEN_V3.md` §3.12). Every step below needs the user's go-ahead. Nothing here runs automatically.

The order is **drain → back up → wipe (dry run, then `--yes`) → deploy v3 → migrate → seed_library → restart the services**. The wipe has to run on the **old (v2) code and schema**. `compose/local/django/start` runs `migrate` under `set -o errexit`, and the v3 guard migration (`generation 0023_v3_wipe_guard`) raises while any v2 generation table still has rows. So a populated v2 database would stop the v3 web process from starting. `wipe_generation_v2` uses raw SQL only, so the same file also works on the v3 code as long as the schema is still v2 (see [Recovery](#recovery)).

## The dev1 stack

| What | Where |
|---|---|
| Live checkout (bind-mounted at `/app`) | `/home/ubuntu/code/AutoLLM_Code_Analyzer_rework` |
| Web (devcontainer; uvicorn `--reload` started by `.devcontainer/post-attach.sh`) | `llmlab-django-1` |
| Executor (runs every generation job, no autoreload, `restart: unless-stopped`) | `llmlab-executor-1` |
| Celery, flower | `llmlab-celeryworker-1`, `llmlab-celerybeat-1`, `llmlab-flower-1` |
| Database | `llmlab-postgres-1`, database `backend` |
| Sample apps, sandboxes, gateways | `llmlab-apps-dind-1` (nested containers) |
| Run files (`GENERATION_RUNS_ROOT`) | `/app/backend/media/generation_runs` in the containers |

Notes:

- `docker exec` skips the image entrypoint, and the entrypoint is what builds `DATABASE_URL`. So every `manage.py` call needs the `/entrypoint` prefix.
- `python` on the containers' `PATH` is the image venv `/app/.venv`. The web process uses the devcontainer venv `/home/dev-user/.venv`. After the v3 deploy, call that interpreter explicitly. The image venv only gets the v3 dependencies when the image is rebuilt.
- Compose, from the live checkout. Any other file set recreates containers:

```bash
cd /home/ubuntu/code/AutoLLM_Code_Analyzer_rework
DC="docker compose -p llmlab -f docker-compose.local.yml -f .devcontainer/docker-compose.devcontainer.yml -f .devcontainer/docker-compose.host.yml"
```

## 0. Preconditions

- The user has merged `chore/wipe-generation-v2` into `main`. The live checkout is on that `main` and has the command:

  ```bash
  docker exec llmlab-django-1 /entrypoint python manage.py help wipe_generation_v2
  ```

- `feat/sample-gen-redesign` has passed its gates (U1 and wave 3) and is ready to merge.
- No other session is working in the live checkout or on apps-dind. Other sessions' WIP is committed (G0), or its owner has parked it.
- Plan a maintenance window. From step 5 until step 7 the web UI returns errors.

## 1. Drain the executor

```bash
docker exec llmlab-executor-1 /entrypoint python manage.py executor drain     # stop claiming jobs, post-runs and schedules
docker exec llmlab-executor-1 /entrypoint python manage.py executor status    # repeat until "safe_to_restart": true
```

- Pause running experiments in the UI (Experiments → Pause), or cancel the ones you do not want to finish. Draining only stops new claims. Running agent jobs continue until they finish or reach their wall-clock limit (up to 1 h). Cancelling is quicker: the v2 results are wiped either way.
- To wait without retyping the command:

  ```bash
  until docker exec llmlab-executor-1 /entrypoint python manage.py executor status | grep -q '"safe_to_restart": true'; do sleep 30; done
  ```

- Check for leftover sandboxes. The command below should print nothing. Otherwise use System → Maintenance → purge orphans, or `docker exec llmlab-apps-dind-1 docker rm -f <name>`:

  ```bash
  docker exec llmlab-apps-dind-1 docker ps -a --format '{{.Names}}' | grep -E '^llmlab-(agent|gateway)-'
  ```

- Stop everything else that runs v2 code against the database. The web process stays up for the wipe:

  ```bash
  docker stop llmlab-executor-1 llmlab-celerybeat-1 llmlab-celeryworker-1
  ```

## 2. Back up

The wipe cannot be undone. Take a database dump and an archive of the run files and human-evaluation uploads:

```bash
mkdir -p ~/backups && TS=$(date +%Y%m%d%H%M)
docker exec llmlab-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -Fc backend' > ~/backups/llmlab-backend-pre-v3-$TS.dump
docker exec llmlab-django-1 tar -C /app/backend/media --ignore-failed-read -czf - generation_runs human_eval \
  > ~/backups/llmlab-media-pre-v3-$TS.tgz
ls -lh ~/backups/*pre-v3-$TS*
```

## 3. Wipe: dry run

```bash
docker exec llmlab-django-1 /entrypoint python manage.py wipe_generation_v2
```

The dry run changes nothing. Check its output:

- **Per-table counts.** `generation_generationjob` should match the number of jobs in the UI. All of `generation_contentblock`, `generation_generationprofile` and `generation_runpreset` go, system rows included. In `generation_apprequirementtemplate`, only the non-system rows (`is_default = false`) go.
- **Rows of other apps:**
  - analysis runs of jobs, with their tool results and findings;
  - reports of jobs, of those analysis runs, and the experiment reports of v2 post-runs;
  - runtime containers of jobs, with their actions, port allocations and (BuildKit WIP) build leases;
  - **whole human-evaluation tasks** that have an experiment link or any job-linked app, with everything in them (apps, items, invites, participants, assignments, responses, observations, attachments, verdicts, HTTP exchanges).
- **What is kept:**
  - `inference_llmrequest` rows, with `job_id` set to NULL;
  - analysis runs, reports and containers that are not tied to a job;
  - human-evaluation tasks that are not tied to v2 data. Their references to wiped rows are set to NULL: `container_id`, and the `finding_id` of finding verdicts, which keep their `finding_key` and snapshot.
- **Containers** that `--yes` will stop, and the number of **run-file entries** and attachment files it will delete.

If the command refuses with "These foreign keys point into tables the wipe deletes from …", the live schema has a table the plan does not know about. Stop and add a step to `wipe_generation_v2.STEPS` (WP-00). Do not delete the rows by hand.

## 4. Wipe: `--yes`

```bash
docker exec llmlab-django-1 /entrypoint python manage.py wipe_generation_v2 --yes
```

- Run it as root, which is the default `docker exec` user and the user the executor wrote the run files as. That way the post-commit deletion of `GENERATION_RUNS_ROOT/*` is allowed. If a file cannot be deleted, the command lists it on stderr, but the database part is already committed. Remove such files by hand.
- The command stops the containers before the transaction, best effort. A failure (docker unreachable, container already gone) is listed and never aborts the wipe. Pass `--skip-containers` to leave docker alone. The wipe only *stops* containers, so you can then remove the ones it listed: `docker exec llmlab-apps-dind-1 docker rm -f <name> ...`. Do not run `container prune`: apps-dind also holds containers that do not belong to jobs.
- The deletes run in one transaction, with constraints checked statement by statement. Any error rolls the whole wipe back.
- Verify. The dry run must now print `Total: 0 rows to delete, 0 references set NULL.`:

  ```bash
  docker exec llmlab-django-1 /entrypoint python manage.py wipe_generation_v2 | tail -3
  ```

- **From here until step 5, do not restart the web process on the v2 code, and do not run `migrate` or `/start` on it.** The v2 `post_migrate` seeder would recreate the system content blocks, profiles and presets, and the v3 guard would then refuse to migrate. If that happens, run step 4 again.

## 5. Deploy v3

1. The user merges `feat/sample-gen-redesign` into `main`.
2. Update the live checkout. uvicorn reloads into the v3 code at once and returns errors until step 6 is done. This is expected.

   ```bash
   cd /home/ubuntu/code/AutoLLM_Code_Analyzer_rework
   git status --short          # must be clean
   git switch main && git pull --ff-only
   ```

3. Install the v3 Python dependencies into the web venv:

   ```bash
   docker exec -u dev-user -w /app llmlab-django-1 uv sync --frozen
   ```

4. Rebuild the image used by the executor and celery. They run from the image venv, which is an anonymous volume, so they must later be recreated with `--renew-anon-volumes` (step 7):

   ```bash
   $DC build executor
   ```

5. Frontend: if `frontend/package-lock.json` changed, run `docker exec llmlab-frontend-1 npm ci`. Then `docker restart llmlab-frontend-1`. A stuck Vite (HTTP 504) also needs this restart.

## 6. Migrate and seed

```bash
PY=/home/dev-user/.venv/bin/python
docker exec -u dev-user -w /app llmlab-django-1 /entrypoint $PY manage.py migrate
docker exec -u dev-user -w /app llmlab-django-1 /entrypoint $PY manage.py showmigrations generation runtime analysis | tail -20
docker exec -u dev-user -w /app llmlab-django-1 /entrypoint $PY manage.py seed_library
docker exec -u dev-user -w /app llmlab-django-1 /entrypoint $PY manage.py makemigrations --check --dry-run
```

On the wiped database the guard (`0023_v3_wipe_guard`) is a no-op. In dev, `seed_library` also runs on `post_migrate`. Running it explicitly as well is idempotent and prints what it seeded.

## 7. Restart the services and verify

```bash
# web: restart the devcontainer's /start (migrate is now a no-op) so it loads the synced venv
docker exec -u dev-user llmlab-django-1 bash -c 'kill "$(cat /tmp/django-start.pid)"' || true
docker exec -u dev-user -w /app -e PATH=/home/dev-user/.venv/bin:/usr/local/bin:/usr/bin:/bin llmlab-django-1 \
  bash .devcontainer/post-attach.sh

# executor and celery on the rebuilt image (fresh anonymous venv volume)
$DC up -d --no-deps --renew-anon-volumes executor celeryworker celerybeat flower
docker exec llmlab-executor-1 /entrypoint python manage.py executor resume
docker exec llmlab-executor-1 /entrypoint python manage.py executor status   # "online": true, "draining": false
```

Then check:

- `docker exec llmlab-django-1 /entrypoint /home/dev-user/.venv/bin/python manage.py wipe_generation_v2` prints `No v2 generation tables in schema 'public' (generation_generationjob is absent): nothing to wipe.`, so the schema is v3.
- The UI loads, the Library lists the seeded tasks, stacks and presets, and a Study dry run renders its stimuli.
- Run the wave-3 pilots (§12.1) before any real study.

## Recovery

- **`migrate` fails in `0023_v3_wipe_guard` ("generation v2 data present").** Rows came back after the wipe, for example because v2 was restarted. The guard runs before every other generation v3 migration, so the generation schema is still v2. The command file is identical on v3, so wipe again with the v3 code, then run step 6 again:

  ```bash
  docker exec llmlab-django-1 /entrypoint /home/dev-user/.venv/bin/python manage.py wipe_generation_v2
  docker exec llmlab-django-1 /entrypoint /home/dev-user/.venv/bin/python manage.py wipe_generation_v2 --yes
  ```

- **Full rollback to v2.** Stop the executor, celery and the web process. Then restore the dump and the run files, and check out the pre-v3 `main` commit:

  ```bash
  docker stop llmlab-executor-1 llmlab-celerybeat-1 llmlab-celeryworker-1
  docker exec -u dev-user llmlab-django-1 bash -c 'kill "$(cat /tmp/django-start.pid)"' || true
  docker exec llmlab-postgres-1 sh -c 'dropdb --force -U "$POSTGRES_USER" backend && createdb -U "$POSTGRES_USER" backend'
  docker exec -i llmlab-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -d backend --no-owner' < ~/backups/llmlab-backend-pre-v3-$TS.dump
  docker exec -i llmlab-django-1 tar -C /app/backend/media -xzf - < ~/backups/llmlab-media-pre-v3-$TS.tgz
  git switch --detach <pre-v3 main sha> && docker exec -u dev-user -w /app llmlab-django-1 uv sync --frozen
  ```

  After that, restart the services as in step 7, with the v2 image (`$DC build executor` on the restored checkout).
