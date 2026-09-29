from __future__ import annotations

import os
from pathlib import Path

#: The name Hermes knows this plugin by; also the directory under plugin-data/.
PLUGIN_NAME = "telegram-user"


def _plugin_home() -> Path | None:
    """``<hermes home>/plugin-data/telegram-user`` when Hermes provides it.

    The sanctioned home for a plugin's state: it survives ``hermes plugins
    update``/``remove`` (which git-pull or delete the install tree) and follows
    the active profile, because Hermes resolves its home per call. Imported
    lazily and never fatally — outside Hermes the import simply fails and the
    fallback below applies.
    """
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        return None


def _legacy_home() -> Path:
    """Where state lived before Hermes' plugin-data convention was adopted.

    On Linux this is the same tree as ``<hermes home>``, which is why it went
    unnoticed; elsewhere it is a different home entirely.
    """
    return Path.home() / ".hermes" / "state" / PLUGIN_NAME


def state_dir() -> Path:
    """Private persistent state owned by this plugin (aliases/transcripts/archive).

    Resolution order: the explicit ``HERMES_TG_USER_STATE_DIR`` override, then
    Hermes' per-plugin data root, then the pre-convention path. The override is
    first so it remains the escape hatch it is documented to be.
    """
    raw = (os.getenv("HERMES_TG_USER_STATE_DIR") or "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = _plugin_home() or _legacy_home()
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
