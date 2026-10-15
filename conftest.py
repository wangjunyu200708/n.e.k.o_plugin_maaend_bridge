"""Let pytest import the plugin directly from this standalone repository.

Background: the repository root's ``__init__.py`` *is* the plugin entry — it
imports the host-only ``plugin.sdk.plugin`` at module level and pulls its own
submodules with relative imports. The directory name
``n.e.k.o_plugin_maaend_bridge`` is not a legal Python identifier, so pytest
cannot import it as a package; worse, the repository root is on ``sys.path``,
so pytest treats it as a package to trace and calls ``import __init__``, which
executes the entry file under a *top-level* name and makes ``from .service
import ...`` raise ``attempted relative import with no known parent package``.
The symptom is that **every test under ``tests/`` errors during setup** —
including a ``test_smoke.py`` that imports nothing at all.

This file only takes effect in the standalone repository: inside the host tree
``plugin.sdk.plugin`` exists, ``STANDALONE`` is false, and the whole stub block
is skipped, so the host layout is untouched. In the standalone repository it
does three things:

  1. injects the minimal host stubs the entry needs at *import* time, so tests
     run under any Python 3.11 without an N.E.K.O. checkout;
  2. loads the root ``__init__.py`` under the legal package name
     ``maaend_bridge_plugin``, with ``submodule_search_locations`` pointing at
     the repository root so ``from .service import ...`` resolves to the real
     submodules;
  3. registers that module as ``sys.modules["__init__"]`` so pytest's tracing
     import hits the already-executed module instead of re-running the entry.

The stubs only cover names the plugin touches **while importing**. ``Ok`` /
``Err`` behave like the real SDK's (``is_ok()``, ``.value``, ``.error``) so a
test that asserts on them is testing something real.
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys
import types
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parent


def _missing(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return True
    return False


STANDALONE = _missing("plugin.sdk.plugin")


if STANDALONE:
    # ── plugin.sdk.plugin ───────────────────────────────────────────────
    class SdkError(Exception):
        """Stub; the real one lives in the host SDK."""

    class Ok:
        __slots__ = ("value",)

        def __init__(self, value: Any = None) -> None:
            self.value = value

        def is_ok(self) -> bool:
            return True

    class Err:
        __slots__ = ("error",)

        def __init__(self, error: Any = None) -> None:
            self.error = error

        def is_ok(self) -> bool:
            return False

    def _identity_decorator(*_args: Any, **_kwargs: Any):
        """Decorator stub: keeps the function. Registration is a runtime concern."""

        def _wrap(fn):
            return fn

        return _wrap

    def neko_plugin(cls):
        return cls

    class _Ui:
        context = staticmethod(_identity_decorator)
        action = staticmethod(_identity_decorator)

    class NekoPluginBase:
        """Covers only what the plugin reaches for after import.

        No test instantiates :class:`MaaendBridgePlugin`; if one ever does,
        ``config`` and ``ctx`` need filling in here too.
        """

        def __init__(self, ctx: Any = None) -> None:
            self.ctx = ctx

        def enable_file_logging(self, log_level: str = "INFO"):
            return logging.getLogger("maaend_bridge")

        def push_message(self, **_kwargs: Any) -> None:
            return None

    _plugin_sdk_plugin = types.ModuleType("plugin.sdk.plugin")
    for _name, _obj in {
        "Err": Err,
        "NekoPluginBase": NekoPluginBase,
        "Ok": Ok,
        "SdkError": SdkError,
        "lifecycle": _identity_decorator,
        "llm_tool": _identity_decorator,
        "neko_plugin": neko_plugin,
        "plugin_entry": _identity_decorator,
        "ui": _Ui,
    }.items():
        setattr(_plugin_sdk_plugin, _name, _obj)
    sys.modules["plugin"] = types.ModuleType("plugin")
    sys.modules["plugin.sdk"] = types.ModuleType("plugin.sdk")
    sys.modules["plugin.sdk.plugin"] = _plugin_sdk_plugin


def _load_plugin_root():
    """Load the root ``__init__.py`` under the legal package name.

    ``submodule_search_locations=[ROOT]`` makes the module a package rooted at
    the repository, so ``from .client import ...`` resolves to the real
    submodules rather than failing on the dotted directory name.
    """
    spec = importlib.util.spec_from_file_location(
        "maaend_bridge_plugin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["maaend_bridge_plugin"] = module
    spec.loader.exec_module(module)
    return module


try:
    plugin_root = _load_plugin_root()
except Exception:  # pragma: no cover - host layout or a genuine load failure
    plugin_root = None
else:
    # pytest will try ``import __init__`` (the repository root is on
    # pythonpath). Registering it here makes that hit the loaded module
    # instead of re-executing the host import.
    sys.modules.setdefault("__init__", plugin_root)


@pytest.fixture
def plugin_module():
    """The plugin root module (``MaaendBridgePlugin`` lives there)."""
    if plugin_root is None:
        pytest.skip("plugin root module unavailable (host layout or load failure)")
    return plugin_root
