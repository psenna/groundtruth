# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --all-extras


FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system groundtruth \
 && useradd --system --gid groundtruth --home /home/groundtruth --create-home groundtruth

# Commits carry the dedicated groundtruth identity (spec §7.9).
RUN git config --system user.name  "groundtruth" \
 && git config --system user.email "groundtruth@localhost" \
 && git config --system --add safe.directory '*'

COPY --from=build --chown=groundtruth:groundtruth /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    GT_CONFIG=/etc/groundtruth/config.yaml

# Named volumes mount here (see docker-compose.yml). The state dir is NOT under
# any vault repo (spec §5.1).
RUN mkdir -p /var/lib/groundtruth /data \
 && chown -R groundtruth:groundtruth /var/lib/groundtruth /data

USER groundtruth
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["python", "-m", "groundtruth.main"]
