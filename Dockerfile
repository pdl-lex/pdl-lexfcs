FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy project definition and sync dependencies
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY *.py .

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
