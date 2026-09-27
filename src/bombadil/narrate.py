"""Plain words for what the agent is doing: the live line above the pill.

agentd feeds every provider event of a turn to a `Narrator`, which answers with the line
to show ("Installing ffmpeg", "Building Passwords, 84 lines") and, when the turn ends, one
sentence for what it changed ("Installed ffmpeg and made Passwords."). A step that touches
the system (sudo, pacman, /etc, a service) is marked "system" and carries the exact
command; a step no restore point can undo (formatting a disk, deleting files in your home
folder) is marked "irreversible". Nothing here pauses or asks: the marks are only shown.

Pure functions over the events, no I/O except checking whether an app folder exists, so the
rule table is cheap to test and to extend.
"""

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import paths

SYSTEM = "system"
IRREVERSIBLE = "irreversible"


@dataclass
class Step:
    text: str                  # present tense for the live line: "Installing ffmpeg"
    done: str | None = None    # past tense for the closing sentence: "Installed ffmpeg"; None = not a change
    risk: str | None = None    # None, SYSTEM or IRREVERSIBLE
    command: str | None = None  # the exact command, shown under a marked step

    @property
    def changes(self) -> bool:
        return self.done is not None


# -- words --

PANEL_WORDS = {"browser": "the browser", "terminal": "the terminal", "files": "Files"}

# Verbs Claude and Codex start step descriptions with ("Install ffmpeg package").
_VERBS = {
    "add", "apply", "back", "build", "capture", "change", "check", "clean", "clone", "close", "commit",
    "compile", "compress", "configure", "connect", "convert", "copy", "count", "create", "delete",
    "disable", "display", "download", "edit", "enable", "extract", "fetch", "find", "fix", "format",
    "generate", "get", "grep", "inspect", "install", "kill", "launch", "lint", "list", "load", "look",
    "make", "mount", "move", "open", "print", "pull", "push", "query", "read", "reload", "remove",
    "rename", "restart", "run", "save", "scan", "search", "set", "show", "start", "stop", "sync",
    "take", "test", "uninstall", "unmount", "unpack", "update", "upgrade", "verify", "view", "wait",
    "write", "use", "prepare", "try", "reinstall", "refresh", "resize", "replace", "tail",
}
_DOUBLE = {"run", "set", "get", "put", "stop", "cut", "begin", "plan", "ship", "drop", "grep", "commit",
           "submit", "wrap", "scan", "tap", "log", "pin", "zip", "unzip", "chat", "dig", "map", "swap", "strip"}


def gerund(verb: str) -> str:
    v = verb.lower()
    if v in _DOUBLE:
        return v + v[-1] + "ing"
    if v.endswith("ie"):
        return v[:-2] + "ying"
    if v.endswith("e") and not v.endswith(("ee", "ye", "oe")) and len(v) > 2:
        return v[:-1] + "ing"
    return v + "ing"


def from_description(desc: str) -> str | None:
    """'Install ffmpeg package.' -> 'Installing ffmpeg package'; None if it does not start with a verb."""
    desc = " ".join(str(desc).split()).rstrip(".")
    if not desc:
        return None
    first, _, rest = desc.partition(" ")
    if first.lower() in _VERBS:
        return (gerund(first).capitalize() + (" " + rest if rest else ""))[:90]
    return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _quote(s: str, n: int = 40) -> str:
    s = " ".join(str(s).split())
    return "“" + (s if len(s) <= n else s[: n - 1] + "…") + "”"


def _name(path: str) -> str:
    p = PurePosixPath(str(path).rstrip("/"))
    return p.name or str(path)


def _names(items: list[str], n: int = 2) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) <= n:
        return " and ".join(items)
    return f"{', '.join(items[:n])} and {len(items) - n} more"


def _lines(*texts) -> int:
    return sum(t.count("\n") + (0 if t.endswith("\n") else 1) for t in texts if isinstance(t, str) and t)


def _app_title(title: str) -> str:
    return " ".join(str(title).split())[:40] or "an app"


def _app_exists(title: str) -> bool:
    from . import apps
    try:
        return (paths.apps_dir() / apps.slug(title) / "main.qml").exists()
    except ValueError:
        return False


# -- shell commands --

SYSTEM_PATHS = ("/etc", "/usr", "/boot", "/efi", "/opt", "/var", "/srv", "/lib", "/bin", "/sbin", "/root")
_PKG = {"pacman", "yay", "paru", "pikaur"}
_TRIVIAL = {"cd", "export", "set", "true", "false", ":", "source", ".", "echo", "printf", "unset", "local",
            "test", "[", "exit", "read"}
_WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "nice", "ionice", "stdbuf", "timeout", "exec", "command",
             "builtin", "setsid", "unbuffer", "script"}


