# Reference: file_share_multipart on flask-react

- Purpose: task oracle reference (spec 8.5); contract_bearer auth.
- Source: the `flask-react-minimal` starter (Dockerfile, frontend placeholder UI, locks unchanged) with the backend of this task's `flask-none` reference; only the index route changed, to serve the React build (`frontend/dist`) with a client-routing fallback for non-API GET paths. The UI is the starter's placeholder: the reference covers the API only.
- Author: the backend is WP-09's `flask-none` reference (hand-written); the assembly and the index-route change are by
  Claude Code (model claude-opus-5-5) as the dev1 E2E workflow agent, 2026-11-04 (CHANGES E2E-25), an AI coding assistant; no generation run and no model under study was involved. Not yet reviewed by a person.
- Verified: `oracle_validate --runs 3` on the dev1 docker runtime with eval-v2 (agent_pass_rate 1.0 in every run).
