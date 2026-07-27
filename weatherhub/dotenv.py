"""Minimal, dependency-free .env file loader.

Reads simple KEY=VALUE lines into os.environ. Existing environment variables
always win over values from the file (so `docker run -e ...` or real env
vars can still override a .env file), matching common .env-loader convention.
"""

from __future__ import annotations

import os


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from path into os.environ, if the file exists.

    Blank lines and lines starting with '#' are ignored. Values may be
    optionally wrapped in single or double quotes, which are stripped.
    Does nothing if the file doesn't exist.
    """
    if not os.path.isfile(path):
        return

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            os.environ.setdefault(key, value)