def _unwrap(command: str) -> str:
    """Codex runs `bash -lc '<cmd>'`; show and read the inner command."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if len(argv) >= 3 and PurePosixPath(argv[0]).name in ("bash", "sh", "zsh", "dash") and argv[1] in ("-lc", "-c", "-l -c"):
        return argv[2]
    return command


def _split(command: str) -> list[str]:
    """Split on ; && || | & and newlines outside quotes (`sh -c 'a && b'` stays whole, 2>&1 too)."""
    parts: list[str] = []
    cur: list[str] = []
    quote = None
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                cur.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < n:
            cur += [c, command[i + 1]]
            i += 1
        elif command[i:i + 2] in ("&&", "||"):
            parts.append("".join(cur))
            cur = []
            i += 1
        elif c in ";|\n" or (c == "&" and command[i - 1:i] not in ("<", ">") and command[i + 1:i + 2] != ">"):
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _segments(command: str) -> list[list[str]]:
    out = []
    for part in _split(command.strip()):
        if not part:
            continue
        try:
            argv = shlex.split(part, comments=True)
        except ValueError:
            argv = part.split()
        if argv:
            out.append(argv)
    return out


def _strip_wrappers(argv: list[str]) -> tuple[list[str], bool]:
    """Drop sudo/env/timeout and VAR=value prefixes; say whether sudo was one of them."""
    sudo = False
    i = 0
    while i < len(argv):
        a = argv[i]
        base = PurePosixPath(a).name
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a):
            i += 1
        elif base in _WRAPPERS:
            sudo = sudo or base in ("sudo", "doas")
            i += 1
            # skip the wrapper's own options (sudo -u root, timeout 30, env -i, nice -n 5)
            while i < len(argv) and (argv[i].startswith("-") or (base == "timeout" and re.match(r"^\d", argv[i]))):
                if base == "sudo" and argv[i] in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"):
                    i += 1
                if base == "nice" and argv[i] == "-n":
                    i += 1
                i += 1
        else:
            break
    return argv[i:], sudo


def _args(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if not a.startswith("-")]


def _writes_redirect(segment: str) -> list[str]:
    return re.findall(r">>?\s*([^\s;&|]+)", segment)


def _is_system_path(p: str) -> bool:
    return any(p == s or p.startswith(s + "/") for s in SYSTEM_PATHS)


def _is_home_path(p: str) -> bool:
    home = str(paths.home())
    p = os.path.expanduser(p)
    return (p.startswith("~") or p == home or p.startswith(home + "/")
            or (not p.startswith("/") and not p.startswith("$")))


def _pkg_step(prog: str, argv: list[str]) -> Step | None:
    flags = "".join(a[1:] for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
    long = {a for a in argv[1:] if a.startswith("--")}
    pkgs = [a for a in _args(argv) if not a.startswith("/") or a.endswith(".pkg.tar.zst")]
    names = [_name(p).split("-")[0] if p.endswith(".pkg.tar.zst") else p for p in pkgs]
    op = argv[1][1:2] if len(argv) > 1 and argv[1].startswith("-") and not argv[1].startswith("--") else ""
    if "--sync" in long:
        op = "S"
    elif "--remove" in long:
        op = "R"
    elif "--upgrade" in long:
        op = "U"
    elif "--query" in long:
        op = "Q"
    aur = " from the AUR" if prog in ("yay", "paru", "pikaur") else ""
    if op in ("S", "U") and ("s" in flags or "i" in flags or "--search" in long or "--info" in long) and op == "S":
        what = _quote(" ".join(pkgs)) if pkgs else "packages"
        return Step(f"Looking up {what}")
    if op in ("S", "U") and names:
        n = _names(names)
        return Step(f"Installing {n}{aur}", f"Installed {n}")
    if op == "S" and "u" in flags:
        return Step("Updating the system", "Updated the system")
    if op == "S" and "y" in flags:
        return Step("Refreshing the package lists")
    if op == "S" and "c" in flags:
        return Step("Clearing the package cache", "Cleared the package cache")
    if op == "R" and names:
        n = _names(names)
        return Step(f"Removing {n}", f"Removed {n}")
    if op in ("Q", "F", "T", "D"):
        return Step("Checking installed packages")
    if not op and prog in ("yay", "paru") and not pkgs:
        return Step("Updating the system", "Updated the system")
    return None


def _unit(u: str) -> str:
    return u.removesuffix(".service").removesuffix(".socket").removesuffix(".timer")


def _systemctl_step(argv: list[str]) -> Step | None:
    args = _args(argv)
    user = "--user" in argv
    if not args:
        return Step("Checking services")
    verb, units = args[0], [_unit(u) for u in args[1:]]
    n = _names(units)
    now = "--now" in argv
    words = {
        "start": ("Starting", "Started"), "stop": ("Stopping", "Stopped"),
        "restart": ("Restarting", "Restarted"), "reload": ("Reloading", "Reloaded"),
        "try-restart": ("Restarting", "Restarted"), "reload-or-restart": ("Restarting", "Restarted"),
        "enable": ("Turning on", "Turned on") if now else ("Enabling", "Enabled"),
        "disable": ("Turning off", "Turned off") if now else ("Disabling", "Disabled"),
        "mask": ("Blocking", "Blocked"), "unmask": ("Unblocking", "Unblocked"),
    }
    if verb in ("reboot",):
        return Step("Restarting the computer", "Restarted the computer")
    if verb in ("poweroff", "halt"):
        return Step("Shutting down", "Shut down")
    if verb in ("suspend", "hibernate"):
        return Step("Going to sleep")
    if verb == "daemon-reload":
        return Step("Reloading service settings", "Reloaded service settings")
    if verb in words and n:
        now_w, past = words[verb]
        return Step(f"{now_w} {n}", f"{past} {n}", None if user else SYSTEM)
    if verb in ("status", "is-active", "is-enabled", "list-units", "list-unit-files", "show", "cat"):
        return Step(f"Checking {n}" if n else "Checking services")
    return None


def _irreversible(prog: str, argv: list[str], segment: str) -> bool:
    if prog.startswith("mkfs") or prog in ("wipefs", "blkdiscard", "shred", "mkswap"):
        return True
    if prog == "dd" and any(a.startswith("of=/dev/") for a in argv):
        return True
    if prog in ("sgdisk", "sfdisk", "gdisk") and any(a in ("-Z", "--zap-all", "-o", "--clear", "-z", "--zap",
                                                            "--delete") or a.startswith("-d") for a in argv[1:]):
        return True
    if prog == "parted" and re.search(r"\b(mklabel|mktable|rm)\b", segment):
        return True
    if prog == "cryptsetup" and re.search(r"\b(luksFormat|erase|luksErase)\b", segment):
        return True
    if prog in ("rm", "find") and (prog == "rm" or "-delete" in argv):
        targets = _args(argv) if prog == "rm" else argv[1:2]
        # Home is not in the restore points yet, so deleting there cannot be undone.
        return any(_is_home_path(t) and not t.startswith(("/tmp", "/var/tmp")) for t in targets)
    if prog == "git" and re.search(r"\b(push\s+(-f|--force)|reset\s+--hard|clean\s+-\w*f)", segment):
        return True
    return False


def _command_step(argv: list[str], segment: str, description: str | None) -> Step | None:
    """The step for one simple command, or None when it is not one we have words for."""
    prog = PurePosixPath(argv[0]).name
    args = _args(argv)
    a0 = args[0] if args else ""
    if prog in _PKG:
        return _pkg_step(prog, argv)
    if prog == "systemctl":
        return _systemctl_step(argv)
    if prog in ("pip", "pip3", "pipx", "uv", "npm", "pnpm", "yarn", "cargo", "flatpak", "gem", "go") or (
            prog.startswith("python") and argv[1:3] == ["-m", "pip"]):
        rest = args
        if prog.startswith("python"):
            rest = _args(argv[2:])
        if prog == "uv" and rest[:1] == ["pip"]:
            rest = rest[1:]
        if rest[:1] in (["install"], ["i"], ["add"]):
            if "-r" in argv or "--requirement" in argv or "-e" in argv:
                return Step("Installing dependencies", "Installed dependencies")
            pk = [r for r in rest[1:] if (not r.startswith(".") and "/" not in r) or r.startswith("git+")]
            if prog == "flatpak":
                pk = [p.split(".")[-1] for p in pk if p not in ("flathub",)]
            if pk:
                n = _names(pk)
                return Step(f"Installing {n}", f"Installed {n}")
            return Step("Installing dependencies", "Installed dependencies")
        if rest[:1] in (["uninstall"], ["remove"], ["rm"]) and rest[1:]:
            n = _names(rest[1:])
            return Step(f"Removing {n}", f"Removed {n}")
        if rest[:2] == ["run", "build"] or rest[:1] == ["build"]:
            return Step("Building", "Built it")
        if rest[:1] == ["test"] or rest[:2] == ["run", "test"]:
            return Step("Running the tests")
        return None
    if prog == "git":
        if a0 == "clone" and len(args) > 1:
            repo = _name(args[1]).removesuffix(".git")
            return Step(f"Downloading {repo}", f"Downloaded {repo}")
        return {"pull": Step("Pulling changes", "Pulled changes"), "push": Step("Pushing changes"),
                "commit": Step("Saving a version", "Saved a version"), "status": Step("Checking changes"),
                "diff": Step("Looking at changes"), "log": Step("Reading the history"),
                "checkout": Step("Switching versions", "Switched versions"),
                "switch": Step("Switching versions", "Switched versions"),
                "reset": Step("Resetting changes", "Reset changes"),
                "init": Step("Starting a repository", "Started a repository")}.get(a0)
    if prog in ("curl", "wget", "aria2c", "xh", "http"):
        url = next((a for a in args if re.match(r"^(https?|ftp)://", a)), a0)
        host = re.sub(r"^\w+://", "", url).split("/")[0] or "the web"
        short = "".join(a[1:] for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
        out = ("o" in short or "O" in short or "--output" in argv or "--remote-name" in argv
               or prog in ("wget", "aria2c") or ">" in segment)
        if out:
            fname = _name(url.split("?")[0]) if "/" in re.sub(r"^\w+://", "", url) else host
            return Step(f"Downloading {fname}", f"Downloaded {fname}")
        return Step(f"Fetching {host}")
    if prog in ("mkdir",) and args:
        return Step(f"Making the {_name(args[-1])} folder", f"Made the {_name(args[-1])} folder")
    if prog in ("rm", "rmdir", "trash", "trash-put", "gio") and args:
        targets = args[1:] if prog == "gio" else args
        what = _name(targets[0]) if len(targets) == 1 else f"{len(targets)} files"
        return Step(f"Deleting {what}", f"Deleted {what}")
    if prog in ("mv", "cp", "rsync", "install") and len(args) >= 2:
        verb = {"mv": ("Moving", "Moved"), "cp": ("Copying", "Copied"), "rsync": ("Copying", "Copied"),
                "install": ("Installing", "Installed")}[prog]
        what = _name(args[0]) if len(args) == 2 else f"{len(args) - 1} files"
        return Step(f"{verb[0]} {what}", f"{verb[1]} {what}")
    if prog in ("ln",) and args:
        return Step(f"Linking {_name(args[-1])}", f"Linked {_name(args[-1])}")
    if prog in ("chmod", "chown", "chgrp", "setfacl") and len(args) >= 2:
        return Step(f"Changing permissions on {_name(args[-1])}", f"Changed permissions on {_name(args[-1])}")
    if prog == "touch" and args:
        return Step(f"Creating {_name(args[0])}", f"Created {_name(args[0])}")
    if prog in ("tee",) and args:
        return Step(f"Writing {_name(args[-1])}", f"Wrote {_name(args[-1])}")
    if prog == "sed" and ("-i" in argv or any(a.startswith("-i") for a in argv)) and args:
        return Step(f"Editing {_name(args[-1])}", f"Edited {_name(args[-1])}")
    if prog in ("cat", "head", "tail", "less", "more", "bat", "wc", "file", "stat", "xxd", "hexdump") and args:
        return Step(f"Reading {_name(args[-1])}")
    if prog in ("grep", "rg", "ag", "egrep", "fgrep", "ack") and args:
        return Step(f"Searching for {_quote(args[0])}")
    if prog in ("ls", "find", "fd", "tree", "locate", "exa", "eza"):
        return Step("Looking through files")
    if prog in ("du", "df", "ncdu", "duf"):
        return Step("Checking disk space")
    if prog in ("free", "vmstat"):
        return Step("Checking memory")
    if prog in ("ps", "top", "htop", "btop", "pgrep", "pidof", "lsof", "fuser", "pstree"):
        return Step("Checking what is running")
    if prog in ("kill", "pkill", "killall"):
        what = next((a for a in args if not a.isdigit()), "a process")
        return Step(f"Stopping {what}", f"Stopped {what}")
    if prog in ("lsblk", "blkid", "fdisk", "parted", "sgdisk", "sfdisk", "smartctl", "findmnt") and not _irreversible(prog, argv, segment):
        return Step("Looking at the disks")
    if prog in ("mount", "umount"):
        return Step("Mounting a disk" if prog == "mount" else "Unmounting a disk", None, SYSTEM)
    if prog == "nmcli":
        if "connect" in args and "wifi" in args:
            ssid = args[args.index("connect") + 1] if args.index("connect") + 1 < len(args) else "Wi-Fi"
            return Step(f"Connecting to {ssid}", f"Connected to {ssid}", SYSTEM)
        if a0 in ("connection", "con", "c") and args[1:2] and args[1] in ("modify", "mod", "add", "delete", "up", "down"):
            return Step("Changing network settings", "Changed network settings", SYSTEM)
        return Step("Checking the network")
    if prog in ("ping", "ip", "ss", "netstat", "dig", "nslookup", "host", "traceroute", "resolvectl"):
        return Step("Checking the network")
    if prog in ("journalctl", "dmesg"):
        return Step("Reading the system log")
    if prog in ("hostnamectl", "timedatectl", "localectl") and len(args) > 1:
        return Step("Changing system settings", "Changed system settings", SYSTEM)
    if prog in ("tar", "unzip", "7z", "unrar", "gunzip", "xz", "zstd", "bsdtar"):
        creating = prog == "tar" and any("c" in a for a in argv[1:2] if not a.startswith("--"))
        target = _name(args[0]) if args else "an archive"
        if creating:
            return Step(f"Packing {target}", f"Packed {target}")
        return Step(f"Unpacking {target}", f"Unpacked {target}")
    if prog in ("make", "cmake", "ninja", "meson", "makepkg", "gcc", "g++", "clang", "rustc", "javac", "tsc"):
        if prog == "makepkg" and ("-i" in argv or "-si" in argv or "-sri" in argv):
            return Step("Building and installing a package", "Built and installed a package", SYSTEM)
        return Step("Building", "Built it")
    if prog == "docker" or prog == "podman":
        return {"pull": Step("Downloading a container image", "Downloaded a container image"),
                "build": Step("Building a container image", "Built a container image"),
                "run": Step("Starting a container", "Started a container"),
                "compose": Step("Starting containers", "Started containers"),
                "stop": Step("Stopping a container", "Stopped a container"),
                "rm": Step("Removing a container", "Removed a container")}.get(a0, Step("Checking containers"))
    if prog in ("python", "python3", "node", "deno", "bun", "ruby", "perl", "bash", "sh", "zsh", "lua"):
        script = next((a for a in argv[1:] if not a.startswith("-") and "/" in a or a.endswith((".py", ".js", ".sh"))), "")
        if script:
            return Step(f"Running {_name(script)}")
        return Step(from_description(description) or "Running a script") if description else Step("Running a script")
    if prog in ("hyprctl",):
        if a0 == "reload":
            return Step("Reloading the desktop settings", "Reloaded the desktop settings")
        if a0 in ("dispatch", "keyword", "eval"):
            return Step("Arranging the desktop")
        return Step("Checking the desktop")
    if prog in ("grim", "slurp"):
        return Step("Looking at the screen")
    if prog in ("notify-send",):
        return Step("Sending a notification")
    if prog in ("wpctl", "pactl", "amixer", "pamixer"):
        changing = any(a.startswith("set") for a in args)
        return Step("Changing the volume", "Changed the volume") if changing else Step("Checking the sound")
    if prog in ("brightnessctl", "light"):
        return Step("Changing the brightness", "Changed the brightness") if "set" in args or "-S" in argv else Step("Checking the brightness")
    if prog in ("bombadil-app",):
        name = args[1] if len(args) > 1 else ""
        verb = {"check": "Checking", "run": "Opening", "show": "Opening", "close": "Closing", "status": "Checking"}.get(a0)
        if verb and name:
            return Step(f"{verb} {name}")
    if prog in ("bombadil",) and a0 == "undo":
        return Step("Undoing the last change", "Undid the last change")
    if prog in ("snapper",):
        return Step("Checking restore points")
    if prog in ("reboot",):
        return Step("Restarting the computer", "Restarted the computer")
    if prog in ("poweroff", "shutdown", "halt"):
        return Step("Shutting down", "Shut down")
    if prog in ("which", "type", "whereis", "command"):
        return Step(f"Checking for {a0}" if a0 else "Checking what is installed")
    if prog in ("xdg-open", "chromium", "google-chrome", "firefox"):
        return Step("Opening the browser" if prog != "xdg-open" else "Opening a file")
    if prog in ("sleep", "wait"):
        return Step("Waiting")
    if prog in ("useradd", "usermod", "userdel", "passwd", "groupadd", "gpasswd", "chpasswd"):
        return Step("Changing user accounts", "Changed user accounts", SYSTEM)
    if prog in ("modprobe", "rmmod", "insmod", "sysctl") and args:
        return Step("Changing kernel settings", "Changed kernel settings", SYSTEM)
    if prog in ("grub-mkconfig", "grub-install", "mkinitcpio", "bootctl", "efibootmgr"):
        return Step("Updating the boot setup", "Updated the boot setup", SYSTEM)
    if prog in ("ufw", "iptables", "nft", "firewall-cmd"):
        return Step("Changing the firewall", "Changed the firewall", SYSTEM)
    if prog.startswith("mkfs") or prog in ("wipefs", "blkdiscard", "shred", "dd", "cryptsetup", "mkswap"):
        dev = next((a.split("=", 1)[1] for a in argv if a.startswith("of=")), args[-1] if args else "a disk")
        return Step(f"Formatting {dev}" if prog != "shred" else f"Wiping {dev}", f"Erased {dev}")
    return None


def shell_step(command: str, description: str | None = None) -> Step:
    """The step for a shell command, from the main command in it."""
    inner = _unwrap(command)
    best: Step | None = None
    sudo_any = False
    system = False
    irreversible = False
    for segment_argv in _segments(inner):
        argv, sudo = _strip_wrappers(segment_argv)
        sudo_any = sudo_any or sudo
        if not argv:
            continue
        prog = PurePosixPath(argv[0]).name
        segment = " ".join(segment_argv)
        if _irreversible(prog, argv, segment):
            irreversible = True
        if prog in _PKG and any(a.startswith(("-S", "-R", "-U", "--sync", "--remove", "--upgrade"))
                                and not re.match(r"^-S[si]", a) for a in argv[1:]):
            system = True
        if any(_is_system_path(p) for p in _writes_redirect(segment)):
            system = True
        if prog in ("tee", "cp", "mv", "rm", "ln", "install", "chmod", "chown", "sed", "touch", "mkdir") and any(
                _is_system_path(a) for a in _args(argv)):
            system = True
        if prog in _TRIVIAL and best is None:
            continue
        if prog in ("bash", "sh", "zsh", "dash") and len(argv) > 2 and argv[1] in ("-c", "-lc"):
            # `sudo sh -c '...'`: read the script inside.
            step = shell_step(argv[2], description)
            irreversible = irreversible or step.risk == IRREVERSIBLE
            system = system or step.risk == SYSTEM
        else:
            step = _command_step(argv, segment, description)
        if step is not None and (best is None or (step.changes and not best.changes)):
            best = step
            if step.risk == SYSTEM:
                system = True
    said = from_description(description) if description else None
    if best is not None and said and not best.changes:
        # The model's own words for what it runs beat a generic reading ("Waiting", "Running
        # a script"); a recognised change (Installing ffmpeg) keeps its reading and its summary.
        best = Step(said, None, best.risk)
    if best is None:
        text = said
        prog = next((PurePosixPath(a[0]).name for a in (_strip_wrappers(s)[0] for s in _segments(inner)) if a), "a command")
        best = Step(text or f"Running {prog}")
    if irreversible:
        best.risk = IRREVERSIBLE
    elif sudo_any or system:
        best.risk = SYSTEM
    if best.risk:
        best.command = " ".join(inner.split())[:300]
    return best


# -- tools --

def _os_tool(tool: str, a: dict) -> Step | None:
    """bombadil-os tools (os-mcp), for both providers."""
    if tool == "show_panel":
        return Step(f"Opening {PANEL_WORDS.get(a.get('name'), a.get('name') or 'a panel')}",
                    f"Opened {PANEL_WORDS.get(a.get('name'), a.get('name') or 'a panel')}")
    if tool == "hide_panel":
        return Step(f"Putting {PANEL_WORDS.get(a.get('name'), a.get('name') or 'a panel')} away")
    if tool == "create_app":
        title = _app_title(a.get("title", ""))
        files = a.get("files") if isinstance(a.get("files"), dict) else {}
        n = _lines(a.get("qml"), a.get("python"), *files.values())
        again = _app_exists(a.get("title", "")) if a.get("title") else False
        now, past = ("Changing", "Changed") if again else ("Building", "Made")
        return Step(f"{now} {title}" + (f", {n} lines" if n > 1 else ""), f"{past} {title}")
    if tool in ("open_app", "show_app"):
        return Step(f"Opening {a.get('name') or 'an app'}")
    if tool == "hide_app":
        return Step(f"Putting {a.get('name') or 'an app'} away")
    if tool == "close_app":
        return Step(f"Closing {a.get('name') or 'an app'}")
    if tool in ("check_app", "app_status"):
        return Step(f"Checking {a.get('name') or 'the app'}")
    if tool == "list_apps":
        return Step("Checking your apps")
    if tool in ("app_template", "app_guide"):
        return Step("Reading the app guide")
    if tool == "screenshot":
        return Step("Looking at the screen")
    if tool == "snapshot":
        return Step("Saving a restore point")
    if tool == "list_snapshots":
        return Step("Checking restore points")
    if tool == "rollback":
        return Step("Undoing the last change", "Undid the last change", SYSTEM)
    if tool == "notify":
        return Step("Sending a notification")
    if tool == "show_card":
        return Step("Showing a card")
    if tool == "open_url":
        url = str(a.get("url", ""))
        host = re.sub(r"^\w+://", "", url).split("/")[0]
        return Step(f"Opening {host}" if host else "Opening the browser")
    return None


def tool_step(name: str, a: dict | None) -> Step | None:
    """The step for a tool call, or None for tools that are not worth a line (ToolSearch)."""
    a = a if isinstance(a, dict) else {}
    name = str(name or "")
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        if server == "bombadil-os":
            return _os_tool(tool, a) or Step("Working on it")
        return Step(f"Using {server.replace('-', ' ')}" if server else "Working on it")
    if name == "Bash":
        return shell_step(str(a.get("command", "")), a.get("description"))
    if name in ("Read", "NotebookRead"):
        return Step(f"Reading {_name(a.get('file_path') or a.get('notebook_path') or 'a file')}")
    if name == "Write":
        n = _lines(a.get("content"))
        p = str(a.get("file_path", ""))
        step = Step(f"Writing {_name(p)}" + (f", {n} lines" if n > 1 else ""), f"Wrote {_name(p)}")
        if _is_system_path(p):
            step.risk, step.command = SYSTEM, f"write {p}"
        return step
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        p = str(a.get("file_path") or a.get("notebook_path") or "")
        step = Step(f"Editing {_name(p)}", f"Edited {_name(p)}")
        if _is_system_path(p):
            step.risk, step.command = SYSTEM, f"edit {p}"
        return step
    if name == "Glob":
        return Step("Looking for files")
    if name == "Grep":
        return Step(f"Searching for {_quote(a.get('pattern', ''))}")
    if name == "LS":
        return Step("Looking through files")
    if name == "WebFetch":
        host = re.sub(r"^\w+://", "", str(a.get("url", ""))).split("/")[0]
        return Step(f"Reading {host}" if host else "Reading a web page")
    if name == "WebSearch":
        return Step(f"Searching the web for {_quote(a.get('query', ''))}")
    if name == "TodoWrite":
        todos = a.get("todos") if isinstance(a.get("todos"), list) else []
        active = next((t for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"), None)
        if active and (active.get("activeForm") or active.get("content")):
            return Step(str(active.get("activeForm") or from_description(active["content"]) or active["content"])[:90])
        return Step("Planning the steps")
    if name == "TaskCreate":
        return Step("Planning the steps")
    if name == "TaskUpdate":
        # Claude's task list: the task it starts has a present-tense form ("Installing ffmpeg").
        if a.get("status") == "in_progress" and (a.get("activeForm") or a.get("subject")):
            return Step(str(a.get("activeForm") or from_description(a["subject"]) or a["subject"])[:90])
        return None
    if name in ("TaskList", "TaskGet"):
        return None
    if name in ("Task", "Agent"):
        return Step(from_description(a.get("description", "")) or "Working on part of it")
    if name in ("BashOutput", "TaskOutput"):
        return Step("Checking on a background job")
    if name in ("KillShell", "KillBash", "TaskStop"):
        return Step("Stopping a background job")
    if name == "Skill":
        return Step("Reading a guide")
    if name in ("ToolSearch", "ExitPlanMode", "EnterPlanMode", "ListMcpResourcesTool", "ReadMcpResourceTool"):
        return None
    return Step("Working on it")


def file_change_step(changes: list) -> Step:
    """Codex's file_change item: [{path, kind: add|delete|update}]."""
    changes = [c for c in changes if isinstance(c, dict)]
    if not changes:
        return Step("Editing files", "Edited files")
    kinds = {c.get("kind") for c in changes}
    if len(changes) == 1:
        c = changes[0]
        name = _name(c.get("path", ""))
        verb = {"add": ("Writing", "Wrote"), "delete": ("Deleting", "Deleted")}.get(c.get("kind"), ("Editing", "Edited"))
        step = Step(f"{verb[0]} {name}", f"{verb[1]} {name}")
    else:
        verb = ("Deleting", "Deleted") if kinds == {"delete"} else ("Editing", "Edited")
        step = Step(f"{verb[0]} {len(changes)} files", f"{verb[1]} {len(changes)} files")
    sys_paths = [c.get("path", "") for c in changes if _is_system_path(str(c.get("path", "")))]
    if sys_paths:
        step.risk, step.command = SYSTEM, "edit " + " ".join(sys_paths)[:280]
    return step


