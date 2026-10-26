# llmlab analyzer image: every static analysis tool, exactly pinned and baked at build time.
#
# Built from this directory and tagged llmlab/analyzer:<sha12 of this directory's content>
# (engine/sandbox.image_tag), so any change here produces a new tag. Nothing is installed at scan
# time and scans run with --network none, a read-only rootfs, uid 10001, /src read-only, cwd /tmp
# (tmpfs) and reports in /out. All versions were released on or before 2026-10-08; see README.md.

# Node tools (ESLint + plugins, jscpd) are installed from the committed lockfile in the official
# Node 22 LTS image; only the node binary and node_modules reach the final image (no npm in it).
# Install scripts are disabled: none of the pinned packages needs one.
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS node
WORKDIR /opt/analyzer
COPY npm/package.json npm/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && rm -rf /root/.npm

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ARG HADOLINT_VERSION=2.15.1
ARG HADOLINT_SHA256=c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONUNBUFFERED=1

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /opt/analyzer/node_modules /opt/analyzer/node_modules
COPY npm/package.json npm/package-lock.json /opt/analyzer/

# Python tools: one venv per tool from its own hash-locked requirement set, wheels only, so tools
# cannot constrain each other's dependencies and mypy/pylint never see another tool's packages.
COPY requirements/ /opt/analyzer/requirements/
RUN set -eux; \
    for req in /opt/analyzer/requirements/*.txt; do \
        tool="$(basename "$req" .txt)"; \
        python -m venv --without-pip "/opt/analyzer/venvs/$tool"; \
        python -m pip --python "/opt/analyzer/venvs/$tool/bin/python" install \
            --no-deps --require-hashes --only-binary=:all: -r "$req"; \
    done

COPY scripts/ /opt/analyzer/scripts/

# Release binaries, verified against the published sha256 before use.
RUN set -eux; \
    python /opt/analyzer/scripts/fetch_binary.py \
        "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
        "$GITLEAKS_SHA256" /opt/analyzer/bin/gitleaks gitleaks; \
    python /opt/analyzer/scripts/fetch_binary.py \
        "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-x86_64" \
        "$HADOLINT_SHA256" /opt/analyzer/bin/hadolint

# semgrep rules: a pinned, content-verified snapshot (no registry access at scan time).
RUN /opt/analyzer/venvs/semgrep/bin/python /opt/analyzer/scripts/build_semgrep_rules.py /opt/analyzer/rules/semgrep

# One stable entry point per tool.
RUN set -eux; \
    cd /opt/analyzer/bin; \
    for tool in ruff bandit semgrep pylint mypy vulture detect-secrets codespell radon lizard; do \
        ln -s "/opt/analyzer/venvs/$tool/bin/$tool" "$tool"; \
    done; \
    ln -s /opt/analyzer/node_modules/.bin/eslint eslint; \
    ln -s /opt/analyzer/node_modules/.bin/jscpd jscpd; \
    ln -s /usr/local/bin/node node

COPY config/ /opt/analyzer/config/

# The scanner user. /out is created owned by it so the (empty) volume mounted there for reports
# inherits a writable mount point; /src is mounted read-only over an empty directory.
RUN groupadd --gid 10001 analyzer \
    && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin analyzer \
    && install -d -o 10001 -g 10001 -m 0755 /out \
    && install -d -m 0755 /src

# Runtime environment: nothing may be written outside /tmp (tmpfs) and /out, and nothing may phone
# home. HOME and every cache point into /tmp.
ENV PATH=/opt/analyzer/bin:/usr/local/bin:/usr/bin:/bin \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    PYTHONDONTWRITEBYTECODE=1 \
    NO_COLOR=1 \
    SEMGREP_SEND_METRICS=off \
    SEMGREP_ENABLE_VERSION_CHECK=0 \
    JSCPD_NO_TIPS=1 \
    NODE_ENV=production

# Last step: run every tool once, check its version against the pins and record the manifest.
RUN python /opt/analyzer/scripts/write_manifest.py "$GITLEAKS_VERSION" "$HADOLINT_VERSION" \
    && rm -rf /tmp/* /tmp/.[!.]*

USER 10001:10001
WORKDIR /tmp

LABEL llmlab.analysis-image="1" \
      org.opencontainers.image.title="llmlab analyzer" \
      org.opencontainers.image.description="Pinned static analysis toolchain for LLM Eval Lab"
