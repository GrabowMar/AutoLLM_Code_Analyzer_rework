# LLM Lab relay, built once on the GPU box from the sha256-verified script.
# The image never fetches code at runtime; rebuild it to upgrade.
FROM python:3.12-alpine@sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d

COPY llmlab_relay.py /relay.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LLMLAB_STATE_DIR=/state

USER 65534
ENTRYPOINT ["python", "/relay.py"]
