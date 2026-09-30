#!/usr/bin/env python3
"""Zip extension/ into an (unsigned) XPI: build_xpi.py [out.xpi]. Prints the path.

As a library: build(out, patch=None, drop=()) where `patch` is merged into manifest.json (shallow) and `drop` lists
top-level manifest keys to delete; the Q2 scripts use it to make manifest-v3 / event-page / no-permission variants.
"""
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "extension")
DEFAULT_OUT = os.path.expanduser("~/.cache/bombadil-lab/build/bombadil-mail-lab.xpi")


def build(out=DEFAULT_OUT, patch=None, drop=(), remove_permissions=()):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(SRC):
            for f in sorted(files):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, SRC)
                if rel == "manifest.json" and (patch or drop or remove_permissions):
                    m = json.load(open(p))
                    m.update(patch or {})
                    for k in drop:
                        m.pop(k, None)
                    m["permissions"] = [x for x in m.get("permissions", []) if x not in remove_permissions]
                    z.writestr(rel, json.dumps(m, indent=2))
                else:
                    z.write(p, rel)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT))
