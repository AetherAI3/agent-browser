FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

LABEL org.opencontainers.image.title="Aether Browser" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/AetherAI3/aetherbrowser"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/patchright-browsers \
    DISPLAY=:99

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dumb-init \
        fonts-dejavu-core \
        novnc \
        procps \
        scrot \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN python -m pip install --require-hashes -r requirements.lock \
    && python -m pip install --no-build-isolation --no-deps . \
    && python -m patchright install --with-deps chromium \
    && groupadd --gid 10001 aether \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin aether \
    && chown -R root:root /app /opt/patchright-browsers \
    && chmod -R a-w /app /opt/patchright-browsers \
    && chmod 0755 /app/scripts/container-entrypoint.sh

USER 10001:10001

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/app/scripts/container-entrypoint.sh"]
