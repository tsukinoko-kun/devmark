#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if command -v uv >/dev/null 2>&1; then
    if [ ! -x .venv/bin/python ]; then
        uv venv --python '>=3.11' --no-python-downloads .venv
    fi
    uv pip install --python .venv/bin/python --quiet -r requirements.txt
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3.11 or newer is required." >&2
        exit 1
    fi
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
    fi
    if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
        .venv/bin/python -m ensurepip --upgrade
    fi
    .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements.txt
fi
exec .venv/bin/python -m devmark "$@"
