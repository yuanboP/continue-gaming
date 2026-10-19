#!/bin/sh
set -eu
mkdir -p /logs/verifier
exec python -m continuum_core.verifier.verify --engine /tests/engine.mjs
