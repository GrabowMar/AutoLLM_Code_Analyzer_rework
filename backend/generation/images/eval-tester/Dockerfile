# eval-tester v1 (spec 8.1): runs a compiled AcceptancePlan against a started sample. Standard library only.
# The suite (backend/generation/evaluation/runtime.py) copies /in/plan.json in, runs the phases
# (`--phase pre`, app restart, `--phase post`) and reads /out/result.json. Secrets come from the environment
# (ADMIN_PASSWORD, WEBHOOK_SECRET). The container runs on the internal eval network (alias mock.internal for
# the ssrf_mock server), all capabilities dropped, as an unprivileged user.
# The pinned digest of the built image goes into data/eval_suites/eval-v2.yaml (tester.image_digest).
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

RUN useradd --uid 10001 --create-home tester \
    && mkdir -p /in /out /opt/tester \
    && chown tester:tester /in /out
COPY tester.py /opt/tester/tester.py
USER tester
WORKDIR /opt/tester
ENTRYPOINT ["python", "/opt/tester/tester.py"]
CMD ["--plan", "/in/plan.json", "--out", "/out/result.json", "--phase", "all"]
