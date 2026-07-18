FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# uv for fast, locked installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Non-root runtime. The plugin runtime pip-installs into /app/.venv and writes folder plugins into
# /app/.system/plugins at runtime, so the `app` user must own both. A fresh named volume mounted at
# .system/plugins inherits this directory's ownership when Docker first populates it, so the non-root
# process can write there too.
RUN useradd --system --uid 10001 --create-home app \
    && mkdir -p /app/.system/plugins \
    && chown -R app:app /app
USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
