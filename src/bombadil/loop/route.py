"""What a turn actually did, as topics: the loop's own reading of a per-turn log's tool events.

"what's eating my ram" and "memory hog?" share no words, but both turns ran `free` and `ps`,
so both routes are {memory, processes}. That is what lets the counting join paraphrases. The
narrator's labels ("Checking memory", "Checking what is running") are too coarse for this and
are made for a line above the pill, so the loop keeps its own table: one dict of programs per
topic, then a few readers for the programs whose subcommand or argument says more (git, docker,
systemctl, a package manager, a download, a path).

A topic is a plain string: `memory`, `disk`, `packages`, or `kind:detail` (`browser:wttr.in`,
`app:passwords`, `files:downloads`, `git:status`). Anything that touched a private thing
(keys, secrets, tokens, a password app's data) is reported as the one topic `private` and
nothing else from that step, so what they hid never reaches a count.

Pure functions over the events; no I/O. One odd event costs that event, never the route.
"""

import os
import re
import shlex
from collections.abc import Iterable
from pathlib import PurePosixPath

from .. import narrate, paths

PRIVATE = "private"
OPENED = "opened"   # the turn put an app or a panel in front of them (as opposed to reading or changing its files)

# Program -> topic, one row per topic so a new program is one word in one place.
PROGRAMS: dict[str, tuple[str, ...]] = {
    "memory": ("free", "vmstat", "smem", "pmap"),
    "processes": ("ps", "top", "htop", "btop", "pgrep", "pidof", "pstree", "lsof", "fuser", "kill", "pkill",
                  "killall", "nice", "renice"),
    "disk": ("df", "du", "lsblk", "ncdu", "duf", "findmnt", "blkid", "smartctl", "fdisk", "parted",
             "mount", "umount"),
    "cpu": ("lscpu", "mpstat", "uptime", "nproc", "cpupower", "turbostat"),
    "temperature": ("sensors", "psensor"),
    "network": ("ip", "ss", "netstat", "nmcli", "nmtui", "ping", "dig", "nslookup", "host", "traceroute",
                "mtr", "iw", "iwctl", "iwconfig", "ifconfig", "resolvectl", "networkctl", "speedtest",
                "speedtest-cli", "rfkill", "wg", "wg-quick", "tailscale"),
    "bluetooth": ("bluetoothctl",),
    "packages": ("pacman", "yay", "paru", "pikaur", "makepkg", "flatpak", "pip", "pip3", "pipx", "uv", "npm",
                 "pnpm", "yarn", "cargo", "gem", "snap"),
    "docker": ("docker", "podman", "docker-compose", "podman-compose"),
    "git": ("git", "gh", "glab"),
    "services": ("systemctl", "journalctl", "service", "loginctl", "systemd-analyze"),
    "logs": ("dmesg",),
    "timers": ("crontab", "at", "atq", "atrm"),
    "time": ("date", "timedatectl", "cal", "hwclock", "chronyc"),
    "battery": ("upower", "acpi", "tlp", "tlp-stat", "powertop"),
    "sound": ("wpctl", "pactl", "amixer", "pamixer", "playerctl", "pw-cli", "pw-top"),
    "display": ("brightnessctl", "light", "xrandr", "wlr-randr", "gammastep", "redshift", "ddcutil"),
    "screen": ("grim", "slurp", "hyprshot", "wf-recorder"),
    "desktop": ("hyprctl",),
    "notify": ("notify-send",),
    "search": ("locate", "plocate", "updatedb", "whereis", "which"),
}
_PROGRAM = {prog: topic for topic, progs in PROGRAMS.items() for prog in progs}

