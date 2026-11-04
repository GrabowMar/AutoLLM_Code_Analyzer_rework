# Reference: team_roles_multitenant on django-none

- Purpose: tester conformance kit reference (spec 8.5); contract_bearer auth.
- Author: WP-09 (wave 3, 2026-11-03), hand-written on the `django-none-minimal` starter (Dockerfile, locks and
  run contract unchanged except where noted in the app's docstring).
- Verified: `conformance_kit` and `oracle_validate --runs 3` on the docker runtime with eval-v2 (agent_pass_rate
  1.0, every probe not_exploited).
- No LLM was involved.
