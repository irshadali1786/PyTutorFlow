#!/usr/bin/env bash
# Create requirements.lock.txt: the exact versions you just tested with.
#   ./scripts/freeze_lock.sh
# Run it inside the project's virtualenv AFTER `python -m pytest -q` passes.
# Restricted to this project's direct dependencies and everything they pulled in.
set -eu
cd "$(dirname "$0")/.."
python -m pytest -q
python -m pip freeze > requirements.lock.txt
echo "Wrote requirements.lock.txt ($(wc -l < requirements.lock.txt) packages). Commit it."
echo "Reproducible install later:  pip install -r requirements.lock.txt"