# Programs whose arguments are files: their paths say which part of the home folder was touched.
FILE_PROGRAMS = {
    "ls", "find", "fd", "tree", "cat", "head", "tail", "less", "more", "bat", "wc", "file", "stat", "mv", "cp",
    "rm", "rmdir", "mkdir", "touch", "ln", "rsync", "du", "ncdu", "tar", "zip", "unzip", "7z", "sort", "grep",
    "rg", "ag", "awk", "sed", "tee", "chmod", "chown", "trash", "trash-put", "gio", "realpath", "fdupes",
    "sha256sum", "md5sum", "xdg-open", "open", "exa", "eza", "lsd", "diff", "cmp",
}
# What a file program does with its files: to tell "find my tax pdf" from "how many files are in it".
FILE_KINDS = {
    "search": {"find", "fd", "rg", "grep", "ag", "egrep", "fgrep", "fdupes", "locate", "plocate"},
    "list": {"ls", "tree", "exa", "eza", "lsd", "stat", "wc", "file", "realpath"},
    "read": {"cat", "head", "tail", "less", "more", "bat", "nl", "diff", "cmp", "sha256sum", "md5sum"},
}
_FILE_KIND = {prog: kind for kind, progs in FILE_KINDS.items() for prog in progs}
_TOOL_KINDS = {"Read": "read", "NotebookRead": "read", "Glob": "search", "Grep": "search", "LS": "list"}
# Steps that only help the real one along: a screenshot to check, a notice to say it is done.
SIDE_TOPICS = frozenset(("notify", "screen", "apps", "card"))
# Their first plain argument is a pattern or a script, not a path.
_PATTERN_FIRST = {"grep", "rg", "ag", "sed", "awk", "egrep", "fgrep"}

# Said by the shell and the tools around it, never a thing the turn was about.
_NOISE = {
    "cd", "echo", "printf", "true", "false", ":", "test", "[", "exit", "export", "set", "unset", "source", ".",
    "read", "env", "sleep", "wait", "time", "python", "python3", "node", "bash", "sh", "zsh", "dash", "jq", "cut",
    "tr", "uniq", "xargs", "column", "paste", "yes", "seq", "basename", "dirname", "pwd", "id", "whoami",
    "sudo", "command", "type", "hostname", "uname", "clear", "tput", "lua", "perl", "ruby", "deno", "bun",
    "bombadil", "bombadil-app", "bombadil-os-mcp", "nl", "tac", "rev", "fold", "expand", "printenv", "timeout",
}

# Browsers: a URL names the host; with none, just the browser.
BROWSERS = {"chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "firefox",
            "brave", "brave-browser", "epiphany", "qutebrowser"}
DOWNLOADERS = {"curl", "wget", "aria2c", "xh", "http", "https", "httpie"}
# A host the answer is about more than the host itself.
HOST_TOPICS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(("wttr.in", "api.open-meteo.com", "open-meteo.com", "api.openweathermap.org",
                     "openweathermap.org", "weather.com", "yr.no", "api.met.no"), ("weather",)),
    **dict.fromkeys(("ifconfig.me", "icanhazip.com", "api.ipify.org", "ipinfo.io"), ("network", "network:addr")),
    **dict.fromkeys(("1.1.1.1", "8.8.8.8", "example.com", "connectivitycheck.gstatic.com"),
                    ("network", "network:reach")),
}

# Programs that say more than their topic: what kind of network, sound or display question it was.
# Two asks about the network are not the same ask when one is about ports and the other about the wifi.
NETWORK_KINDS = {
    "ss": "ports", "netstat": "ports", "ip": "addr", "ifconfig": "addr",
    "iw": "wifi", "iwctl": "wifi", "iwconfig": "wifi", "rfkill": "wifi", "ping": "reach", "dig": "reach",
    "nslookup": "reach", "host": "reach", "traceroute": "reach", "mtr": "reach", "resolvectl": "reach",
    "speedtest": "speed", "speedtest-cli": "speed", "wg": "vpn", "wg-quick": "vpn", "tailscale": "vpn",
    "networkctl": "link", "nmtui": "link",
}
SOUND_KINDS = {"wpctl": "volume", "pactl": "volume", "amixer": "volume", "pamixer": "volume",
               "playerctl": "player"}
DISPLAY_KINDS = {"brightnessctl": "brightness", "light": "brightness", "ddcutil": "brightness",
                 "gammastep": "color", "redshift": "color", "xrandr": "monitors", "wlr-randr": "monitors"}

