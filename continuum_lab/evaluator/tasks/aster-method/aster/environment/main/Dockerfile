FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
RUN apt-get update && apt-get install -y --no-install-recommends bash curl git nodejs npm ripgrep ca-certificates && npm install -g @openai/codex@0.157.0 && rm -rf /var/lib/apt/lists/* /root/.npm
RUN pip install --no-cache-dir pillow==12.1.1 aiohttp==3.13.3 numpy==2.2.6
WORKDIR /app
COPY public/ /app/
RUN mkdir -p /app/output
