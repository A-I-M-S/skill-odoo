from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path = Path('.env')) -> None:
    """Load KEY=VALUE pairs from a local .env file into process env (without overriding existing values)."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