# Topics whose answer changes within the hour: asked again and again, worth a card that updates.
LIVE = {"memory", "processes", "cpu", "temperature", "network", "battery", "services", "docker", "weather",
        "sound", "time"}

# Area of the machine a path belongs to, when it is not in the home folder.
_SYSTEM_AREAS = (
    ("/proc/meminfo", "memory"), ("/proc/cpuinfo", "cpu"), ("/proc/loadavg", "cpu"), ("/proc/stat", "cpu"),
    ("/proc/uptime", "cpu"), ("/proc/net", "network"), ("/sys/class/power_supply", "battery"),
    ("/sys/class/backlight", "display"), ("/sys/class/thermal", "temperature"), ("/sys/class/hwmon", "temperature"),
    ("/sys/class/net", "network"), ("/var/log", "logs"), ("/etc", "files:etc"), ("/usr", "files:system"),
    ("/opt", "files:system"), ("/var", "files:system"), ("/srv", "files:system"), ("/boot", "files:system"),
    ("/mnt", "files:mounts"), ("/media", "files:mounts"), ("/run/media", "files:mounts"),
)

# What makes a step private. Checked on the command, the paths and the address a step was given.
PRIVATE_RE = re.compile(
    r"\.ssh\b|\.gnupg|\bgpg2?\b|key-?ring|keychain|kwallet|secret|token|api[_ -]?key|\.env\b|id_rsa|id_ed25519|"
    r"credential|\.netrc|\.aws\b|/etc/(shadow|passwd)|password[-_ ]store|\bpass\s+(show|insert|generate|edit)\b|"
    r"passphrase|\bpsk\b|wireless-security|show-secrets|\bnmcli\b.*\s-s\b|"
    r"bitwarden|keepass|\.kdbx|seed phrase|private[_ -]?key|\.pem\b|\.p12\b|\.pfx\b", re.IGNORECASE)
# A password app's own code is not private; its data is.
_PASSWORD_APP_RE = re.compile(r"pass(word|wd)|vault|keep|wallet|secret|login", re.IGNORECASE)
_CODE_SUFFIXES = (".qml", ".py", ".toml", ".md", ".txt~", ".pyc")


# -- the events --

def route(events: Iterable[dict]) -> list[str]:
    """The topics a turn touched, in the order it first touched them, each once. `events` are a
    per-turn log's events (or a provider's): only tool calls and file changes are read."""
    topics: list[str] = []
    for ev in events or ():
        for t in _safely(ev):
            if t not in topics:
                topics.append(t)
    main = [t for t in topics if t not in SIDE_TOPICS]
    return main or topics


def _safely(ev) -> list[str]:
    try:
        return _event(ev) if isinstance(ev, dict) else []
    except Exception:  # noqa: BLE001 - one odd event costs that event, never the route
        return []


def is_private(topics: Iterable[str]) -> bool:
    return PRIVATE in topics


def is_live(topics: Iterable[str]) -> bool:
    """Does the answer to what these topics read change within the hour?"""
    return any(t in LIVE for t in topics)


def opens_thing(topics: Iterable[str]) -> str:
    """The one app or panel a turn only opened or showed (`app:passwords`), else ''. Reading or writing
    an app's files is not an open, and neither is anything done besides it; a screenshot or a notice
    does not make it more than one."""
    topics = [t for t in topics if t not in SIDE_TOPICS]
    things = [t for t in topics if t.startswith(("app:", "panel:"))]
    return things[0] if OPENED in topics and len(topics) == 2 and len(things) == 1 else ""


