"""The install plan: what bombadil-install is told to do, checked before anything is touched.

The install card writes the plan as JSON; a hand-run install builds one from its arguments. The
password is never in it: it comes to the installer on a file descriptor, so it is in no file, no
argument list and no log. `bombadil-install` runs this module and evals what it prints, so every
value that gets through is matched against a strict pattern first and quoted.

    python3 -m bombadil.installplan --plan plan.json
    python3 -m bombadil.installplan --disk /dev/vda --mode refresh
"""

import argparse
import json
import re
import shlex
import sys
import unicodedata
from pathlib import Path

CARRY_LIST = Path("/usr/share/bombadil/install/carry.list")
ZONEINFO = Path("/usr/share/zoneinfo")

MODES = ("fresh", "refresh")
ZONE_SOURCES = ("detected", "chosen", "unset")
KINDS = ("internal", "usb")
KEYS = {"disk", "mode", "timezone", "timezone_source", "keymap", "hostname", "carry", "encrypt", "kind"}

_DISK = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9._/-]*")
_ZONE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+/-]*")
_KEYMAP = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_CARRY = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._ /@+-]*")


class PlanError(ValueError):
    """The plan is not one the installer will run; the message is for a person."""


def read_carry_list(path: Path = CARRY_LIST) -> list[str]:
    """One home path per line, relative to the home folder; blank lines and # comments are skipped."""
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(_carry_path(line))
    return out


def _carry_path(value: object) -> str:
    if not isinstance(value, str) or not _CARRY.fullmatch(value):
        raise PlanError(f"carry: {value!r} is not a path inside the home folder")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts) or value.startswith("/"):
        raise PlanError(f"carry: {value!r} is not a path inside the home folder")
    return value


def _text(plan: dict, key: str, pattern: re.Pattern, what: str) -> str | None:
    value = plan.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PlanError(f"{key}: {value!r} is not {what}")
    return value


def validate(plan: dict, *, carry_list: list[str], zoneinfo: Path = ZONEINFO, disk: str | None = None,
             mode: str | None = None) -> dict:
    """The plan with every field checked and every default filled in. `disk` and `mode` are the
    command line's, used when the plan has none."""
    if not isinstance(plan, dict):
        raise PlanError("the plan must be a JSON object")
    if "password" in plan:
        raise PlanError("the password is never part of the plan: it comes on a file descriptor")
    unknown = sorted(set(plan) - KEYS)
    if unknown:
        raise PlanError(f"unknown plan field: {', '.join(unknown)}")

    out: dict = {}
    out["disk"] = _text({**plan, "disk": plan.get("disk", disk)}, "disk", _DISK, "a disk under /dev")
    if not out["disk"] or ".." in out["disk"].split("/"):
        raise PlanError("disk: the plan names no disk")

    out["mode"] = plan.get("mode", mode or "fresh")
    if out["mode"] not in MODES:
        raise PlanError(f"mode: {out['mode']!r} is not one of {', '.join(MODES)}")

    zone = _text(plan, "timezone", _ZONE, "a time zone such as Europe/Lisbon")
    source = plan.get("timezone_source")
    if source is not None and source not in ZONE_SOURCES:
        raise PlanError(f"timezone_source: {source!r} is not one of {', '.join(ZONE_SOURCES)}")
    if zone is None:
        if source not in (None, "unset"):
            raise PlanError(f"timezone_source {source!r} needs a timezone")
        zone, source = "UTC", "unset"
    else:
        if ".." in zone.split("/") or not (zoneinfo / zone).is_file():
            raise PlanError(f"timezone: {zone!r} is not a time zone this system knows")
        if source in (None, "unset"):
            source = "chosen" if source is None else "unset"
    out["timezone"], out["timezone_source"] = zone, source

    out["keymap"] = _text(plan, "keymap", _KEYMAP, "a console keymap such as us or de-latin1")
    out["hostname"] = _text(plan, "hostname", _HOST, "a computer name of lowercase letters, digits and dashes")

    enc = plan.get("encrypt")
    if enc is not None and not isinstance(enc, bool):
        raise PlanError("encrypt: must be true or false")
    out["encrypt"] = enc

    kind = plan.get("kind")
    if kind is not None and kind not in KINDS:
        raise PlanError(f"kind: {kind!r} is not one of {', '.join(KINDS)}")
    out["kind"] = kind

    carry = plan.get("carry")
    if carry is None:
        out["carry"] = list(carry_list)
    else:
        if not isinstance(carry, list):
            raise PlanError("carry: must be a list of home paths")
        chosen = [_carry_path(c) for c in carry]
        stray = [c for c in chosen if c not in carry_list]
        if stray:
            raise PlanError(f"carry: {stray[0]!r} is not on the list of things that can come along")
        out["carry"] = chosen
    return out


def shell_assignments(plan: dict) -> str:
    """The validated plan as bash assignments, one P_NAME per field and P_CARRY as an array."""
    lines = []
    for key in ("disk", "mode", "timezone", "timezone_source", "keymap", "hostname", "kind"):
        lines.append(f"P_{key.upper()}={shlex.quote(plan[key] or '')}")
    enc = plan["encrypt"]
    lines.append(f"P_ENCRYPT={'auto' if enc is None else ('yes' if enc else 'no')}")
    lines.append("P_CARRY=(" + " ".join(shlex.quote(c) for c in plan["carry"]) + ")")
    return "\n".join(lines) + "\n"


_JUNK_MODEL = re.compile(r"to be filled|default string|system product|not specified|not applicable|standard pc|"
                         r"virtual machine|^none$|^o\.?e\.?m|unknown|^[0-9a-z]{10}$", re.I)


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", plain.lower())).strip("-")


def suggest_hostname(seed: str, model: str | None = None, name: str | None = None) -> str:
    """The computer name the install card offers: a readable one from the maker's model ("daniels-thinkpad"),
    else bombadil- and four characters of `seed` (the machine id), which differ from one machine to the next
    where a fixed name would collide on a shared network."""
    fallback = "bombadil-" + (_slug(seed)[:4] or "home")
    if not model or _JUNK_MODEL.search(model.strip()):
        return fallback
    word = _slug(" ".join(model.split()[:1]))
    if not word:
        return fallback
    owner = _slug(name or "")
    host = f"{owner}s-{word}" if owner else word
    return host[:63].strip("-") if _HOST.fullmatch(host[:63].strip("-")) else fallback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bombadil.installplan")
    ap.add_argument("--plan", help="the plan, as a JSON file")
    ap.add_argument("--disk", help="the disk, when there is no plan file")
    ap.add_argument("--mode", choices=MODES, help="fresh or refresh, when the plan has none")
    ap.add_argument("--carry-list", type=Path, default=CARRY_LIST)
    ap.add_argument("--zoneinfo", type=Path, default=ZONEINFO)
    args = ap.parse_args(argv)
    try:
        raw = json.loads(Path(args.plan).read_text()) if args.plan else {}
        plan = validate(raw, carry_list=read_carry_list(args.carry_list), zoneinfo=args.zoneinfo,
                        disk=args.disk, mode=args.mode)
    except (PlanError, OSError, json.JSONDecodeError) as e:
        print(f"install plan: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(shell_assignments(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
