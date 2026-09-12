# Band Manager – FastAPI + FastMCP + statische PWA in einem Container.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY web ./web
COPY scripts ./scripts

ENV PATH="/opt/venv/bin:$PATH" \
    BANDMANAGER_DB=/data/band-manager.db \
    BANDMANAGER_WEB=/app/web
VOLUME ["/data"]
EXPOSE 8019
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8019/api/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8019", "--proxy-headers", "--forwarded-allow-ips", "*"]
