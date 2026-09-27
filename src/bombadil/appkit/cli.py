"""The `bombadil-app` command."""

import argparse
import json
import os
import sys
from pathlib import Path

from .. import apps


def _exit(code: int) -> int:
    """Leave without Python tearing down Qt objects in a random order (a crash at exit
    would look like an app error in the log)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bombadil-app", description="Run, check and manage Bombadil apps.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="open an app as a native window (hot reloads on edit)")
    r.add_argument("name")
    c = sub.add_parser("check", help="load an app offscreen and report errors as JSON")
    c.add_argument("target", help="app name, app directory, or a .qml file")
    c.add_argument("--screenshot", metavar="PNG", help="save a picture of the window")
    c.add_argument("--wait", type=int, default=1200, metavar="MS", help="run this long before judging (1200)")
    c.add_argument("--size", metavar="WxH", help="window size for the screenshot")
    sub.add_parser("list", help="list generated apps")
    cr = sub.add_parser("create", help="create an app from a QML file")
    cr.add_argument("title")
    cr.add_argument("qml", type=Path)
    cr.add_argument("--python", type=Path)
    cr.add_argument("--description", default="")
    cr.add_argument("--icon", default="")
    for verb in ("show", "hide", "toggle", "close"):
        v = sub.add_parser(verb, help=f"{verb} an app" + (" (starts it if needed)" if verb == "show" else ""))
        v.add_argument("name")
    st = sub.add_parser("status", help="last load result and log of an app")
    st.add_argument("name")
    args = p.parse_args(argv)

    if args.cmd == "run":
        from . import runtime
        return _exit(runtime.run(args.name))
    if args.cmd == "check":
        from . import check
        return _exit(check.main(args.target, args.screenshot, args.wait, args.size))
    if args.cmd == "list":
        from . import placement
        running, shown = placement.running(), placement.shown()
        for a in apps.list_apps():
            flags = ("shown" if a.name in shown else "running") if a.name in running else ""
            print(f"{a.name:24} {flags:8} {a.title}  {a.path}")
        return 0
    if args.cmd == "create":
        a = apps.create(args.title, args.qml.read_text(), args.python.read_text() if args.python else None,
                        args.description, icon=args.icon)
        print(a.path)
        return 0
    if args.cmd in ("show", "hide", "toggle", "close"):
        from . import placement
        print(getattr(placement, args.cmd)(args.name))
        return 0
    if args.cmd == "status":
        from . import runtime
        print(json.dumps(runtime.status(args.name), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
