# Reference: notes_markdown_render on flask-none

- Purpose: task oracle reference (spec 8.5); contract_bearer auth. The Markdown renderer escapes the whole body before adding markup.
- Source: the API and domain logic are new; the adapter skeleton, Dockerfile and locks are the WP-09 team_roles_multitenant reference's.
- Author: Claude Code (model claude-opus-5-5) as the dev1 E2E workflow agent, 2026-11-04 (CHANGES E2E-25). An AI
  coding assistant wrote it; no generation run and no model under study was involved. Not yet reviewed by a person.
- Verified: `oracle_validate --runs 3` on the dev1 docker runtime with eval-v2 (agent_pass_rate 1.0 in every run).
