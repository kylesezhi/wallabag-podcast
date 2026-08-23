FROM python:3.12-slim

# Install uv from the official image so no build toolchain is needed.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first so the layer caches unless pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code.
COPY app/ app/
COPY templates/ templates/
COPY static/ static/

# Runtime data directory (mounted as a volume in docker-compose.yml).
RUN mkdir -p data/audio

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
