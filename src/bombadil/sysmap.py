"""Pictures of the machine, captured from the machine.

A passenger cannot check what the AI says about the network or the disks, so the picture of
them is never the AI's: each `capture_*` here runs the real commands (`ip`, `nmcli`, `lsblk`,
`systemd-analyze`, `pw-dump`, `hyprctl`), reads their output with a pure parser and returns
a diagram card (see cards.py) plus the plain facts it was made from. No model, no tokens,
the same on Claude and Codex, and it works offline.

Each capture has a 500 ms budget: its commands run side by side in threads and what has not
answered by then is treated as missing, so a picture always comes back at once, drawn from
what could be read. The parsers take text, so the tests feed them real captured output.

`facts` are what a receipt compares: before a turn and after it, the ones that changed become
a small before and after (receipt()). Values that move by themselves (signal, latency, disk
use) are marked volatile and never count as a change.
"""

import json
import re
import socket
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as _wait
from pathlib import Path

from . import cards

BUDGET = 0.5
KINDS = ("network", "boot", "service", "disks", "sound", "screens")
TITLES = {"network": "How you're connected", "boot": "What starts when you boot", "disks": "Your disks",
          "sound": "What's playing where", "screens": "Your screens"}
# The tools' own words in a fixed shape: no translation, no colour.
_ENV = {"LC_ALL": "C", "LANG": "C", "SYSTEMD_COLORS": "0", "NO_COLOR": "1", "TERM": "dumb"}
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="sysmap")
_OUTER = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sysmap-part")   # whole captures, which run jobs in _POOL

Run = Callable[..., "str | None"]


class Unavailable(Exception):
    """Nothing could be read: the message is one plain sentence for the line."""


def run(argv: list[str], timeout: float = BUDGET) -> str | None:
    """A command's output, or None when it is missing, fails or is too slow."""
    import os
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                           env={**os.environ, **_ENV}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _gather(jobs: dict[str, Callable[[], object]], budget: float, pool=None) -> dict[str, object]:
    """Run the jobs side by side; what has not finished within the budget is None."""
    futures = {name: (pool or _POOL).submit(fn) for name, fn in jobs.items()}
    _wait(list(futures.values()), timeout=budget)
    out: dict[str, object] = {}
    for name, f in futures.items():
        try:
            out[name] = f.result(timeout=0) if f.done() else None
        except Exception:  # noqa: BLE001 - a job that broke is a job that gave nothing
            out[name] = None
    return out


def _within(fn: Callable[[], object], budget: float):
    """One job under the budget: its answer, or None when it has not come by then."""
    return _gather({"one": fn}, budget)["one"]


def _short(unit: str) -> str:
    """A unit as people say it: without .service, at most a label long."""
    unit = unit.removesuffix(".service")
    return unit if len(unit) <= cards.MAX_LABEL else unit[:cards.MAX_LABEL - 1] + "…"


def _json(text: str | None):
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def _human(n: float) -> str:
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if n >= 100 or unit == "B" else f"{n:.1f}".rstrip("0").rstrip(".") + f" {unit}"
        n /= 1000
    return f"{n:.0f} TB"


def _secs(s: str) -> float:
    """'1min 2.345s' -> 62.345; '188ms' -> 0.188."""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)\s*(min|ms|us|s|h)", s):
        total += float(num) * {"h": 3600, "min": 60, "s": 1, "ms": 0.001, "us": 0.000001}[unit]
    return total


def _fmt_secs(x: float) -> str:
    return f"{x * 1000:.0f} ms" if x < 1 else f"{x:.1f} s".replace(".0 s", " s") if x < 60 else f"{x / 60:.1f} min"


def _result(kind: str, spec: dict, facts: list[dict], highlight=None, say: str | None = None) -> dict:
    """The validated card and its facts. A capture that made an invalid card is a bug, not a state."""
    spec = {**spec}
    if highlight is not None:
        spec["highlight"] = highlight
    if say is not None:
        spec["say"] = say
    card, errors = cards.validate_diagram(spec)
    if card is None:
        raise Unavailable(f"Could not draw {kind}: {errors[0]}")
    card["source"] = kind
    return {"card": card, "facts": facts}


# ---------------------------------------------------------------- network

_TUNNEL_RE = re.compile(r"^(wg|tun|tap|tailscale|ppp|vpn|zt)\w*")
PROVIDER_HOSTS = {"claude": ("Claude", "api.anthropic.com"), "codex": ("Codex", "api.openai.com")}


