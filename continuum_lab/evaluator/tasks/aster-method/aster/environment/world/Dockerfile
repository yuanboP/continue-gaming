FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
RUN apt-get update && apt-get install -y --no-install-recommends nodejs ca-certificates fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.58.0 pillow==12.1.1 aiohttp==3.13.3 httpx==0.28.1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium --only-shell && rm -rf /var/lib/apt/lists/*
COPY upstream/ /opt/game/
COPY bench/ /opt/bench/
COPY private/ /private/
ENV PYTHONPATH=/opt/bench
WORKDIR /opt/bench
RUN chmod -R a+rX /opt/game /opt/bench /private
USER 65534:65534
CMD ["python", "-m", "continuum_core.world.server"]
