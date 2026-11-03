# Shared sandbox environment for every agent harness (spec section 5.3, "common rules").
# Harness images build FROM this exact image, so the tools an agent finds (Python, Node, git,
# ripgrep, ...) are byte-identical across harnesses; only the harness layer differs.
# The base release is pinned exactly: a floating tag would change the environment under an
# unchanged content hash. (Debian packages still resolve at build time; the built image digest is
# recorded per run in the manifest.)
# Runtimes match the builtin stacks' Dockerfiles: Node 24 here, and trixie's Debian python3 is 3.13.
# Phase-B toolchains (spec 11.4, WP-05 ask): the agent can run `go mod tidy` / `go build` and `mvn package`
# with the same releases the go-stdlib and spring-boot builds use (digests from
# data/starters/base_images.yaml; keep them in step). Copied as whole trees, so the image needs no extra
# package source; the JDK and Maven are glibc builds that run on trixie.
FROM golang:1.25-trixie@sha256:2c4c60ef415fbfa5e90300722293bef36c5e63fae17570ce18f580af933dbd73 AS go
FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS maven

FROM node:24.22.1-trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git ripgrep python3 python3-venv python3-pip curl procps bzip2 \
    && rm -rf /var/lib/apt/lists/*

# v3 layout (harnesses/base.py): /workspace (the app), /logs (gateway + harness evidence),
# /run (brief, rendered harness files) and /run/home, the fresh HOME every harness gets, so no
# user config, cached login or project context file is ever found outside the rendered files.
RUN mkdir -p /workspace /logs /run/home /opt/harness \
    && chown node:node /workspace /logs /run/home

COPY --from=go /usr/local/go /usr/local/go
COPY --from=maven /opt/java/openjdk /opt/java/openjdk
COPY --from=maven /usr/share/maven /usr/share/maven

# Debian marks its Python as externally managed; inside a throwaway sandbox plain
# `pip install` (to ~/.local) should just work, as on a dev machine. GOTOOLCHAIN=local: Go never
# downloads another toolchain (a go.mod asking for a newer one fails visibly instead).
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GOTOOLCHAIN=local \
    GOPATH=/run/home/go \
    JAVA_HOME=/opt/java/openjdk \
    MAVEN_HOME=/usr/share/maven \
    PATH=/run/home/.local/bin:/run/home/go/bin:/usr/local/go/bin:/opt/java/openjdk/bin:/usr/share/maven/bin:$PATH \
    npm_config_update_notifier=false \
    npm_config_fund=false \
    HOME=/run/home

USER node
WORKDIR /workspace
