#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if command -v uv >/dev/null 2>&1; then
    if ! [ -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11, 8))'; then
        uv venv --clear --python '>=3.11.8' .venv
    fi
    uv pip install --python .venv/bin/python --quiet -r requirements.txt
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3.11.8 or newer is required." >&2
        exit 1
    fi
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11, 8) else "Python 3.11.8 or newer is required.")'
    if ! [ -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11, 8))'; then
        python3 -m venv --clear .venv
    fi
    if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
        .venv/bin/python -m ensurepip --upgrade
    fi
    .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements.txt
fi
exec .venv/bin/python -m devmark "$@"
