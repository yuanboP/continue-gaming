#!/bin/sh
set -eu
mkdir -p /logs/verifier
exec python3 /tests/verify.py
