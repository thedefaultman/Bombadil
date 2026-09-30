#!/usr/bin/env python3
"""Zip extension/ into an (unsigned) XPI: build_xpi.py [out.xpi]. Prints the path."""
import os
import sys
import zipfile

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "extension")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/bombadil-lab/build/bombadil-mail-lab.xpi")
os.makedirs(os.path.dirname(out), exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _dirs, files in os.walk(src):
        for f in sorted(files):
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src))
print(out)
