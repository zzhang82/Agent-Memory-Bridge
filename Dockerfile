FROM python:3.12-slim AS wheel-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ARG AMB_VERSION=0.36.1
ARG VCS_REF=unverified

LABEL org.opencontainers.image.title="Agent Memory Bridge" \
    org.opencontainers.image.source="https://github.com/zzhang82/Agent-Memory-Bridge" \
    org.opencontainers.image.version="${AMB_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge

RUN groupadd --gid 10001 amb \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin amb \
    && install -d --owner amb --group amb --mode 0700 /data/agent-memory-bridge

COPY --from=wheel-builder /wheels /wheels

RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && python -c "import importlib.metadata; assert importlib.metadata.version('agent-memory-bridge') == '${AMB_VERSION}'" \
    && rm -rf /wheels

USER amb:amb
WORKDIR /data/agent-memory-bridge

EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).close()"]

CMD ["agent-memory-bridge", "serve", "--transport", "streamable-http"]