# -- partial tool input (Claude's --include-partial-messages) --

_TITLE_RE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"')
_PATH_RE = re.compile(r'"file_path"\s*:\s*"((?:[^"\\]|\\.)*)"')


def partial_step(name: str, partial: str) -> Step | None:
    """A step from a tool call's input while it is still being written: an app's title and a
    running line count, so "Building Passwords, 84 lines" counts up as the model writes."""
    if name.endswith("__create_app"):
        m = _TITLE_RE.search(partial)
        title = _app_title(json.loads(f'"{m.group(1)}"')) if m else "an app"
        n = partial.count("\\n")
        again = _app_exists(title) if m else False
        return Step(f"{'Changing' if again else 'Building'} {title}" + (f", {n} lines" if n > 1 else ""),
                    f"{'Changed' if again else 'Made'} {title}")
    if name == "Write":
        m = _PATH_RE.search(partial)
        n = partial.count("\\n")
        if m:
            p = json.loads(f'"{m.group(1)}"')
            return Step(f"Writing {_name(p)}" + (f", {n} lines" if n > 1 else ""), f"Wrote {_name(p)}")
    return None


# -- the turn --

def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


class Narrator:
    """Follows one turn's events; says what to show on the line and how the turn ended."""

    def __init__(self):
        self.step: Step | None = None
        self.done: list[str] = []
        self.irreversible = False
        self.system = False
        self.said = ""           # the agent's words in the current text block
        self._partial: dict[int, dict] = {}
        self._creating: dict[str, str] = {}   # TaskCreate tool id -> its present-tense form
        self._tasks: dict[str, str] = {}      # task id -> its present-tense form
        self._shown: dict | None = None       # the line last returned

    # Each method returns the new line (a dict for a "status" event) or None when it did not change.

    def _set(self, step: Step | None) -> dict | None:
        if step is None:
            return None
        if step.changes and step.done not in self.done:
            self.done.append(step.done)
        if step.risk == IRREVERSIBLE:
            self.irreversible = True
        elif step.risk == SYSTEM:
            self.system = True
        self.step = step
        return {"text": step.text, "risk": step.risk, "command": step.command, "source": "step"}

    def on_event(self, ev: dict) -> dict | None:
        line = self._line(ev)
        # The complete message repeats what its stream already showed; say each line once.
        if line is None or line == self._shown:
            return None
        self._shown = line
        return line

    def _line(self, ev: dict) -> dict | None:
        kind = ev.get("kind")
        if kind == "tool_result" and ev.get("id") in self._creating:
            m = re.search(r"#?(\d+)", str(ev.get("output", "")))
            if m:
                self._tasks[m.group(1)] = self._creating.pop(ev["id"])
            return None
        if kind == "tool":
            a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
            if ev.get("name") == "TaskCreate" and ev.get("id"):
                form = a.get("activeForm") or from_description(a.get("subject", "")) or a.get("subject")
                if form:
                    self._creating[ev["id"]] = str(form)[:90]
            if ev.get("name") == "TaskUpdate" and a.get("status") == "in_progress" and not a.get("activeForm"):
                known = self._tasks.get(str(a.get("taskId", "")))
                if known:
                    self.said = ""
                    return self._set(Step(known))
            step = tool_step(ev.get("name", ""), ev.get("input"))
            if step is None:
                return None
            self.said = ""
            return self._set(step)
        if kind == "file_change":
            return self._set(file_change_step(ev.get("changes") or []))
        if kind == "tool_start":
            self._partial[ev.get("index", 0)] = {"name": ev.get("name", ""), "json": ""}
            step = partial_step(ev.get("name", ""), "") or tool_step(ev.get("name", ""), {})
            if step is not None and step.changes:
                # Not recorded as a change until the call is complete.
                step = Step(step.text, None, step.risk, step.command)
            self.said = ""
            return self._set(step) if step else None
        if kind == "tool_input":
            p = self._partial.get(ev.get("index", 0))
            if p is None:
                return None
            p["json"] += ev.get("partial", "")
            step = partial_step(p["name"], p["json"])
            if step is None:
                return None
            return self._set(Step(step.text))
        if kind == "text_delta":
            self.said += ev.get("text", "")
            line = self.said.strip().splitlines()[-1].strip() if self.said.strip() else ""
            if not line:
                return None
            return {"text": line[-200:], "risk": None, "command": None, "source": "agent"}
        if kind == "text":
            self.said = ""
            text = ev.get("text", "").strip()
            if not text:
                return None
            return {"text": text.splitlines()[-1].strip()[-200:], "risk": None, "command": None, "source": "agent"}
        if kind == "output":
            # A typed "!command": its newest output line, keeping the command's mark.
            text = ev.get("text", "").strip()
            if not text:
                return None
            s = self.step
            return {"text": text[-200:], "risk": s.risk if s else None, "command": s.command if s else None,
                    "source": "agent"}
        if kind == "thinking":
            head = " ".join(str(ev.get("text", "")).split())
            if head and len(head) <= 90:
                return self._set(Step(head))
            if self.step is None and not self.said:
                return {"text": "Thinking", "risk": None, "command": None, "source": "step"}
        return None

    def summary(self) -> str:
        """One sentence for what the turn changed: "Installed ffmpeg and made Passwords."."""
        done = self.done
        if not done:
            return ""
        parts = [done[0]] + [_lower_first(d) for d in done[1:3]]
        if len(done) > 3:
            s = ", ".join(parts) + f" and {len(done) - 3} more"
        elif len(parts) > 1:
            s = ", ".join(parts[:-1]) + " and " + parts[-1]
        else:
            s = parts[0]
        return s + "."

    def stopped_line(self) -> str:
        if self.step is None:
            return "Stopped."
        return f"Stopped while {_lower_first(self.step.text.split(',')[0])}."
