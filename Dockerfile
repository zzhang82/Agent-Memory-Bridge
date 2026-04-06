FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODEX_HOME=/tmp/.codex \
    AGENT_MEMORY_BRIDGE_HOME=/tmp/.codex/mem-bridge

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir .

CMD ["python", "-m", "agent_mem_bridge"]
