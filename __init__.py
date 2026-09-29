"""Hermes Telegram User (MTProto) plugin package.

Hermes loads a plugin by importing this package and calling ``register(ctx)``, so
the entry point has to live here rather than only in the two modules that do the
work. The imports are inside the function on purpose: importing this package must
stay cheap, and ``adapter`` pulls in ``gateway.*`` at module level.
"""

from __future__ import annotations

from typing import Any


def register(ctx: Any) -> None:
    """Register the platform adapter and the tool surface.

    Both halves belong to this one entry point: Hermes calls only ``register``,
    and a manifest that declares ``provides_tools`` is read as a promise that
    those names exist after registration.
    """
    from . import adapter, tools

    tools.register_tools(ctx)
    adapter.register(ctx)


__all__ = ["register"]
