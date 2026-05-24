FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
RUN adduser --disabled-password --no-create-home appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src/ .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8123
ENTRYPOINT ["./entrypoint.sh"]
