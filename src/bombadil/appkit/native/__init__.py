"""Native types for `import Bombadil`, registered from Python before any QML loads.

Each module has a `register(ctx)` that calls qmlRegisterType / qmlRegisterSingletonInstance
under the "Bombadil" 1.0 URI. The QML half of the module (share/qml/Bombadil/qmldir) and
these types merge into one import.
"""

from ..context import AppContext

URI = "Bombadil"
MAJOR, MINOR = 1, 0

_registered: AppContext | None = None


def register(ctx: AppContext) -> None:
    """Register every native type once per process."""
    global _registered
    if _registered is not None:
        return
    _registered = ctx
    from . import app as app_mod
    from . import agent, clipboard, command, files, highlighter, processes, system, textfile, vault

    for mod in (app_mod, files, system, processes, command, textfile, vault, clipboard, agent, highlighter):
        mod.register(ctx)


def context() -> AppContext:
    if _registered is None:
        raise RuntimeError("native types are not registered yet")
    return _registered
