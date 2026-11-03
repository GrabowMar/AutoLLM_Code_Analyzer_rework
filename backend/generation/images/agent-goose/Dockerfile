# Goose harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
# The release tarball is sha256-verified (digest from the GitHub release asset); the binary links
# only glibc, libstdc++ and libgcc_s, which the base already has (`ldd` fails the build otherwise).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG GOOSE_VERSION=1.57.0
ARG GOOSE_SHA256=e54036f3b7f95d3b0d7854c09465af5ef52a28c7fa03d2497d0ea68a6382282d
RUN curl -fsSLo /tmp/goose.tar.bz2 \
        "https://github.com/aaif-goose/goose/releases/download/v${GOOSE_VERSION}/goose-x86_64-unknown-linux-gnu.tar.bz2" \
    && echo "${GOOSE_SHA256}  /tmp/goose.tar.bz2" | sha256sum -c - \
    && tar -xjf /tmp/goose.tar.bz2 -C /usr/local/bin ./goose \
    && rm /tmp/goose.tar.bz2 \
    && chmod 0755 /usr/local/bin/goose \
    && ! ldd /usr/local/bin/goose | grep -q 'not found' \
    && GOOSE_DISABLE_KEYRING=1 HOME=/tmp/goose-home goose --version \
    && rm -rf /tmp/goose-home
USER node