def _event(ev: dict) -> list[str]:
    kind = ev.get("kind")
    if kind == "file_change":
        paths_ = [str(c.get("path", "")) for c in ev.get("changes") or [] if isinstance(c, dict)]
        return _private_or([_area(p) for p in paths_], " ".join(paths_))
    if kind != "tool":
        return []
    name = str(ev.get("name") or "")
    a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
    if _touches_private(name, a):
        return [PRIVATE]
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        return _os_tool(tool, a) if "bombadil" in server else []
    if name in BASH_NAMES:
        return _bash(_command(a))
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS"):
        area = _area(str(a.get("file_path") or a.get("notebook_path") or a.get("path") or
                         (a.get("pattern") if name == "Glob" else "") or ""))
        kind = _TOOL_KINDS.get(name)
        return _listed(area) + ([f"kind:{kind}"] if area and area.startswith(("files:", "app:")) and kind else [])
    if name == "WebFetch":
        return _host_topic(str(a.get("url", "")))
    if name == "WebSearch":
        return ["web"]
    if name in OS_TOOL_NAMES:
        return _os_tool(name, a)
    return []


# Codex and the providers' other spellings of "run a shell command".
BASH_NAMES = {"Bash", "shell", "exec", "exec_command", "local_shell", "container.exec", "bash"}


def _command(a: dict) -> str:
    c = a.get("command") if "command" in a else a.get("cmd")
    if isinstance(c, list):
        return shlex.join(str(x) for x in c)
    return str(c or "")


def _listed(topic: str | None) -> list[str]:
    return [topic] if topic else []


def _private_or(found: list, text: str) -> list[str]:
    if PRIVATE_RE.search(text):
        return [PRIVATE]
    return [t for t in found if t]


# Only what a step was pointed at is read for private things, never the text it wrote: an app
# that manages passwords has the word in its own code.
_POINTERS = ("command", "cmd", "file_path", "path", "notebook_path", "pattern", "url", "query", "changes")


def _touches_private(name: str, a: dict) -> bool:
    text = " ".join(str(a[k]) for k in _POINTERS if k in a)
    if PRIVATE_RE.search(text):
        return True
    path = str(a.get("file_path") or a.get("path") or a.get("notebook_path") or "")
    if path and _is_password_data(path):
        return True
    cmd = _command(a) if name in BASH_NAMES else ""
    return any(_is_password_data(w) for w in re.findall(r"[~/][^\s'\";|&<>]+", cmd))


def _is_password_data(path: str) -> bool:
    """A file inside a password app's folder that is not the app's own code: what it holds."""
    p = PurePosixPath(os.path.normpath(narrate._expand_home(path)))
    try:
        rel = p.relative_to(paths.apps_dir())
    except ValueError:
        return False
    return (len(rel.parts) > 1 and bool(_PASSWORD_APP_RE.search(rel.parts[0]))
            and not rel.name.lower().endswith(_CODE_SUFFIXES))


# -- os-mcp --

OS_TOOL_NAMES = {"show_panel", "hide_panel", "create_app", "open_app", "show_app", "hide_app", "close_app",
                 "check_app", "app_status", "list_apps", "screenshot", "notify", "show_card", "open_url"}


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:40]


def _os_tool(tool: str, a: dict) -> list[str]:
    name = str(a.get("name") or "")
    if tool in ("show_panel", "hide_panel"):
        return [f"panel:{name}", *([OPENED] if tool == "show_panel" else [])] if name else ["panel"]
    if tool == "create_app":
        return [f"app:{_slug(a.get('title') or '')}"] if _slug(a.get("title") or "") else ["app"]
    if tool in ("open_app", "show_app", "hide_app", "close_app", "check_app", "app_status"):
        shown = [OPENED] if tool in ("open_app", "show_app") else []
        return [f"app:{_slug(name)}", *shown] if _slug(name) else ["app"]
    if tool == "list_apps":
        return ["apps"]
    if tool == "screenshot":
        return ["screen"]
    if tool == "notify":
        return ["notify"]
    if tool == "show_card":
        return ["card"]
    if tool == "open_url":
        return _host_topic(str(a.get("url", "")))
    return []   # app_guide, snapshot, rollback and the rest are the agent's own plumbing


# -- shell commands --

