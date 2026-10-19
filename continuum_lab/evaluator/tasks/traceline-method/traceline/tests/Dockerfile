FROM python:3.13-slim-bookworm
WORKDIR /app
COPY private/ /tests/
RUN chmod -R go-rwx /tests && chmod 700 /tests/test.sh \
    && mkdir -p /app/output /logs/verifier
