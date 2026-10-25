# Local-endpoint overlay on the frozen llm-gateway image (see local.py). The
# base tag is a build arg, so this image's content-addressed tag pins it.
ARG GATEWAY_BASE
FROM ${GATEWAY_BASE}
COPY local.py /opt/gateway/local.py
CMD ["python", "-u", "/opt/gateway/local.py"]
