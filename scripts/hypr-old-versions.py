#!/usr/bin/env python3
"""Keep share/hypr/old/ complete: every text that share/hypr/bombadil.lua (or the skel hyprland.lua it grew
out of) ever had in git, as old/<first 8 hex of the git blob id>.lua.

  scripts/hypr-old-versions.py          write the missing files (from every branch: a build for a test machine can
                                        come from one that is not merged)
  scripts/hypr-old-versions.py --check  list what is missing from the history of HEAD and exit 1 (the test runs
                                        this; another branch's versions are not its business)

homefix (src/bombadil/homefix.py) tells a person's own lines from Bombadil's by comparing their hyprland.lua
with these texts. A line that Bombadil shipped once but that is missing here would be kept as the person's own,
and then run after Bombadil's current part and undo its fix.

When you change share/hypr/bombadil.lua, run this before you commit: the committed text is then still the
previous one, and it is added here. The text you are editing is not added; it is the current one. Commit the new
file with the change. Nothing is ever removed from old/: a machine that still has a version needs it.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "share" / "hypr" / "old"
CURRENT = "share/hypr/bombadil.lua"
# Where the same file lived before the config was split.
PATHS = (CURRENT, "iso/airootfs/etc/skel/.config/hypr/hyprland.lua")
SHORT = 8


def git(*args: str, text: bool = True):
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=text, check=False)
    except OSError:
        return 127, "" if text else b""   # no git here
    return out.returncode, out.stdout


def has_history() -> bool:
    code, out = git("rev-list", "--count", "--all")
    return code == 0 and out.strip().isdigit() and int(out) > 0


def current_blob() -> str:
    """The blob id of the file as it is on disk now; that text is the current one and needs no copy."""
    path = ROOT / CURRENT
    if not path.is_file():
        return ""
    code, out = git("hash-object", str(path))
    return out.strip() if code == 0 else ""


def history(refs: str) -> dict[str, str]:
    """Every committed text of the file reachable from `refs` (HEAD or --all), as {blob id: a commit that has it}."""
    seen: dict[str, str] = {}
    code, out = git("log", refs, "--format=%H", "--", *PATHS)
    if code != 0:
        return seen
    for commit in out.split():
        for path in PATHS:
            code, blob = git("rev-parse", "--verify", "--quiet", f"{commit}:{path}")
            if code == 0 and blob.strip():
                seen.setdefault(blob.strip(), commit)
    return seen


def missing(refs: str) -> dict[str, str]:
    """{blob id: file name} of the committed texts that old/ lacks. The text on disk now is left out."""
    now = current_blob()
    return {blob: f"{blob[:SHORT]}.lua" for blob in history(refs)
            if blob != now and not (OLD / f"{blob[:SHORT]}.lua").exists()}


def main(argv: list[str]) -> int:
    if not has_history():
        print("no git history here: nothing to read the old versions from", file=sys.stderr)
        return 2
    todo = missing("HEAD" if "--check" in argv else "--all")
    if "--check" in argv:
        for blob, name in sorted(todo.items(), key=lambda kv: kv[1]):
            print(f"missing: share/hypr/old/{name} (git blob {blob})")
        if todo:
            print("run scripts/hypr-old-versions.py and commit the new files with the change to share/hypr/bombadil.lua",
                  file=sys.stderr)
        return 1 if todo else 0
    OLD.mkdir(parents=True, exist_ok=True)
    for blob, name in sorted(todo.items(), key=lambda kv: kv[1]):
        code, data = git("cat-file", "blob", blob, text=False)
        if code != 0:
            print(f"could not read git blob {blob}", file=sys.stderr)
            return 1
        (OLD / name).write_bytes(data)
        print(f"wrote share/hypr/old/{name}")
    if not todo:
        print("share/hypr/old/ is complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
