# Reference: file_share_multipart on spring-boot-none

- Purpose: tester conformance kit reference (spec 8.5); contract_bearer auth.
- Source: a port of the WP-09 Flask reference to the `spring-boot-none-minimal` starter (Dockerfile, locks and run contract unchanged; `application.properties` adds the 10 MiB multipart limit).
- Author: Claude Code (model claude-opus-5-5) as the dev1 E2E workflow agent, 2026-11-04 (CHANGES E2E-25). An AI
  coding assistant wrote it; no generation run and no model under study was involved. Not yet reviewed by a person.
- Verified: `conformance_kit` (run_kit) and `oracle_validate --runs 3` on the dev1 docker runtime with eval-v2
  (agent_pass_rate 1.0 in every run, every probe not_exploited).
