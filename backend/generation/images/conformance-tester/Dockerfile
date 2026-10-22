# Hidden conformance suite for generated apps (see tester.py). Runs on an
# internal network next to the app under test; stdlib only.
FROM python:3.12.12-slim-bookworm

COPY tester.py /opt/tester/tester.py
USER nobody
WORKDIR /work
CMD ["python", "-u", "/opt/tester/tester.py", "/work/plan.json"]
