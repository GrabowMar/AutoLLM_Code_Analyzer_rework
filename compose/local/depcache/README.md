# Dependency caches (`depcache`)

Read-only pull-through caches for agent sandboxes (spec `docs/design/SAMPLE_GEN_V3.md` section 4.2,
`docs/STACKS.md`). They are started with the `depcache` compose profile:

    docker compose -f docker-compose.local.yml --profile depcache up -d

| Service | Image | Port | Read-only by |
|---|---|---|---|
| `depcache-npm` | verdaccio 7 | 4873 | `publish: $nobody`, `unpublish: $nobody`, `max_users: -1` (`verdaccio/config.yaml`) |
| `depcache-pypi` | devpi-server (built here) | 3141 | only the `root/pypi` mirror index; `--restrict-modify root` with a random root password nobody knows |
| `depcache-go` | athens | 3000 | a module proxy has no upload API |
| `depcache-maven` | reposilite 3 | 8080 | one proxied repository and no access tokens, so nothing can be deployed (phase B: spring-boot) |

They never change a resolved version (locks decide, spec 6.10). Agent containers reach them over the
`depcache` network through the gateway's egress proxy; **no cache volume is ever mounted into an agent
container**. Point the backend at them with `DEPCACHE_{NPM,PYPI,GOPROXY,MAVEN}_URL` (the addresses on the
`depcache` network); unset URLs make runs fall back to the public registries with the note
`depcache_unavailable`.
