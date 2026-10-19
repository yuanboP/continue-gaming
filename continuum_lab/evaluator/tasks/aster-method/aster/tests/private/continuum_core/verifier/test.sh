#!/bin/sh
set -eu
export PYTHONPATH=/opt/bench${PYTHONPATH:+:$PYTHONPATH}
exec python -m continuum_core.verifier.verify "$@"
