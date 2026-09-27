"""Runs a generated app as a native Qt Quick window. The runtime lives in appkit/runtime.py;
this name is kept for callers of the first milestone."""


def run(name: str) -> int:
    from .appkit import runtime
    return runtime.run(name)
