# Detector fixtures (WP-13)

Not library entities: the seeder skips `detectors/tests/**` (spec 11.6). Each `<slug>/positive*.<ext>` file
must give `present` (file scope) for the seed detector of the same slug, each `<slug>/negative*.<ext>` file
`absent`; the language comes from the extension (`positive-2.js` etc. for more cases).
`tests/wp13_patterns/test_seed_and_invariants.py` runs the regex and ts_query fixtures in-process and the
semgrep ones only when a `semgrep` binary is on the PATH.

Probe detectors' fixtures are `positive.json` / `negative.json` holding `{"probes": [ProbeResult, ...]}` (the
latest evaluation's probe results); the manual detector has none (its input is cluster labels).