def _bash(command: str) -> list[str]:
    out: list[str] = []
    cwd: str | None = None
    for argv in narrate._segments(narrate._unwrap(command)):
        words, writes = narrate._strip_redirects(argv)
        argv, _sudo = narrate._strip_wrappers(words)
        for target in writes:
            if not target.startswith("/dev/"):
                out += _listed(_area(target))
        if not argv:
            continue
        argv = _unparen(argv)
        if not argv:
            continue
        prog = PurePosixPath(argv[0]).name
        if prog == "cd":
            cwd = _cd(argv, cwd)
            continue
        script = narrate._shell_script(argv)
        if script is not None:
            out += _bash(script)
            continue
        out += _program(prog, argv, cwd)
    return out


def _unparen(argv: list[str]) -> list[str]:
    """`(sleep 60; ...)` and `{ cmd; }` are still the command inside."""
    argv = [argv[0].lstrip("({"), *argv[1:]]
    if not argv[0]:
        argv = argv[1:]
    if argv and argv[-1].rstrip(")}") != argv[-1]:
        argv[-1] = argv[-1].rstrip(")}")
        argv = [a for a in argv if a]
    return argv


def _cd(argv: list[str], cwd: str | None) -> str | None:
    to = narrate._expand_home(argv[1]) if len(argv) > 1 else str(paths.home())
    if to.startswith("/"):
        return os.path.normpath(to)
    if cwd and not to.startswith(("$", "-", "~")):
        return os.path.normpath(os.path.join(cwd, to))
    return None


def _plain(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if not a.startswith("-")]


def _program(prog: str, argv: list[str], cwd: str | None) -> list[str]:
    handler = _HANDLERS.get(prog)
    if handler is not None:
        return handler(argv, cwd)
    if prog.startswith("python") and argv[1:3] == ["-m", "pip"]:
        return _packages(["pip", *argv[3:]], cwd)
    if prog in BROWSERS:
        return _browser(argv, cwd)
    if prog in DOWNLOADERS:
        return _host_topic(narrate._url_arg(argv))
    if prog in ("python", "python3", "bash", "sh", "zsh", "node", "deno", "bun", "lua", "perl", "ruby"):
        return _script(argv)
    topics = [_PROGRAM[prog]] if prog in _PROGRAM else []
    kind = {"network": NETWORK_KINDS, "sound": SOUND_KINDS, "display": DISPLAY_KINDS}.get(
        topics[0] if topics else "", {}).get(prog)
    if kind:
        topics.append(f"{topics[0]}:{kind}")
    if prog in FILE_PROGRAMS:
        topics += _file_areas(prog, argv, cwd)
    if not topics and prog not in _NOISE and prog not in FILE_PROGRAMS:
        topics = [f"run:{prog}"]
    return topics


def _file_areas(prog: str, argv: list[str], cwd: str | None) -> list[str]:
    args = _plain(argv)
    if prog in _PATTERN_FIRST:
        args = args[1:]
    found = [_area(a) for a in args if _pathlike(a)]
    if not any(found) and cwd:
        found = [_area(cwd)]
    areas = [t for t in dict.fromkeys(found) if t]
    kind = _FILE_KIND.get(prog)
    mine = any(t.startswith(("files:", "app:")) for t in areas)   # `cat /proc/meminfo` is memory, whatever the verb
    return areas + [f"kind:{kind}"] if mine and kind else areas


def _pathlike(a: str) -> bool:
    return a.startswith(("/", "~", "./", "../", "$HOME", "${HOME}")) and not a.startswith("/dev/")


