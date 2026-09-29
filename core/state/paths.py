from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Private persistent state owned by this plugin (aliases/transcripts)."""
    raw = (os.getenv("HERMES_TG_USER_STATE_DIR") or "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / ".hermes" / "state" / "telegram-user"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def private_file(path: Path) -> Path:
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
