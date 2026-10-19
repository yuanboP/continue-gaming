FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bash curl git nodejs npm ripgrep ca-certificates fonts-dejavu-core \
    && npm install -g @openai/codex@0.157.0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow==11.3.0
WORKDIR /app
COPY public/ /app/
RUN mkdir -p /app/output