def _area(path: str) -> str | None:
    """The part of the machine a path is in: `files:downloads`, `app:tracker`, or `memory` for
    /proc/meminfo. None for scratch space and what names no place."""
    p = narrate._expand_home(str(path).strip().strip("'\""))
    if not p or p.startswith(("-", "$")) or "://" in p:
        return None
    home = str(paths.home())
    if not p.startswith("/"):
        p = os.path.join(home, p)
    p = os.path.normpath(p)
    try:
        rel = PurePosixPath(p).relative_to(paths.apps_dir())
        if rel.parts:
            return f"app:{_slug(rel.parts[0])}"
    except ValueError:
        pass
    if p == home:
        return "files:home"
    if p.startswith(home + "/"):
        parts = p[len(home) + 1:].split("/")
        if len(parts) == 1 and "." in parts[0][1:]:
            return "files:home"   # a file in the home folder itself, not a folder of that name
        first = parts[0].lstrip(".").lower()
        if any(c in first for c in "*?["):
            return "files:home"   # `~/*`: the folder itself, whatever is in it
        return f"files:{first}" if first else None
    for prefix, topic in _SYSTEM_AREAS:
        if p == prefix or p.startswith(prefix + "/"):
            return topic
    return None


def _host_topic(url: str) -> list[str]:
    host = narrate._host(url)
    if not host:
        return ["browser"]
    return list(HOST_TOPICS.get(host, (f"browser:{host}",)))


def _browser(argv: list[str], cwd: str | None) -> list[str]:
    url = next((a for a in _plain(argv) if "://" in a or a.startswith("www.")), "")
    return _host_topic(url) if url else ["browser"]


def _script(argv: list[str]) -> list[str]:
    """`python ~/bin/batch_status.py`: the script's name says what was run. An inline script
    or a heredoc says nothing, and one in /tmp is the agent's scratch."""
    script = next((a for a in argv[1:] if not a.startswith("-") and a.endswith((".py", ".sh", ".js", ".lua"))), "")
    where = narrate._expand_home(script)
    if not script or (where.startswith("/tmp/") and not where.startswith(f"{paths.home()}/")):
        return []
    return [f"script:{PurePosixPath(script).name}"]


# -- programs that say more in their arguments --

def _ps(argv: list[str], cwd: str | None) -> list[str]:
    said = " ".join(argv[1:]).lower()
    out = ["processes"]
    if re.search(r"mem|rss|vsz", said):
        out.append("memory")
    if "cpu" in said:
        out.append("cpu")
    return out


def _git(argv: list[str], cwd: str | None) -> list[str]:
    if PurePosixPath(argv[0]).name != "git":   # `gh pr list` and `gh issue list` are not one ask
        args = _plain(argv)
        return ["git", f"git:{args[0]}"] if args else ["git"]
    sub, _ = narrate._git_sub(argv)
    return ["git", f"git:{sub}"] if sub else ["git"]


def _docker(argv: list[str], cwd: str | None) -> list[str]:
    args = _plain(argv)
    if PurePosixPath(argv[0]).name.endswith("-compose"):
        args = ["compose", *args]
    sub = "-".join(args[:2]) if args[:1] == ["compose"] else (args[0] if args else "")
    return ["docker", f"docker:{sub}"] if sub else ["docker"]


def _systemctl(argv: list[str], cwd: str | None) -> list[str]:
    prog = PurePosixPath(argv[0]).name
    args = _plain(argv)
    if prog == "journalctl":
        unit = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("-u", "--unit")), "")
        return ["services", f"services:{narrate._unit(unit).lower()}"] if unit else ["services"]
    if prog != "systemctl":
        return ["services"]
    units = args[1:]
    if args[:1] == ["list-timers"] or any(u.endswith(".timer") for u in units):
        return ["timers"]
    return ["services", f"services:{narrate._unit(units[0]).lower()}"] if units else ["services"]


def _packages(argv: list[str], cwd: str | None) -> list[str]:
    prog = PurePosixPath(argv[0]).name
    rest = argv[1:]
    if prog == "uv" and rest[:1] == ["pip"]:
        rest = rest[1:]
    if prog in ("pacman", "yay", "paru", "pikaur"):
        names = [a for a in rest if not a.startswith("-")]
    else:
        verbs = ("install", "i", "add", "remove", "uninstall", "rm", "update", "upgrade", "info", "show")
        start = next((i for i, a in enumerate(rest) if a in verbs), None)
        names = [a for a in rest[start + 1:] if not a.startswith("-") and a != "flathub" and "/" not in a
                 and not a.startswith(".")] if start is not None else []
    names = [n.removesuffix(".pkg.tar.zst") for n in names]
    return ["packages", *(f"packages:{n.lower()}" for n in names[:3])]


