FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge

WORKDIR /app

COPY . /app

RUN mkdir -p /data/agent-memory-bridge \
    && pip install --no-cache-dir .

CMD ["python", "-m", "agent_mem_bridge"]
