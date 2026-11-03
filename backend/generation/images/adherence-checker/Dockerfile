# adherence-checker v1 (spec 8.7): stack, styling and auth adherence of a sample tree, plus a syft SBOM.
# The suite copies the tree to /tree and /in/expect.json in and reads /out/adherence.json (and /out/sbom.json).
# The checker only reads the tree; it never executes it. No network at run time.
# The pinned digest of the built image goes into data/eval_suites/eval-v2.yaml (adherence_checker.image_digest).
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

ARG SYFT_VERSION=1.18.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/syft.tgz "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/syft.tgz -C /usr/local/bin syft \
    && rm /tmp/syft.tgz \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home checker \
    && mkdir -p /in /out /tree /opt/checker \
    && chown checker:checker /in /out
COPY checker.py /opt/checker/checker.py
USER checker
ENTRYPOINT ["python", "/opt/checker/checker.py"]
CMD ["--tree", "/tree", "--expect", "/in/expect.json", "--out", "/out/adherence.json", "--sbom", "/out/sbom.json"]
