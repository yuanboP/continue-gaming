FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
COPY private/ /tests/
ENV PYTHONPATH=/tests
WORKDIR /app
RUN mkdir -p /app/output /logs/verifier && chmod 700 /tests/test.sh