def _systemd_run(argv: list[str], cwd: str | None) -> list[str]:
    return ["timers"] if any(a.startswith("--on") for a in argv) else ["services"]


def _sleep(argv: list[str], cwd: str | None) -> list[str]:
    """A long `sleep` followed by something is how a timer gets made in a shell."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", argv[1]) if len(argv) > 1 else None
    if m and float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)] >= 30:
        return ["timers"]
    return []


def _hyprctl(argv: list[str], cwd: str | None) -> list[str]:
    args = _plain(argv)
    if args[:1] == ["monitors"]:
        return ["display", "display:monitors"]
    return ["desktop", f"desktop:{args[0]}"] if args else ["desktop"]


def _mixer(argv: list[str], cwd: str | None) -> list[str]:
    """Volume and mute are one ask (the level), choosing where the sound goes is another."""
    args = _plain(argv)
    sub = args[0].lower() if args else ""   # the subcommand: `set-volume` and `set-mute` act on the default sink too
    return ["sound", "sound:output" if "default" in sub or "move-sink-input" in sub else "sound:volume"]


def _player(argv: list[str], cwd: str | None) -> list[str]:
    """`playerctl next` and `playerctl metadata` are not the same ask: the subcommand says which."""
    args = _plain(argv)
    return ["sound", f"sound:{args[0]}"] if args else ["sound"]


def _bluetooth(argv: list[str], cwd: str | None) -> list[str]:
    args = _plain(argv)
    sub = {"devices": "show", "info": "show", "paired-devices": "show"}.get(args[0], args[0]) if args else ""
    return ["bluetooth", f"bluetooth:{sub}"] if sub else ["bluetooth"]


def _kill(argv: list[str], cwd: str | None) -> list[str]:
    """Closing firefox and closing the night light are two asks; what was closed says which."""
    names = [a.lower() for a in _plain(argv) if not a.isdigit()]
    return ["processes", f"processes:{names[0]}"] if names else ["processes"]


def _nmcli(argv: list[str], cwd: str | None) -> list[str]:
    """`nmcli dev wifi` is the wifi, `nmcli general` is whether it is up, the rest is the link."""
    args = _plain(argv)
    kind = "wifi" if {"wifi", "radio"} & set(args[:3]) else "reach" if args[:1] in (["general"], ["networking"],
                                                                                   ["connectivity"]) else "link"
    return ["network", f"network:{kind}"]


def _bombadil_app(argv: list[str], cwd: str | None) -> list[str]:
    args = _plain(argv)
    name = _slug(args[1]) if len(args) > 1 else ""
    if not (args[:1] and args[0] in ("run", "show", "check", "status", "close", "hide") and name):
        return []
    return [f"app:{name}", *([OPENED] if args[0] in ("run", "show") else [])]


def _du(argv: list[str], cwd: str | None) -> list[str]:
    return ["disk", *_file_areas("du", argv, cwd)]


_HANDLERS = {
    "ps": _ps, "top": _ps, "htop": _ps, "btop": _ps,
    "git": _git, "gh": _git, "glab": _git,
    "docker": _docker, "podman": _docker, "docker-compose": _docker, "podman-compose": _docker,
    "systemctl": _systemctl, "journalctl": _systemctl, "service": _systemctl, "loginctl": _systemctl,
    "systemd-analyze": _systemctl,
    "systemd-run": _systemd_run, "sleep": _sleep, "hyprctl": _hyprctl, "bombadil-app": _bombadil_app,
    "du": _du, "ncdu": _du, "nmcli": _nmcli, "playerctl": _player, "bluetoothctl": _bluetooth,
    "pkill": _kill, "killall": _kill, "kill": _kill,
    **dict.fromkeys(("wpctl", "pactl", "amixer", "pamixer"), _mixer),
    **{p: _packages for p in PROGRAMS["packages"]},
}