def nmcli_fields(line: str) -> list[str]:
    """One line of `nmcli -t`: fields separated by ':', a literal ':' escaped as '\\:'."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 1
        elif c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def parse_nmcli_devices(text: str | None) -> dict[str, dict]:
    """`nmcli -t -f GENERAL.DEVICE,GENERAL.TYPE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show`: per device."""
    devs: dict[str, dict] = {}
    cur: dict | None = None
    for raw in (text or "").splitlines():
        if not raw.strip():
            cur = None
            continue
        key, _, val = raw.partition(":")
        key = re.sub(r"\[\d+\]$", "", key)
        val = val.replace("\\:", ":")
        if key == "GENERAL.DEVICE":
            cur = devs.setdefault(val, {"dns": []})
        elif cur is not None:
            if key == "GENERAL.TYPE":
                cur["type"] = val
            elif key == "IP4.GATEWAY" and val:
                cur["gateway"] = val
            elif key == "IP4.DNS" and val:
                cur["dns"].append(val)
    return devs


def parse_wifi_list(text: str | None) -> tuple[str, int] | None:
    """`nmcli -t -f IN-USE,SSID,SIGNAL device wifi list`: the network in use and its signal."""
    for line in (text or "").splitlines():
        f = nmcli_fields(line)
        if len(f) >= 3 and f[0].strip() == "*":
            try:
                return f[1], int(f[2])
            except ValueError:
                return f[1], -1
    return None


def resolv_servers(text: str | None) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s*nameserver\s+(\S+)", text or "", re.M)]


def _tcp_ms(host: str, port: int = 443, timeout: float = 0.4) -> int | None:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return None
    return round((time.monotonic() - t0) * 1000)


def capture_network(run_: Run = run, probe: Callable[[str], int | None] | None = None,
                    read: Callable[[str], str | None] | None = None, provider: str = "claude",
                    budget: float = BUDGET, wireless: Callable[[str], bool] | None = None) -> dict:
    """The path from this laptop to the provider you are talking to, from `ip`, NetworkManager
    and a real connection to the provider's host. The first broken link is red, with one sentence."""
    brand, host = PROVIDER_HOSTS.get(provider, PROVIDER_HOSTS["claude"])
    probe = probe or _tcp_ms
    read = read or _read_text
    wireless = wireless or (lambda dev: Path(f"/sys/class/net/{dev}/wireless").exists())
    got = _gather({
        "route": lambda: _json(run_(["ip", "-j", "route", "get", "1.1.1.1"], budget)),
        "default": lambda: _json(run_(["ip", "-j", "route", "show", "default"], budget)),
        "devices": lambda: run_(["nmcli", "-t", "-f", "GENERAL.DEVICE,GENERAL.TYPE,IP4.GATEWAY,IP4.DNS",
                                 "device", "show"], budget),
        "wifi": lambda: run_(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list",
                              "--rescan", "no"], budget),
        "connectivity": lambda: run_(["nmcli", "-t", "-g", "CONNECTIVITY", "general"], budget),
        "resolv": lambda: read("/etc/resolv.conf"),
        "probe": lambda: probe(host),
    }, budget)
    route = (got["route"] or [None])[0] if isinstance(got["route"], list) else None
    defaults = got["default"] if isinstance(got["default"], list) else []
    devices = parse_nmcli_devices(got["devices"])
    wifi = parse_wifi_list(got["wifi"])
    conn = str(got["connectivity"] or "").strip().lower()
    servers = resolv_servers(got["resolv"])
    latency = got["probe"] if isinstance(got["probe"], int) else None

    if not route or not route.get("dev"):
        if got["route"] is None and got["default"] is None and not devices:
            raise Unavailable("Could not read the network: ip did not answer.")
        return _result("network", _net_spec(brand, [
            {"id": "laptop", "label": "This laptop", "icon": "cpu", "state": "ok"},
            {"id": "link", "label": "No network", "sub": "nothing is connected", "icon": "wifi", "state": "bad"},
        ], "link", "This laptop is not connected to any network.", [{"from": "laptop", "to": "link", "state": "bad"}]),
            [{"key": "link", "label": "Network", "value": "not connected"}])

    dev = str(route["dev"])
    src = str(route.get("prefsrc") or "")
    tunnel = bool(_TUNNEL_RE.match(dev))
    under = dev
    if tunnel:
        # The route to the internet runs through the tunnel; the cable or Wi-Fi under it is the
        # default route that is not a tunnel.
        cands = [d for d in defaults if isinstance(d, dict) and not _TUNNEL_RE.match(str(d.get("dev", "")))]
        cands.sort(key=lambda d: d.get("metric", 0))
        if cands:
            under = str(cands[0].get("dev"))
    gateway = str(route.get("gateway") or "")
    if tunnel and cands:
        gateway = str(cands[0].get("gateway") or gateway)
    info = devices.get(under) or {}
    dns = info.get("dns") or ([s for s in servers if not s.startswith("127.")] or servers)
    is_wifi = wireless(under) or info.get("type") == "wifi"
    nodes: list[dict] = [{"id": "laptop", "label": "This laptop", "sub": src or dev, "icon": "cpu", "state": "ok"}]
    facts: list[dict] = []
    say = ""
    broken: str | None = None
    if tunnel:
        nodes.append({"id": "vpn", "label": "VPN tunnel", "sub": dev, "icon": "shield", "state": "new"})
        facts.append({"key": "vpn", "label": "VPN tunnel", "value": dev})
    if is_wifi:
        ssid, signal = wifi if wifi else ("", -1)
        node = {"id": "link", "label": f"Wi-Fi “{ssid}”"[:32] if ssid else "Wi-Fi", "icon": "wifi", "state": "ok",
                "volatile": True}
        if signal >= 0:
            node["sub"] = f"{signal}%"
            if signal < 40:
                node["state"] = "warn"
                say = f"The Wi-Fi signal is weak ({signal}%): moving closer to the router should help."
        nodes.append(node)
        facts.append({"key": "link", "label": "Wi-Fi", "value": ssid or under})
    else:
        nodes.append({"id": "link", "label": "Cable", "sub": under, "icon": "network", "state": "ok"})
        facts.append({"key": "link", "label": "Network cable", "value": under})
    nodes.append({"id": "router", "label": "Router", "sub": gateway or "not found", "icon": "network",
                  "state": "ok" if gateway else "warn"})
    if gateway:
        facts.append({"key": "gateway", "label": "Router", "value": gateway})
    if dns:
        nodes[-1]["note"] = "names are looked up at " + ", ".join(dns[:2])
        facts.append({"key": "dns", "label": "Names looked up at", "value": ", ".join(dns[:2])})
    nodes.append({"id": "internet", "label": "Internet", "icon": "globe", "state": "ok"})
    nodes.append({"id": "provider", "label": brand, "icon": "bot", "state": "ok", "volatile": True,
                  "sub": f"{latency} ms" if latency is not None else "not answering"})
    internet, provider_node = nodes[-2], nodes[-1]
    if conn in ("none", "limited"):
        internet["state"] = "bad"
        internet["sub"] = "not reachable"
        provider_node.pop("sub", None)
        provider_node.pop("state", None)
        broken = "internet"
        say = "The router answers, but it isn't reaching the internet."
    elif conn == "portal":
        internet["state"] = "warn"
        internet["sub"] = "sign-in needed"
        say = "This network wants you to sign in on its page first."
    elif latency is None:
        provider_node["state"] = "bad"
        broken = "provider"
        say = (f"The internet answers, but {brand} is not: it may be down, or blocked here." if conn == "full"
               else f"{brand} is not answering: it may be down, or blocked here.")
    elif not say:
        say = f"All of it answers: {brand} replies in {latency} ms."
    links = [{"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:])]
    if broken:
        for ln in links:
            if ln["to"] == broken:
                ln["state"] = "bad"
    return _result("network", _net_spec(brand, nodes, broken, say, links), facts)


def _net_spec(brand: str, nodes: list[dict], broken: str | None, say: str, links: list[dict]) -> dict:
    return {"shape": "chain", "title": TITLES["network"], "nodes": nodes, "links": links,
            "highlight": [broken] if broken else [], "say": say}


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------- boot

_CHAIN_RE = re.compile(r"^[\s│└├─]*([\w@.:\\-]+\.(?:target|service|socket|mount|path|timer|device|swap|slice|scope))"
                       r"\s+@([\d.]+\s*(?:min|ms|us|s)?(?:\s*[\d.]+\s*(?:s|ms))?)(?:\s+\+([\d.]+\s*(?:min|ms|us|s)?"
                       r"(?:\s*[\d.]+\s*(?:s|ms))?))?\s*$")
_TIME_RE = re.compile(r"Startup finished in (.*?)(?: = ([\d.]+\s*(?:min|ms|s)(?:\s*[\d.]+\s*s)?))?\s*$", re.M)


def parse_critical_chain(text: str | None) -> list[tuple[str, float, float]]:
    """`systemd-analyze critical-chain`: (unit, started at, took), earliest first."""
    rows = []
    for line in (text or "").splitlines():
        m = _CHAIN_RE.match(line)
        if m:
            rows.append((m.group(1), _secs(m.group(2)), _secs(m.group(3) or "")))
    rows.sort(key=lambda r: r[1])
    return rows


def capture_boot(run_: Run = run, budget: float = BUDGET) -> dict:
    """What holds the boot up, from the critical chain: each unit on the way to the desktop, the
    slow one in amber."""
    got = _gather({"chain": lambda: run_(["systemd-analyze", "critical-chain", "--no-pager"], budget),
                   "time": lambda: run_(["systemd-analyze", "time", "--no-pager"], budget)}, budget)
    rows = parse_critical_chain(got["chain"] if isinstance(got["chain"], str) else None)
    if not rows:
        raise Unavailable("Could not read the boot: systemd-analyze did not answer (a live system has no boot record).")
    m = _TIME_RE.search(got["time"] if isinstance(got["time"], str) else "")
    total = _secs(m.group(2)) if m and m.group(2) else 0.0
    keep = rows
    if len(rows) > cards.MAX_NODES:
        # The slowest steps and the last one, in the order they happened.
        slowest = {r[0] for r in sorted(rows, key=lambda r: -r[2])[:cards.MAX_NODES - 1]} | {rows[-1][0]}
        keep = [r for r in rows if r[0] in slowest][-cards.MAX_NODES:]
    slow_min = 1.0
    worst = max(keep, key=lambda r: r[2])
    nodes = []
    for i, (unit, at, took) in enumerate(keep):
        n = {"id": f"u{i + 1}", "label": _short(unit),
             "time": _fmt_secs(at), "weight": round(took, 3), "sub": f"took {_fmt_secs(took)}" if took >= 0.001 else "",
             "opens": {"kind": "unit", "value": unit}}
        if took >= slow_min and unit == worst[0]:
            n["state"] = "warn"
        nodes.append(n)
    nodes = [{k: v for k, v in n.items() if v != ""} for n in nodes]
    lit = [n["id"] for n in nodes if n.get("state") == "warn"]
    say = (f"{_short(worst[0])} takes {_fmt_secs(worst[2])}, the longest wait on the way to the desktop." if lit
           else "Nothing holds the boot up: no step takes a second.")
    title = TITLES["boot"] + (f" ({_fmt_secs(total)} in all)" if total else "")
    spec = {"shape": "timeline", "title": title[:cards.MAX_TITLE], "nodes": nodes, "links": [], "highlight": lit,
            "say": say}
    return _result("boot", spec, [])


# ---------------------------------------------------------------- one service

_NOISE_DEPS = {"-.mount", "sysinit.target", "basic.target", "shutdown.target", "multi-user.target",
               "system.slice", "network.target", "network-pre.target", "local-fs.target", "sockets.target"}


def parse_show(text: str | None) -> list[dict[str, str]]:
    """`systemctl show` output: one dict per unit (blocks separated by a blank line)."""
    out, cur = [], {}
    for line in (text or "").splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        out.append(cur)
    return out


def unit_name(name: str) -> str:
    name = str(name).strip()
    return name if re.search(r"\.(service|socket|timer|target|mount|path|slice|scope)$", name) else name + ".service"


def capture_service(target: str, run_: Run = run, budget: float = BUDGET) -> dict:
    """One service and what it needs (Requires and Wants), each with its state, and the package
    that owns it."""
    unit = unit_name(target)
    if not re.fullmatch(r"[\w@.:-]{1,80}", unit):
        raise Unavailable(f"“{target}” is not a service name.")
    props = "Id,Description,LoadState,ActiveState,SubState,UnitFileState,Requires,Wants,FragmentPath"
    main = parse_show(_within(lambda: run_(["systemctl", "show", "-p", props, unit], budget), budget))
    if not main:
        raise Unavailable("Could not read services: systemctl did not answer.")
    m = main[0]
    if m.get("LoadState") == "not-found":
        raise Unavailable(f"There is no service called {target}.")
    deps = []
    for key in ("Requires", "Wants"):
        for d in (m.get(key) or "").split():
            if d not in _NOISE_DEPS and not d.endswith(".slice") and d != unit and d not in deps:
                deps.append(d)
    deps = deps[:8]
    frag = m.get("FragmentPath") or ""
    got = _gather({
        "deps": lambda: run_(["systemctl", "show", "-p", "Id,ActiveState,SubState", *deps], budget) if deps else "",
        "owner": lambda: run_(["pacman", "-Qo", "-q", frag], budget) if frag.startswith("/usr/") else None,
    }, budget)
    states = {d.get("Id"): d for d in parse_show(got["deps"] if isinstance(got["deps"], str) else "")}
    owner = str(got["owner"] or "").strip()

    def tone(active: str) -> str:
        return {"active": "ok", "activating": "warn", "failed": "bad"}.get(active, "warn" if active else "")

    active = m.get("ActiveState", "")
    sub = f"{active} ({m.get('SubState', '')})" if m.get("SubState") else active
    top: dict = {"id": "unit", "label": _short(m.get("Id", unit)), "sub": sub[:cards.MAX_SUB],
                 "state": tone(active) or "warn", "opens": {"kind": "unit", "value": unit}}
    if m.get("Description"):
        top["note"] = m["Description"][:cards.MAX_SUB]
    if owner and re.fullmatch(r"[a-z0-9@._+-]+", owner):
        top["icon"] = "archive"
        top["note"] = (top.get("note", "") + f" · from {owner}").strip(" ·")[:cards.MAX_SUB]
    nodes, links, bad = [top], [], []
    for i, d in enumerate(deps):
        st = states.get(d, {})
        a = st.get("ActiveState", "")
        n = {"id": f"d{i + 1}", "label": _short(d),
             "sub": (f"{a} ({st['SubState']})" if st.get("SubState") else a)[:cards.MAX_SUB],
             "opens": {"kind": "unit", "value": d}}
        if tone(a):
            n["state"] = tone(a)
        if a in ("failed", "inactive") and d in (m.get("Requires") or "").split():
            n["state"] = "bad"
            bad.append(n["id"])
        nodes.append({k: v for k, v in n.items() if v != ""})
        links.append({"from": "unit", "to": n["id"]})
    if active == "failed" or bad:
        say = (f"{top['label']} failed to start" + (f" because {_short(deps[int(bad[0][1:]) - 1])} is not running."
                                                      if bad else "."))
    elif active != "active":
        say = f"{top['label']} is not running."
    elif not deps:
        say = f"{top['label']} runs on its own: it needs nothing else here."
    else:
        say = f"{top['label']} is running, and everything it needs is up."
    spec = {"shape": "layers", "title": f"What {top['label']} needs"[:cards.MAX_TITLE],
            "nodes": nodes, "links": links, "highlight": bad or (["unit"] if active == "failed" else []), "say": say}
    facts = [{"key": "state", "label": top["label"], "value": sub},
             {"key": "enabled", "label": "Starts at boot", "value": m.get("UnitFileState", "") or "unknown"}]
    facts += [{"key": f"dep:{d}", "label": d, "value": (states.get(d) or {}).get("ActiveState", "unknown")}
              for d in deps]
    res = _result("service", spec, facts)
    res["card"]["target"] = unit
    return res


# ---------------------------------------------------------------- disks

_READ_ONLY_FS = {"squashfs", "iso9660", "erofs", "udf", "cramfs"}   # full by design, never a warning
_DISK_TYPES = {"disk", "part", "crypt", "lvm", "raid0", "raid1", "raid5", "raid6", "raid10", "md"}


def _truthy(v) -> bool:
    return v is True or str(v).lower() in ("1", "true")


def _mounts(node: dict) -> list[str]:
    m = node.get("mountpoints")
    if m is None and node.get("mountpoint"):
        m = [node["mountpoint"]]
    # The main mount first ("/" before "/home" before /var/lib/docker), then the rest.
    return sorted((x for x in (m or []) if x), key=lambda x: (x != "/", len(x), x))


def _usage(fm: dict | None) -> dict[str, tuple[int, int, str]]:
    """`findmnt -b -J -o TARGET,SOURCE,FSTYPE,SIZE,USED`: mount point -> (used, size, filesystem)."""
    out: dict[str, tuple[int, int, str]] = {}

    def walk(items):
        for f in items or []:
            try:
                out[f["target"]] = (int(f.get("used") or 0), int(f.get("size") or 0), str(f.get("fstype") or ""))
            except (KeyError, TypeError, ValueError):
                pass
            walk(f.get("children"))
    walk((fm or {}).get("filesystems"))
    return out


def capture_disks(run_: Run = run, budget: float = BUDGET) -> dict:
    """The disks, their partitions and where each is mounted, with how full: the answer to
    "where did my disk go?" as a picture. Amber from 90% full, red from 97%."""
    got = _gather({
        "lsblk": lambda: _json(run_(["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MODEL,RM,MOUNTPOINTS"],
                                    budget)),
        "findmnt": lambda: _json(run_(["findmnt", "-b", "-J", "-o", "TARGET,SOURCE,FSTYPE,SIZE,USED"], budget)),
    }, budget)
    lsblk = got["lsblk"]
    if not isinstance(lsblk, dict) or not lsblk.get("blockdevices"):
        raise Unavailable("Could not read the disks: lsblk did not answer.")
    use = _usage(got["findmnt"] if isinstance(got["findmnt"], dict) else None)
    disks = [d for d in lsblk["blockdevices"] if d.get("type") == "disk" and not str(d.get("name", "")).startswith(
        ("zram", "loop", "ram"))]
    facts: list[dict] = []
    nodes: list[dict] = []
    links: list[dict] = []
    full: list[str] = []
    room = cards.MAX_NODES

    def leaves(d: dict, parent: str) -> list[tuple[dict, str]]:
        """The partitions, encrypted volumes and the like under a disk, each with what it sits on."""
        out: list[tuple[dict, str]] = []
        for c in d.get("children") or []:
            if c.get("type") in _DISK_TYPES:
                out.append((c, parent))
                out += leaves(c, f"part:{c['name']}")
        return out

    def fs_bits(p: dict) -> tuple[list[str], str, dict | None]:
        """What a filesystem shows: its mount point, type, size and how full; the state; what it opens."""
        mounts = _mounts(p)
        used, size, mounted_fs = use.get(mounts[0], (0, 0, "")) if mounts else (0, 0, "")
        fstype = p.get("fstype") or mounted_fs
        frac = used / size if size else 0
        bits = [x for x in (_human(p.get("size", 0)), fstype) if x]
        if size:
            bits.insert(0, f"{round(frac * 100)}% full")   # first: the end of a long line gets cut
        if mounts:
            bits.insert(0, mounts[0])
        state = ("ok" if fstype in _READ_ONLY_FS else
                 "bad" if frac >= 0.97 else "warn" if frac >= 0.90 else "ok")
        opens = {"kind": "path", "value": mounts[0]} if mounts and mounts[0].startswith("/") else None
        return bits, state, opens

    per_disk = max(1, (room - len(disks)) // max(1, len(disks)))
    for d in disks[:room]:
        did = f"disk:{d['name']}"
        rm = _truthy(d.get("rm"))
        model = (d.get("model") or "").strip()
        parts = leaves(d, did)
        # A partition with a mount point, or the biggest ones, when there are more than the picture holds.
        parts.sort(key=lambda pp: (not _mounts(pp[0]), -int(pp[0].get("size") or 0)))
        node = {"id": did, "label": d["name"], "icon": "hard-drive", "state": "active" if rm else "ok"}
        facts.append({"key": did, "label": "Disk " + d["name"], "value": _human(d.get("size", 0)) +
                      (" (removable)" if rm else "")})
        if not parts and (_mounts(d) or d.get("fstype")):
            # A disk with a filesystem straight on it (no partitions): the disk is the filesystem.
            bits, state, opens = fs_bits(d)
            node["sub"] = " · ".join(x for x in [model, *bits] if x)[:cards.MAX_SUB]
            if state != "ok":
                node["state"] = state
                full.append(did)
            if opens:
                node["opens"] = opens
            m = _mounts(d)
            facts.append({"key": f"mount:{did}", "label": "Disk " + d["name"] + " mounted at",
                          "value": m[0] if m else "not mounted"})
            used, size, _fs = use.get(m[0], (0, 0, "")) if m else (0, 0, "")
            if size:
                facts.append({"key": f"use:{did}", "label": d["name"] + " used", "value": f"{round(used / size * 100)}%",
                              "volatile": True})
        else:
            node["sub"] = " · ".join(x for x in (model, _human(d.get("size", 0))) if x)[:cards.MAX_SUB]
        nodes.append(node)
        for p, parent in parts[:per_disk]:
            pid = f"part:{p['name']}"
            mounts = _mounts(p)
            bits, state, opens = fs_bits(p)
            n = {"id": pid, "label": p["name"], "sub": " · ".join(bits)[:cards.MAX_SUB],
                 "icon": "folder" if mounts else "file", "state": state}
            if opens:
                n["opens"] = opens
            if state != "ok":
                full.append(pid)
            nodes.append(n)
            links.append({"from": parent, "to": pid})
            facts.append({"key": pid, "label": "Partition " + p["name"],
                          "value": (mounts[0] if mounts else "not mounted") + (f", {p['fstype']}" if p.get("fstype") else "")})
            used, size, _fs = use.get(mounts[0], (0, 0, "")) if mounts else (0, 0, "")
            if size:
                facts.append({"key": f"use:{pid}", "label": p["name"] + " used", "value": f"{round(used / size * 100)}%",
                              "volatile": True})
    nodes = nodes[:cards.MAX_NODES]
    kept = {n["id"] for n in nodes}
    links = [ln for ln in links if ln["from"] in kept and ln["to"] in kept]
    if full:
        worst = next(n for n in nodes if n["id"] == full[0])
        pct = re.search(r"(\d+)% full", worst["sub"])
        say = (f"{worst['label']} is nearly full ({pct.group(0)}): that is where the space went." if pct
               else f"{worst['label']} is nearly full: that is where the space went.")
    else:
        say = "Nothing is close to full."
    spec = {"shape": "layers", "title": TITLES["disks"], "nodes": nodes, "links": links, "highlight": full, "say": say}
    return _result("disks", spec, facts)


# ---------------------------------------------------------------- sound

def parse_pw_dump(dump: list | None) -> dict:
    """`pw-dump`: the speakers (sinks), the streams playing, which stream goes where, the default."""
    dump = dump if isinstance(dump, list) else []
    nodes: dict[int, dict] = {}
    links: list[tuple[int, int]] = []
    default = ""
    for o in dump:
        if not isinstance(o, dict):
            continue
        t = str(o.get("type", ""))
        info = o.get("info") or {}
        if t.endswith("Interface:Node"):
            nodes[o.get("id")] = {"props": info.get("props") or {}, "state": info.get("state"),
                                  "params": info.get("params") or {}}
        elif t.endswith("Interface:Link"):
            try:
                links.append((int(info["output-node-id"]), int(info["input-node-id"])))
            except (KeyError, TypeError, ValueError):
                pass
        elif t.endswith("Interface:Metadata"):
            for m in o.get("metadata") or []:
                if m.get("key") == "default.audio.sink":
                    v = m.get("value")
                    default = (v.get("name") if isinstance(v, dict) else str(v or "")) or ""
    sinks = {i: n for i, n in nodes.items() if n["props"].get("media.class") == "Audio/Sink"}
    streams = {i: n for i, n in nodes.items() if n["props"].get("media.class") == "Stream/Output/Audio"}
    return {"sinks": sinks, "streams": streams, "links": links, "default": default}


def _volume(node: dict) -> tuple[int | None, bool]:
    for p in (node.get("params") or {}).get("Props") or []:
        if isinstance(p, dict) and ("volume" in p or "mute" in p):
            v = p.get("volume")
            return (round(float(v) ** (1 / 3) * 100) if v is not None else None), bool(p.get("mute"))
    return None, False


def capture_sound(run_: Run = run, budget: float = BUDGET) -> dict:
    """Which app plays to which speaker, and how loud, from PipeWire itself."""
    dump = _json(_within(lambda: run_(["pw-dump"], budget), budget))
    if not isinstance(dump, list):
        raise Unavailable("Could not read the sound: PipeWire did not answer.")
    pw = parse_pw_dump(dump)
    if not pw["sinks"]:
        raise Unavailable("There is no sound output right now.")
    nodes, links, facts, lit = [], [], [], []
    playing = {i: s for i, s in pw["streams"].items() if s.get("state") == "running"}
    to_sink: dict[int, list[int]] = {}
    for out, inp in pw["links"]:
        if out in playing and inp in pw["sinks"]:
            to_sink.setdefault(inp, []).append(out)
    used = [i for i in pw["sinks"] if i in to_sink or pw["sinks"][i]["props"].get("node.name") == pw["default"]]
    for sid in (used or list(pw["sinks"]))[:4]:
        p = pw["sinks"][sid]["props"]
        vol, muted = _volume(pw["sinks"][sid])
        name = str(p.get("node.description") or p.get("node.nick") or p.get("node.name") or "Speakers")
        sub = ("muted" if muted else f"{vol}%" if vol is not None else "") + (", default" if p.get("node.name") == pw["default"] else "")
        nid = f"sink{sid}"
        nodes.append({"id": nid, "label": name[:cards.MAX_LABEL], "sub": sub.strip(", "), "icon": "music",
                      "state": "warn" if muted else "ok"})
        if muted:
            lit.append(nid)
        facts.append({"key": nid, "label": name, "value": ("muted" if muted else "on") + (f", {vol}%" if vol is not None else ""),
                      "volatile": False})
        for k, stream in enumerate(to_sink.get(sid, [])[:6]):
            sp = playing[stream]["props"]
            app = str(sp.get("application.name") or sp.get("node.name") or "an app")
            aid = f"app{stream}"
            nodes.append({"id": aid, "label": app[:cards.MAX_LABEL], "sub": str(sp.get("media.name") or "")[:cards.MAX_SUB],
                          "icon": "play", "state": "active"})
            links.append({"from": aid, "to": nid})
            facts.append({"key": f"stream:{app}", "label": app, "value": "plays to " + name})
    nodes = nodes[:cards.MAX_NODES]
    kept = {n["id"] for n in nodes}
    links = [ln for ln in links if ln["from"] in kept and ln["to"] in kept]
    apps = [n for n in nodes if n["id"].startswith("app")]
    sinks = [n for n in nodes if n["id"].startswith("sink")]
    if lit and len(lit) == len(sinks):
        say = "Everything is muted."
    elif not links:
        say = "Nothing is playing right now."
    else:
        ends = {ln["to"] for ln in links}
        first = next(n for n in sinks if n["id"] in ends)
        who = [n["label"] for n in apps if {"from": n["id"], "to": first["id"]} in links]
        say = (f"{who[0]} is playing through {first['label']}" if len(who) == 1 else
               f"{len(who)} apps are playing through {first['label']}") + (f" at {first['sub'].split(',')[0]}."
                                                                             if first["sub"][:1].isdigit() else ".")
    spec = {"shape": "layers", "title": TITLES["sound"], "nodes": [{k: v for k, v in n.items() if v != ""} for n in nodes],
            "links": links, "highlight": lit, "say": say}
    return _result("sound", spec, facts)


# ---------------------------------------------------------------- screens

def capture_screens(run_: Run = run, budget: float = BUDGET) -> dict:
    """The monitors, left to right, with their real mode and scale, from Hyprland."""
    mons = _json(_within(lambda: run_(["hyprctl", "-j", "monitors"], budget), budget))
    if not isinstance(mons, list) or not mons:
        raise Unavailable("Could not read the screens: Hyprland did not answer.")
    mons = sorted((m for m in mons if isinstance(m, dict)), key=lambda m: (m.get("x", 0), m.get("y", 0)))
    nodes, facts, lit = [], [], []
    for i, m in enumerate(mons[:cards.MAX_NODES]):
        mode = f"{m.get('width')}x{m.get('height')}"
        bits = [mode, f"{round(float(m.get('refreshRate', 0)))} Hz", f"scale {float(m.get('scale', 1)):g}"]
        if m.get("disabled"):
            bits.append("off")
        nodes.append({"id": f"m{i + 1}", "label": str(m.get("name", "screen"))[:cards.MAX_LABEL],
                      "sub": " · ".join(bits), "icon": "sun",
                      "state": "active" if m.get("focused") else ("warn" if m.get("disabled") else "ok"),
                      "note": str(m.get("description") or m.get("model") or "")[:cards.MAX_SUB]})
        facts.append({"key": f"screen:{m.get('name')}", "label": str(m.get("name")),
                      "value": f"{' · '.join(bits)}, at {m.get('x', 0)},{m.get('y', 0)}"})
        if m.get("focused"):
            lit.append(f"m{i + 1}")
    say = f"{len(nodes)} screen" + ("s" if len(nodes) != 1 else "") + ", the one you are on is lit."
    spec = {"shape": "chain", "linked": False, "title": TITLES["screens"], "nodes": [{k: v for k, v in n.items() if v != ""} for n in nodes],
            "links": [], "highlight": lit, "say": say}
    return _result("screens", spec, facts)


# ---------------------------------------------------------------- the front door

def capture(kind: str, target: str = "", *, run_: Run = run, budget: float = BUDGET, provider: str = "claude",
            **kw) -> dict:
    """{"card": ..., "facts": [...]} for one part of the machine. Raises Unavailable, with one
    plain sentence, when it could not be read at all."""
    if kind == "network":
        return capture_network(run_, provider=provider, budget=budget, **kw)
    if kind == "boot":
        return capture_boot(run_, budget)
    if kind == "service":
        if not target:
            raise Unavailable("Say which service.")
        return capture_service(target, run_, budget)
    if kind == "disks":
        return capture_disks(run_, budget)
    if kind == "sound":
        return capture_sound(run_, budget)
    if kind == "screens":
        return capture_screens(run_, budget)
    raise Unavailable(f"I can draw {', '.join(KINDS)}; not {kind!r}.")


def apply_overrides(card: dict, highlight=None, say: str | None = None) -> dict:
    """The agent picks what to point at and adds one line; the picture stays the machine's."""
    card = dict(card)
    ids = {n["id"] for n in card.get("nodes", [])}
    if highlight:
        hs = [highlight] if isinstance(highlight, str) else list(highlight)
        card["highlight"] = [h for h in hs if h in ids] or card.get("highlight", [])
    if say:
        card["say"] = cards._cut(" ".join(str(say).split()), cards.MAX_SAY)
    card["text"] = cards.text_of(card)
    return card


def service_exists(name: str, run_: Run = run, budget: float = BUDGET) -> bool:
    """Is there a unit by this name? The picture words ask before "what does bluetooth need" draws."""
    unit = unit_name(name)
    if not re.fullmatch(r"[\w@.:-]{1,80}", unit):
        return False
    show = parse_show(run_(["systemctl", "show", "-p", "LoadState", unit], budget))
    return bool(show) and show[0].get("LoadState") == "loaded"


# ---------------------------------------------------------------- receipts

# What a turn can change in each part, checked before and after it. Only what a turn's steps
# touched is compared (narrate.touched), so a Wi-Fi drop is never blamed on the agent.
BEFORE_KINDS = ("network", "disks", "sound", "screens")


def snapshot(kinds=BEFORE_KINDS, provider: str = "claude", run_: Run = run, budget: float = BUDGET) -> dict[str, list | None]:
    """The facts of these parts now, side by side, without waiting past the budget. A part that
    cannot be read here (no Hyprland, no PipeWire) is None."""
    def one(kind: str):
        try:
            return capture(kind, run_=run_, budget=budget, provider=provider)["facts"]
        except Unavailable:
            return None
    return _gather({k: (lambda k=k: one(k)) for k in kinds}, budget + 0.5, _OUTER)


def snapshot_service(unit: str, run_: Run = run, budget: float = BUDGET) -> list | None:
    try:
        return capture_service(unit, run_, budget)["facts"]
    except Unavailable:
        return None

def receipt(kind: str, before: list[dict], after: list[dict], target: str = "") -> dict | None:
    """A before and after of what a turn changed in this part of the machine, or None when nothing
    that matters changed (a signal, a latency or a used-space figure moving does not count).
    The change is lit: what is gone dim, what is new orange."""
    b = {f["key"]: f for f in before or [] if not f.get("volatile")}
    a = {f["key"]: f for f in after or [] if not f.get("volatile")}
    changed = [k for k in list(b) + [k for k in a if k not in b] if (k in b) != (k in a) or b[k]["value"] != a[k]["value"]]
    if not changed:
        return None
    nodes: list[dict] = []
    for k in changed[:6]:
        old, new = b.get(k), a.get(k)
        label = (new or old)["label"][:cards.MAX_LABEL]
        if old:
            nodes.append({"id": f"b:{k}", "key": k, "side": "before", "label": label, "sub": old["value"][:cards.MAX_SUB],
                          "state": "gone"})
        if new:
            nodes.append({"id": f"a:{k}", "key": k, "side": "after", "label": label, "sub": new["value"][:cards.MAX_SUB],
                          "state": "new"})
    title = {"service": f"{unit_name(target).removesuffix('.service')}" if target else "Service"}.get(kind, TITLES.get(kind, kind))
    spec = {"shape": "compare", "title": f"{title}, before and after"[:cards.MAX_TITLE], "nodes": nodes, "links": [],
            "highlight": [n["id"] for n in nodes if n["side"] == "after"],
            "say": f"{len(changed) - 6} more changed." if len(changed) > 6 else ""}
    card, errors = cards.validate_diagram(spec)
    if card is None:
        return None
    card["receipt"] = True
    card["source"] = kind
    return card
