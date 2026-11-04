# Colab Studio in a container. See "Docker" in the README:
#   docker compose run --rm studio colab usage   # one-time Google sign-in
#   docker compose up -d
FROM node:22-slim AS web
WORKDIR /web
COPY server/web/package.json server/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY server/web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client curl ca-certificates tar \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_DOWNLOADS=never
RUN uv tool install --python /usr/local/bin/python3.12 google-colab-cli
WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY VERSION ./
COPY station/ station/
COPY server/ server/
COPY --from=web /web/dist server/web/dist
# Inside the container the studio listens on all interfaces; docker-compose.yml decides which host
# address the port is published on (127.0.0.1 by default).
ENV STUDIO_HOST=0.0.0.0 STUDIO_PORT=8190 STUDIO_DATA=/data STUDIO_IN_DOCKER=1
VOLUME ["/data", "/root/.config/colab-cli"]
EXPOSE 8190
WORKDIR /app/server
CMD ["python", "app.py"]
