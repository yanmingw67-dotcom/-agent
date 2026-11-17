FROM ghcr.io/astral-sh/uv:0.7.12-python3.12-bookworm-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=app:app app ./app
COPY --chown=app:app alembic.ini ./
RUN mkdir -p /data/archives /data/ingestion /data/sources && chown -R app:app /data
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
