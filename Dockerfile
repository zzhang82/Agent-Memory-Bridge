FROM python:3.12-slim AS wheel-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge

RUN groupadd --gid 10001 amb \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin amb \
    && install -d --owner amb --group amb --mode 0700 /data/agent-memory-bridge

COPY --from=wheel-builder /wheels /wheels

RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

USER amb:amb
WORKDIR /data/agent-memory-bridge

CMD ["python", "-m", "agent_mem_bridge"]
