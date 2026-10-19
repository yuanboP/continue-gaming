FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow==11.3.0
WORKDIR /world
COPY public/ /world/
COPY private/ /private/
RUN chmod 755 /private && chmod 444 /private/signing.key
USER 65534:65534
