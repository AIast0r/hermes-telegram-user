"""Where the plugin keeps its state.

Hermes owns a sanctioned per-plugin data root, and on Linux the plugin's old
default happened to be the same tree — which is exactly why the divergence went
unnoticed until the target moved to a machine where Hermes' home is elsewhere.
These tests pin the resolution order, and the fallback must be checked without
creating anything under the real home.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402


def _paths():
    return plugin_module("core.state.paths")


def _stub_plugin_storage(target):
    """Make ``plugins.plugin_storage`` importable, as it is inside Hermes."""
    calls = []
    package = types.ModuleType("plugins")
    storage = types.ModuleType("plugins.plugin_storage")

    def plugin_data_dir(name):
        calls.append(name)
        return target

    storage.plugin_data_dir = plugin_data_dir
    package.plugin_storage = storage
    saved = {name: sys.modules.get(name) for name in ("plugins", "plugins.plugin_storage")}
    sys.modules["plugins"] = package
    sys.modules["plugins.plugin_storage"] = storage
    return calls, saved


def _unstub(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_the_explicit_override_always_wins():
    with isolated_state() as root:
        assert _paths().state_dir() == root


def test_the_plugin_data_root_is_preferred_when_hermes_offers_it():
    with isolated_state():
        target = Path(tempfile.mkdtemp(prefix="tgu-plugin-data-"))
        calls, saved = _stub_plugin_storage(target)
        previous = os.environ.pop("HERMES_TG_USER_STATE_DIR", None)
        try:
            resolved = _paths().state_dir()
        finally:
            _unstub(saved)
            if previous is not None:
                os.environ["HERMES_TG_USER_STATE_DIR"] = previous

        assert resolved == target
        assert calls == ["telegram-user"], "the directory is keyed by the plugin's own name"
        assert target.is_dir()


def test_without_hermes_state_falls_back_to_the_legacy_path():
    """Standalone use (and this suite) has no Hermes core, so the old path applies."""
    with isolated_state():
        fake_home = Path(tempfile.mkdtemp(prefix="tgu-home-"))
        saved_modules = {n: sys.modules.pop(n, None) for n in ("plugins", "plugins.plugin_storage")}
        previous = {
            key: os.environ.get(key)
            for key in ("HERMES_TG_USER_STATE_DIR", "HOME", "USERPROFILE")
        }
        try:
            for key in ("HERMES_TG_USER_STATE_DIR",):
                os.environ.pop(key, None)
            os.environ["HOME"] = str(fake_home)
            os.environ["USERPROFILE"] = str(fake_home)
            resolved = _paths().state_dir()
        finally:
            sys.modules.update({k: v for k, v in saved_modules.items() if v is not None})
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        assert resolved == fake_home / ".hermes" / "state" / "telegram-user"
        assert resolved.is_dir()


def test_the_state_directory_is_private():
    """Best-effort 0700: the alias/transcript/archive files live in here."""
    with isolated_state() as root:
        _paths().state_dir()
        if os.name != "nt":
            assert (root.stat().st_mode & 0o777) == 0o700
