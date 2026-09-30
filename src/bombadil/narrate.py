"""Plain words for what the agent is doing: the live line above the pill.

agentd feeds every provider event of a turn to a `Narrator`, which answers with the line
to show ("Installing ffmpeg", "Building Passwords, 84 lines") and, when the turn ends, one
sentence for what it changed ("Installed ffmpeg and made Passwords."). A step that touches
the system (sudo, pacman, /etc, a service) is marked "system" and carries the exact
command; a step no restore point can undo (formatting a disk, deleting files in your home
folder) is marked "irreversible". Nothing here pauses or asks: the marks are only shown.

The narrator also keeps the plan the agent writes for itself (Claude's task list, Codex's plan)
as one table for the desk, and counts what the turn has changed so far.

Pure functions over the events, no I/O except checking whether an app folder exists and reading
a job's title by its id, so the rule table is cheap to test and to extend.
"""

import json
import math
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import desk, jobs, paths

SYSTEM = "system"
IRREVERSIBLE = "irreversible"


@dataclass
class Step:
    text: str                  # present tense for the live line: "Installing ffmpeg"
    done: str | None = None    # past tense for the closing sentence: "Installed ffmpeg"; None = not a change
    risk: str | None = None    # None, SYSTEM or IRREVERSIBLE
    command: str | None = None  # the exact command, shown under a marked step
    # What a change touches, by kind (package, service, file, app), for the turn's running count.
    touched: dict[str, tuple[str, ...]] = field(default_factory=dict)

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


def _job_title(title) -> str:
    return " ".join(str(title if title is not None else "").split())[:40]


def _timer_title(n) -> str:
    """"Timer, 10 min" for a length the job registry would take, else ""."""
    try:
        ok = not isinstance(n, bool) and 1 <= float(n) <= jobs.MAX_SECONDS
    except (TypeError, ValueError):
        ok = False
    return jobs.timer_title(math.ceil(float(n))) if ok else ""


def _app_exists(title: str) -> bool:
    from . import apps
    try:
        return (paths.apps_dir() / apps.slug(str(title)) / "main.qml").exists()
    except Exception:  # noqa: BLE001 - only decides "Building" or "Changing"
        return False


# -- shell commands --

SYSTEM_PATHS = ("/etc", "/usr", "/boot", "/efi", "/opt", "/var", "/srv", "/lib", "/bin", "/sbin", "/root")
_PKG = {"pacman", "yay", "paru", "pikaur"}
_TRIVIAL = {"cd", "export", "set", "true", "false", ":", "source", ".", "echo", "printf", "unset", "local",
            "test", "[", "exit", "read"}
_WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "nice", "ionice", "stdbuf", "timeout", "exec", "command",
             "builtin", "setsid", "unbuffer", "script"}


_SHELLS = ("bash", "sh", "zsh", "dash")


def _shell_script(argv: list[str]) -> str | None:
    """The script of `bash -c SCRIPT`, `sh -ec SCRIPT`, `bash -e -o pipefail -c SCRIPT`."""
    if not argv or PurePosixPath(argv[0]).name not in _SHELLS:
        return None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "+o", "-O", "+O"):
            i += 2
        elif a.startswith("--"):
            i += 1
        elif a[:1] in ("-", "+") and len(a) > 1:
            if a[0] == "-" and "c" in a[1:]:
                return argv[i + 1] if i + 1 < len(argv) else None
            i += 1
        else:
            return None   # a script file, not -c
    return None


def _unwrap(command: str) -> str:
    """Codex runs `bash -lc '<cmd>'`; show and read the inner command."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    script = _shell_script(argv)
    return command if script is None else script


_HEREDOC_RE = re.compile(r"""<<(-?)[ \t]*(['"]?)([A-Za-z0-9_.-]+)\2""")


def _split(command: str) -> list[str]:
    """Split on ; && || | & and newlines outside quotes (`sh -c 'a && b'` stays whole, 2>&1 too).
    A heredoc's body is text, not commands: it is left out."""
    parts: list[str] = []
    cur: list[str] = []
    quote = None
    heredocs: list[tuple[str, bool]] = []   # (terminator, <<- strips tabs) waiting for their body
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if not quote and command.startswith("<<", i) and not command.startswith("<<<", i):
            m = _HEREDOC_RE.match(command, i)
            if m:
                heredocs.append((m.group(3), m.group(1) == "-"))
                cur.append(m.group(0))
                i = m.end()
                continue
        if not quote and c == "\n" and heredocs:
            parts.append("".join(cur))
            cur = []
            j = i + 1
            for word, tabs in heredocs:
                while j < n:
                    end = command.find("\n", j)
                    end = n if end == -1 else end
                    line = command[j:end]
                    j = end + 1
                    if (line.lstrip("\t") if tabs else line) == word:
                        break
            heredocs = []
            i = j
            continue
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


_REDIRECT_RE = re.compile(r"^(\d*|&)(>>|>\||>&|>|<<<|<<-|<<|<&|<>|<)(.*)$")


def _strip_redirects(argv: list[str]) -> tuple[list[str], list[str]]:
    """(the words of a command without its redirections, the files its output goes to)."""
    words: list[str] = []
    out: list[str] = []
    i = 0
    while i < len(argv):
        m = _REDIRECT_RE.match(argv[i])
        if not m:
            words.append(argv[i])
            i += 1
            continue
        fd, op, target = m.groups()
        if not target and i + 1 < len(argv):
            target = argv[i + 1]
            i += 1
        i += 1
        if op in (">", ">>", ">|") and fd in ("", "1", "&") and target and not target.startswith("&"):
            out.append(target)
    return words, out


def _is_system_path(p: str) -> bool:
    return any(p == s or p.startswith(s + "/") for s in SYSTEM_PATHS)


def _expand_home(p: str) -> str:
    home = str(paths.home())
    return os.path.expanduser(p.replace("${HOME}", home).replace("$HOME", home))


def _is_home_path(p: str, cwd: str | None = None) -> bool:
    """Is this path in the home folder, or above it (/, /home)? A relative path is where the
    command runs: after `cd /tmp` not home, otherwise the agent's own folder, which is."""
    home = str(paths.home())
    p = _expand_home(p)
    if p.startswith(("$", "~")):
        return p.startswith("~")   # ~otheruser is someone's home; $VAR is anyone's guess
    if not p.startswith("/"):
        if cwd is None:
            return True
        p = os.path.join(cwd, p)
    p = os.path.normpath(p)
    if p.rstrip("*").rstrip("/") in ("", "/home"):
        return True   # everything under it, home folders included
    return p == home or p.startswith(home + "/")


def _git_sub(argv: list[str]) -> tuple[str, list[str]]:
    """`git -C repo push -f` -> ("push", ["-f"])."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    return (argv[i], argv[i + 1:]) if i < len(argv) else ("", [])


def _short(a: str, letter: str) -> bool:
    """Is `a` a short option cluster (-xf) containing `letter`?"""
    return a.startswith("-") and not a.startswith("--") and letter in a[1:]


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
        return Step(f"Installing {n}{aur}", f"Installed {n}", touched={"package": tuple(names)})
    if op == "S" and "u" in flags:
        return Step("Updating the system", "Updated the system")
    if op == "S" and "y" in flags:
        return Step("Refreshing the package lists")
    if op == "S" and "c" in flags:
        return Step("Clearing the package cache", "Cleared the package cache")
    if op == "R" and names:
        n = _names(names)
        return Step(f"Removing {n}", f"Removed {n}", touched={"package": tuple(names)})
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
        return Step(f"{now_w} {n}", f"{past} {n}", None if user else SYSTEM,
                    touched={"service": tuple(units)})
    if verb in ("status", "is-active", "is-enabled", "list-units", "list-unit-files", "show", "cat"):
        return Step(f"Checking {n}" if n else "Checking services")
    return None


_DEV_SAFE = ("/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/random", "/dev/urandom")
_SFDISK_READ = {"-l", "--list", "-d", "--dump", "-J", "--json", "-s", "--show-size", "-g", "--show-geometry",
                "-F", "--list-free", "-V", "--verify", "-v", "--version", "-h", "--help", "-b", "--backup"}


def _dd_of(argv: list[str]) -> str | None:
    return next((a.split("=", 1)[1] for a in argv if a.startswith("of=")), None)


def _find_paths(argv: list[str]) -> list[str]:
    """find's starting points: the arguments before its first test or action."""
    out = []
    for a in argv[1:]:
        if a.startswith(("-", "(", "!")) or a == ")":
            break
        out.append(a)
    return out or ["."]


def _irreversible(prog: str, argv: list[str], segment: str, cwd: str | None = None) -> bool:
    if prog.startswith("mkfs") or prog in ("blkdiscard", "shred", "mkswap"):
        return True
    if prog == "wipefs":
        return any(a in ("--all", "--offset") or a.startswith("--offset=") or _short(a, "a") or _short(a, "o")
                   for a in argv[1:])
    if prog == "dd":
        of = _dd_of(argv)
        return bool(of and of.startswith("/dev/") and of not in _DEV_SAFE)
    if prog == "sgdisk" and any(a in ("-Z", "--zap-all", "-o", "--clear", "-z", "--zap", "--delete")
                                or re.match(r"^-d\d*$", a) for a in argv[1:]):
        return True
    if prog == "sfdisk":
        if "--delete" in argv:
            return True
        # Without a flag that only reads, sfdisk writes the table it reads on stdin.
        return not any(a in _SFDISK_READ for a in argv[1:]) and any(a.startswith("/dev/") for a in argv[1:])
    if prog == "parted" and re.search(r"\b(mklabel|mktable|rm)\b", segment):
        return True
    if prog == "cryptsetup" and re.search(r"\b(luksFormat|erase|luksErase)\b", segment):
        return True
    if prog == "rm" or (prog == "find" and ("-delete" in argv or any(
            a in ("-exec", "-execdir", "-ok", "-okdir") and PurePosixPath(argv[i + 1] if i + 1 < len(argv) else "").name == "rm"
            for i, a in enumerate(argv)))):
        targets = _args(argv) if prog == "rm" else _find_paths(argv)
        # Home is not in the restore points yet, so deleting there cannot be undone.
        return any(_is_home_path(t, cwd) for t in targets)
    if prog == "git":
        sub, rest = _git_sub(argv)
        if sub == "push":
            return any(a in ("--force", "--mirror", "--delete", "--prune") or a.startswith(("--force", "+", ":"))
                       or _short(a, "f") or _short(a, "d") for a in rest)
        if sub == "clean":
            return any(a == "--force" or _short(a, "f") for a in rest)
        if sub == "reset":
            return "--hard" in rest
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
                return Step(f"Installing {n}", f"Installed {n}", touched={"package": tuple(pk)})
            return Step("Installing dependencies", "Installed dependencies")
        if rest[:1] in (["uninstall"], ["remove"], ["rm"]) and rest[1:]:
            n = _names(rest[1:])
            return Step(f"Removing {n}", f"Removed {n}", touched={"package": tuple(rest[1:])})
        if rest[:2] == ["run", "build"] or rest[:1] == ["build"]:
            return Step("Building", "Built it")
        if rest[:1] == ["test"] or rest[:2] == ["run", "test"]:
            return Step("Running the tests")
        return None
    if prog == "git":
        a0, rest = _git_sub(argv)
        rest = [a for a in rest if not a.startswith("-")]
        if a0 == "clone" and rest:
            repo = _name(rest[0]).removesuffix(".git")
            return Step(f"Downloading {repo}", f"Downloaded {repo}")
        return {"pull": Step("Pulling changes", "Pulled changes"), "push": Step("Pushing changes"),
                "commit": Step("Saving a version", "Saved a version"), "status": Step("Checking changes"),
                "diff": Step("Looking at changes"), "log": Step("Reading the history"),
                "checkout": Step("Switching versions", "Switched versions"),
                "switch": Step("Switching versions", "Switched versions"),
                "reset": Step("Resetting changes", "Reset changes"),
                "init": Step("Starting a repository", "Started a repository")}.get(a0)
    if prog in ("curl", "wget", "aria2c", "xh", "http", "https"):
        url = _url_arg(argv)
        host = _host(url) or "the web"
        short = "".join(a[1:] for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
        out = ("o" in short or "O" in short or "--output" in argv or "--remote-name" in argv
               or prog in ("wget", "aria2c") or ">" in segment)
        if out:
            fname = _name(url.split("?")[0]) if "/" in re.sub(r"^\w+://", "", url) else host
            fname = fname or host
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
    if prog in ("lsblk", "blkid", "fdisk", "gdisk", "parted", "sgdisk", "sfdisk", "smartctl", "findmnt"):
        if not _irreversible(prog, argv, segment):
            return Step("Looking at the disks")
        dev = next((_name(a) for a in args if a.startswith("/dev/")), "a disk")
        return Step(f"Changing the partitions on {dev}", f"Changed the partitions on {dev}")
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
        return _disk_step(prog, argv, args, segment)
    return None


def _disk_step(prog: str, argv: list[str], args: list[str], segment: str) -> Step:
    dev = args[-1] if args else "a disk"
    if prog.startswith("mkfs"):
        return Step(f"Formatting {dev}", f"Formatted {dev}")
    if prog == "mkswap":
        return Step(f"Making swap on {dev}", f"Made swap on {dev}")
    if prog == "wipefs":
        if _irreversible(prog, argv, segment):
            return Step(f"Erasing {dev}", f"Erased {dev}")
        return Step("Looking at the disks")
    if prog == "blkdiscard":
        return Step(f"Erasing {dev}", f"Erased {dev}")
    if prog == "shred":
        what = _name(dev) if len(args) == 1 else f"{len(args)} files"
        return Step(f"Wiping {what}", f"Wiped {what}")
    if prog == "dd":
        of = _dd_of(argv)
        src = next((a.split("=", 1)[1] for a in argv if a.startswith("if=")), "")
        if not of or of in _DEV_SAFE:
            return Step(f"Reading {src}" if src else "Copying data")
        if of.startswith("/dev/"):
            what = f"{_name(src)} to" if src and not src.startswith("/dev/") else "to"
            return Step(f"Writing {what} {of}", f"Wrote {what} {of}")
        return Step(f"Writing {_name(of)}", f"Wrote {_name(of)}")
    # cryptsetup
    action = args[0] if args else ""
    target = args[1] if len(args) > 1 else "a disk"
    if action == "luksFormat":
        return Step(f"Encrypting {target}", f"Encrypted {target}")
    if action in ("erase", "luksErase"):
        return Step(f"Erasing the keys of {target}", f"Erased the keys of {target}")
    if action in ("open", "luksOpen"):
        return Step(f"Unlocking {target}")
    if action in ("close", "luksClose"):
        return Step(f"Locking {target}")
    if action in ("status", "luksDump", "isLuks", "tcryptDump", "benchmark"):
        return Step("Looking at the disks")
    return Step("Changing disk encryption", "Changed disk encryption")


_VALUE_OPTS = {  # download tools' options that take a value: never the address
    "-u", "--user", "-H", "--header", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "--data-ascii", "--json", "-o", "--output", "-X", "--request", "-A", "--user-agent", "-e", "--referer",
    "-b", "--cookie", "-c", "--cookie-jar", "-F", "--form", "-T", "--upload-file", "-x", "--proxy",
    "-U", "--proxy-user", "--oauth2-bearer", "-K", "--config", "-w", "--write-out", "-m", "--max-time",
    "--connect-timeout", "-r", "--range", "-E", "--cert", "--key", "--cacert", "--resolve", "--retry",
    "-C", "--continue-at", "-O", "-P", "--directory-prefix", "--password", "--http-user", "--http-password",
    "--post-data", "--body-data", "--load-cookies", "--save-cookies", "-a", "--auth", "--session",
}
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _url_arg(argv: list[str]) -> str:
    """The address a download command fetches: an argument with a scheme, else the first
    one that is not an option's value (a user:password, a header) or an HTTP method."""
    plain = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--url" and i + 1 < len(argv):
            return argv[i + 1]
        if a in _VALUE_OPTS:
            i += 2
            continue
        if not a.startswith("-") and a not in _METHODS:
            plain.append(a)
        i += 1
    return next((a for a in plain if re.match(r"^(https?|ftp)://", a)), plain[0] if plain else "")


def _host(url: str) -> str:
    """'https://user:secret@api.example.com/v1' -> 'api.example.com'. Never a credential."""
    rest = re.sub(r"^\w+://", "", str(url)).split("/")[0].split("?")[0]
    rest = rest.rsplit("@", 1)[-1]
    return rest if re.fullmatch(r"[\w.:\[\]-]+", rest) else ""


def _changed_paths(prog: str, argv: list[str]) -> list[str]:
    """The paths a file command changes: a copy's destination, not its source; sed's files
    only with -i."""
    args = _args(argv)
    if prog in ("cp", "ln", "install", "rsync"):
        target = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("-t", "--target-directory")),
                      next((a.split("=", 1)[1] for a in argv if a.startswith("--target-directory=")), None))
        return [target] if target else args[-1:]
    if prog == "sed":
        if not any(a == "--in-place" or a.startswith("--in-place=") or _short(a, "i") for a in argv[1:]):
            return []
        return args if any(a in ("-e", "-f", "--expression", "--file") for a in argv) else args[1:]
    if prog in ("tee", "mv", "rm", "rmdir", "chmod", "chown", "chgrp", "touch", "mkdir", "truncate"):
        return args
    return []


def _touched_files(prog: str, argv: list[str]) -> list[str]:
    """The files a command that changes files touches, for the turn's count: what it makes,
    moves, deletes or edits, never the mode or owner it sets or the place a move ends up."""
    args = _args(argv)
    if prog in ("chmod", "chown", "chgrp", "setfacl"):
        found = args[1:]
    elif prog == "mv":
        found = args[:-1] if len(args) > 1 else args
    elif prog in ("trash", "trash-put"):
        found = args
    elif prog == "gio":
        found = args[1:]
    else:
        found = _changed_paths(prog, argv)
    return [p for p in found if not p.startswith("/dev/")]


def shell_step(command: str, description: str | None = None) -> Step:
    """The step for a shell command, from the main command in it."""
    inner = _unwrap(command)
    best: Step | None = None
    sudo_any = False
    system = False
    irreversible = False
    touched: dict[str, list[str]] = {}   # every segment's, not only the one the line names
    cwd: str | None = None      # after a `cd`, where relative paths point
    talk = False                # a plain edit of persona.toml
    for segment_argv in _segments(inner):
        words, writes = _strip_redirects(segment_argv)
        argv, sudo = _strip_wrappers(words)
        sudo_any = sudo_any or sudo
        if not argv:
            continue
        prog = PurePosixPath(argv[0]).name
        segment = " ".join(segment_argv)
        if prog == "cd":
            to = _expand_home(argv[1]) if len(argv) > 1 else str(paths.home())
            if to.startswith("/"):
                cwd = os.path.normpath(to)
            elif cwd and not to.startswith(("$", "-", "~")):
                cwd = os.path.normpath(os.path.join(cwd, to))
            else:
                cwd = None
            continue
        edited = _changed_paths(prog, argv) if prog in ("tee", "sed") else []
        if any(_is_persona(p) for p in writes + edited):
            talk = True
            continue
        if _irreversible(prog, argv, segment, cwd):
            irreversible = True
        if prog in _PKG and any(a.startswith(("-S", "-R", "-U", "--sync", "--remove", "--upgrade"))
                                and not re.match(r"^-S[si]", a) for a in argv[1:]):
            system = True
        if any(_is_system_path(p) for p in _writes_redirect(segment) + writes):
            system = True
        if any(_is_system_path(p) for p in _changed_paths(prog, argv)):
            system = True
        files = [w for w in writes if not w.startswith("/dev/")]
        if prog in _TRIVIAL and best is None and not (files and prog in ("echo", "printf")):
            continue
        script = _shell_script(argv)
        if script is not None:
            # `sudo sh -c '...'`: read the script inside.
            step = shell_step(script, description)
            irreversible = irreversible or step.risk == IRREVERSIBLE
            system = system or step.risk == SYSTEM
        else:
            step = _command_step(argv, segment, description)
            if step is not None and step.changes and "file" not in step.touched:
                found = _touched_files(prog, argv)
                if found:
                    step.touched = {**step.touched, "file": tuple(found)}
        if files and (prog in ("cat", "echo", "printf") or step is None):
            # `cat > notes.txt <<EOF`: the command is writing that file.
            step = Step(f"Writing {_name(files[-1])}", f"Wrote {_name(files[-1])}",
                        touched={"file": tuple(files)})
        if step is not None:
            for kind, things in step.touched.items():
                touched.setdefault(kind, []).extend(things)
        if step is not None and (best is None or (step.changes and not best.changes)):
            best = step
            if step.risk == SYSTEM:
                system = True
    if talk and (best is None or not best.changes) and not (irreversible or system or sudo_any):
        return Step(PERSONA_STEP)
    said = from_description(description) if description else None
    if best is not None and said and not best.changes:
        # The model's own words for what it runs beat a generic reading ("Waiting", "Running
        # a script"); a recognised change (Installing ffmpeg) keeps its reading and its summary.
        best = Step(said, None, best.risk)
    if best is None:
        text = said
        prog = next((PurePosixPath(a[0]).name for a in (_strip_wrappers(_strip_redirects(s)[0])[0]
                                                          for s in _segments(inner)) if a), "a command")
        best = Step(text or f"Running {prog}")
    if irreversible:
        best.risk = IRREVERSIBLE
    elif sudo_any or system:
        best.risk = SYSTEM
    if best.risk:
        best.command = " ".join(inner.split())[:300]
    if best.changes and touched:
        best.touched = {kind: tuple(dict.fromkeys(things)) for kind, things in touched.items()}
    return best


# -- tools --

def _os_tool(tool: str, a: dict) -> Step | None:
    """bombadil-os tools (os-mcp), for both providers."""
    name = str(a.get("name") or "")
    if tool == "show_panel":
        return Step(f"Opening {PANEL_WORDS.get(name, name or 'a panel')}",
                    f"Opened {PANEL_WORDS.get(name, name or 'a panel')}")
    if tool == "hide_panel":
        return Step(f"Putting {PANEL_WORDS.get(name, name or 'a panel')} away")
    if tool == "create_app":
        raw = str(a.get("title") or "")
        title = _app_title(raw)
        files = a.get("files") if isinstance(a.get("files"), dict) else {}
        n = _lines(a.get("qml"), a.get("python"), *files.values())
        again = _app_exists(raw) if raw else False
        now, past = ("Changing", "Changed") if again else ("Building", "Made")
        return Step(f"{now} {title}" + (f", {n} lines" if n > 1 else ""), f"{past} {title}",
                    touched={"app": (title,)})
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
    if tool == "desk":
        # The line only: the desk is not part of a restore point, and a call can be refused or change
        # nothing, so no closing sentence claims it and no Undo is offered for it.
        op, widget = str(a.get("op") or ""), desk.title(a.get("widget"))
        if op == "hide":
            return Step(f"Putting {widget} away")
        if op == "show":
            return Step(f"Putting {widget} on the desk")
        if op == "move":
            to = f" to the {a['rail']} rail" if a.get("rail") in desk.RAILS else ""
            return Step(f"Moving {widget}{to}")
        if op in ("fold", "unfold"):
            return Step(f"{op.capitalize()}ing the desk")
        return Step("Looking at the desk")
    if tool == "job":
        op = str(a.get("op") or "")
        if op == "start":
            # Not a change to the system, so no closing sentence and no Undo: the desk counts it.
            title = _job_title(a.get("title")) or _timer_title(a.get("seconds"))
            return Step(f"Watching {title}" if title else "Starting a background job")
        if op == "stop":
            return Step(f"Stopped {jobs.title_of(a.get('id')) or 'a background job'}")
        return Step("Checking the background jobs")
    if tool == "show_card":
        return Step("Showing a card")
    if tool == "open_url":
        host = _host(str(a.get("url", "")))
        return Step(f"Opening {host}" if host else "Opening the browser")
    return None


# "Call me Dan" edits persona.toml with the agent's own tools. That is not a change to the system: no
# Undo, no summary entry, no Details row (a Step with no `done`), just a plain line while it happens.
PERSONA_STEP = "Changing how I talk to you"


def _is_persona(p) -> bool:
    """Is this path the user's persona.toml? Compared as written, with no I/O (the agent is told the path)."""
    try:
        return os.path.normpath(_expand_home(str(p))) == os.path.normpath(str(paths.persona_file()))
    except (TypeError, ValueError):
        return False


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
        p = str(a.get("file_path") or "")
        if _is_persona(p):
            return Step(PERSONA_STEP)
        what = _name(p) if p else "a file"
        step = Step(f"Writing {what}" + (f", {n} lines" if n > 1 else ""), f"Wrote {what}",
                    touched={"file": (p,)} if p else {})
        if _is_system_path(p):
            step.risk, step.command = SYSTEM, f"write {p}"
        return step
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        p = str(a.get("file_path") or a.get("notebook_path") or "")
        if _is_persona(p):
            return Step(PERSONA_STEP)
        what = _name(p) if p else "a file"
        step = Step(f"Editing {what}", f"Edited {what}", touched={"file": (p,)} if p else {})
        if _is_system_path(p):
            step.risk, step.command = SYSTEM, f"edit {p}"
        return step
    if name == "Glob":
        return Step("Looking for files")
    if name == "Grep":
        return Step(f"Searching for {_quote(a['pattern'])}" if a.get("pattern") else "Searching")
    if name == "LS":
        return Step("Looking through files")
    if name == "WebFetch":
        host = _host(str(a.get("url", "")))
        return Step(f"Reading {host}" if host else "Reading a web page")
    if name == "WebSearch":
        return Step(f"Searching the web for {_quote(a['query'])}" if a.get("query") else "Searching the web")
    if name == "TodoWrite":
        todos = a.get("todos") if isinstance(a.get("todos"), list) else []
        # The model can leave several under way; the last one listed is the current step.
        active = next((t for t in reversed(todos)
                       if isinstance(t, dict) and t.get("status") == "in_progress"), None)
        if active and (active.get("activeForm") or active.get("content")):
            return Step(str(active.get("activeForm") or from_description(active["content"]) or active["content"])[:90])
        if todos and all(isinstance(t, dict) and t.get("status") == "completed" for t in todos):
            return None   # the last one ticked off says nothing new
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
    changes = [c for c in changes if isinstance(c, dict)] if isinstance(changes, list) else []
    if not changes:
        return Step("Editing files", "Edited files")
    if all(_is_persona(c.get("path", "")) for c in changes):
        return Step(PERSONA_STEP)
    kinds = {c.get("kind") for c in changes}
    if len(changes) == 1:
        c = changes[0]
        name = _name(c.get("path", ""))
        verb = {"add": ("Writing", "Wrote"), "delete": ("Deleting", "Deleted")}.get(c.get("kind"), ("Editing", "Edited"))
        step = Step(f"{verb[0]} {name}", f"{verb[1]} {name}")
    else:
        verb = ("Deleting", "Deleted") if kinds == {"delete"} else ("Editing", "Edited")
        step = Step(f"{verb[0]} {len(changes)} files", f"{verb[1]} {len(changes)} files")
    step.touched = {"file": tuple(str(c["path"]) for c in changes if c.get("path"))}
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
            if _is_persona(p):
                return Step(PERSONA_STEP)
            return Step(f"Writing {_name(p)}" + (f", {n} lines" if n > 1 else ""), f"Wrote {_name(p)}")
    if name in ("Edit", "MultiEdit"):
        m = _PATH_RE.search(partial)
        if m:
            p = json.loads(f'"{m.group(1)}"')
            if _is_persona(p):
                return Step(PERSONA_STEP)
            return Step(f"Editing {_name(p)}", f"Edited {_name(p)}")
    return None


# -- the plan --

PLAN_STATUSES = ("pending", "in_progress", "completed")
MAX_PLAN = 24          # steps kept; a longer list could not be shown on the desk anyway
MAX_TASK_TEXT = 90     # characters of one step's words
TOUCH_KINDS = ("package", "service", "file", "app")

_TASK_CREATED = re.compile(r"\s*Task #(\S+) created successfully")
_TASK_LINE = re.compile(r"^#(\S+) \[(pending|in_progress|completed)\] (.+?)( \(.*\))?( \[blocked by .*\])?$")


@dataclass
class _Task:
    id: str | None             # None until the result of the TaskCreate that made it names it
    subject: str               # "Install ffmpeg"
    active: str | None         # "Installing ffmpeg": the agent's own words, or the subject read as a verb
    status: str = "pending"
    call: str | None = None    # the TaskCreate call this row is waiting on


def _task_text(text) -> str:
    return " ".join(str(text or "").split())[:MAX_TASK_TEXT]


def _active_form(form, subject: str) -> str | None:
    return _task_text(form) or from_description(subject)


def _task_id(a: dict) -> str | None:
    """The task a TaskUpdate names. Claude's own reducer takes `id` and `task_id` for `taskId` too."""
    for key in ("taskId", "id", "task_id"):
        if a.get(key) not in (None, ""):
            return str(a[key])
    return None


# -- the turn --

def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


def _touched_file(path: str) -> str:
    """One name for a file however the turn spelled it (~/a.txt, $HOME/a.txt, ./a.txt)."""
    return os.path.normpath(_expand_home(path))


class Narrator:
    """Follows one turn's events; says what to show on the line and how the turn ended."""

    def __init__(self):
        self.step: Step | None = None
        self.done: list[str] = []
        self.made: list[str] = []   # apps this turn made (not changed), for turns.jsonl
        self.irreversible = False
        self.system = False
        self.touched: dict[str, set[str]] = {}   # what the turn has changed, by kind
        self.said = ""           # the agent's words in the current text block
        self._partial: dict[int, dict] = {}
        self._plan: list[_Task] = []
        self._listing: set[str] = set()       # TaskList calls whose result is the whole table
        self._sent: list[dict] = []           # the plan take_plan gave last
        self._shown: tuple[dict, dict[str, int]] | None = None   # the line last returned, and the counts then

    # Each method returns the new line (a dict for a "status" event) or None when it did not change.

    def _set(self, step: Step | None) -> dict | None:
        if step is None:
            return None
        if step.changes and step.done not in self.done:
            self.done.append(step.done)
        if step.changes:
            for kind, things in step.touched.items():
                self.touched.setdefault(kind, set()).update(
                    _touched_file(t) if kind == "file" else t for t in things)
        if step.risk == IRREVERSIBLE:
            self.irreversible = True
        elif step.risk == SYSTEM:
            self.system = True
        self.step = step
        return {"text": step.text, "risk": step.risk, "command": step.command, "source": "step"}

    def on_event(self, ev: dict) -> dict | None:
        self._note_made(ev)
        self._track_plan(ev)
        line = self._line(ev)
        # The complete message repeats what its stream already showed; say each line once, unless
        # what it has touched so far moved on with it.
        if line is None or (line, self.touched_counts()) == self._shown:
            return None
        self._shown = (line, self.touched_counts())
        return line

    def _note_made(self, ev: dict) -> None:
        """Remember an app this turn creates for the first time ("Since last time you built ...")."""
        name = str(ev.get("name") or "")
        if ev.get("kind") != "tool" or name.split("__", 2)[1:] != ["bombadil-os", "create_app"]:
            return
        a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
        raw = str(a.get("title") or "")
        title = _app_title(raw) if raw.strip() else ""
        if title and not _app_exists(raw) and title not in self.made:
            self.made.append(title)

    def touched_counts(self) -> dict[str, int]:
        """How many things of each kind the turn has changed so far; kinds with none are left out."""
        return {kind: len(self.touched[kind]) for kind in TOUCH_KINDS if self.touched.get(kind)}

    def touched_text(self) -> str:
        """The counts in words: "1 package and 3 files so far", "" when nothing changed."""
        parts = [f"{n} {kind}{'' if n == 1 else 's'}" for kind, n in self.touched_counts().items()]
        if not parts:
            return ""
        return (", ".join(parts[:-1]) + " and " if len(parts) > 1 else "") + parts[-1] + " so far"

    # -- the plan: Claude's TaskCreate/TaskUpdate/TaskList and TodoWrite, Codex's list as TodoWrite --

    @property
    def plan(self) -> list[dict]:
        """The steps in order, {id, subject, active, status}. Rows the CLI has not confirmed yet
        come last, as in its own list. When several are in progress the last one is the current step."""
        rows = sorted(self._plan, key=lambda t: t.id is None)
        return [{"id": t.id, "subject": t.subject, "active": t.active, "status": t.status} for t in rows]

    def take_plan(self) -> list[dict] | None:
        """The whole plan if it differs from the one given last, else None. Apart from on_event's
        answer because ticking a step off changes the plan and not the line."""
        plan = self.plan
        if plan == self._sent:
            return None
        self._sent = plan
        return [dict(row) for row in plan]

    def _task(self, tid: str | None) -> _Task | None:
        return next((t for t in self._plan if tid is not None and t.id == tid), None)

    def _track_plan(self, ev: dict):
        kind = ev.get("kind")
        # A subagent's list is its own; it never enters the turn's.
        if kind not in ("tool", "tool_result") or ev.get("parent"):
            return
        call = ev.get("id") if isinstance(ev.get("id"), str) else None
        if kind == "tool_result":
            self._plan_result(call, str(ev.get("output") or ""), bool(ev.get("error")))
            return
        a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
        name = ev.get("name")
        if name == "TaskCreate":
            self._plan_create(call, a)
        elif name == "TaskUpdate":
            self._plan_update(a)
        elif name == "TaskList" and call:
            self._listing.add(call)
        elif name == "TodoWrite":
            self._plan_todos(a.get("todos"))

    def _plan_create(self, call: str | None, a: dict):
        subject = _task_text(a.get("subject"))
        # Without the call's id its result could never name the row; without a subject there is
        # nothing to show.
        if not call or not subject or len(self._plan) >= MAX_PLAN:
            return
        form = a.get("activeForm") or a.get("active_form")
        self._plan.append(_Task(None, subject, _active_form(form, subject), call=call))

    def _plan_result(self, call: str | None, output: str, error: bool):
        if call is None:
            return
        if call in self._listing:
            self._plan_reseed(output)
            return
        row = next((t for t in self._plan if t.call == call), None)
        if row is None:
            return
        m = None if error else _TASK_CREATED.match(output) or re.search(r"#(\d+)", output)
        if m is None:
            self._plan.remove(row)   # it failed, or made a task nobody can name later
            return
        # A number seen again is the task made last, not two.
        self._plan = [t for t in self._plan if t is row or t.id != m.group(1)]
        row.id, row.call = m.group(1), None

    def _plan_update(self, a: dict):
        tid = _task_id(a)
        if tid is None:
            return
        row = self._task(tid)
        status = a.get("status")
        if status == "deleted":
            if row is not None:
                self._plan.remove(row)
            return
        status = status if status in PLAN_STATUSES else None
        subject = _task_text(a.get("subject"))
        form = a.get("activeForm") or a.get("active_form")
        if row is None:
            # Made in an earlier turn, or never seen: with no subject there is nothing to show,
            # and the bare number is not a step.
            if subject and len(self._plan) < MAX_PLAN:
                self._plan.append(_Task(tid, subject, _active_form(form, subject), status or "pending"))
            return
        if subject:
            row.subject, row.active = subject, _active_form(form, subject)
        elif _task_text(form):
            row.active = _task_text(form)
        if status:
            row.status = status

    def _plan_reseed(self, output: str):
        """A TaskList result is the whole list: `#3 [in_progress] Install ffmpeg (owner) [blocked by #1]`."""
        rows = []
        for line in output.splitlines():
            m = _TASK_LINE.match(line.strip())
            if m is None:
                continue
            known = self._task(m.group(1))
            subject = known.subject if known else _task_text(m.group(3))
            rows.append(_Task(m.group(1), subject, known.active if known else from_description(subject),
                              m.group(2)))
        if not rows and not output.strip().lower().startswith("no tasks"):
            return   # not a list at all (an error, say): keep what is known
        self._plan = (rows + [t for t in self._plan if t.id is None])[:MAX_PLAN]

    def _plan_todos(self, todos):
        """TodoWrite carries the whole list every time; a step's id is its place in it."""
        if not isinstance(todos, list):
            return
        rows = []
        for t in todos:
            subject = _task_text(t.get("content")) if isinstance(t, dict) else ""
            if not subject:
                continue
            status = t.get("status") if t.get("status") in PLAN_STATUSES else "pending"
            rows.append(_Task(str(len(rows) + 1), subject, _active_form(t.get("activeForm"), subject),
                              status))
        self._plan = rows[:MAX_PLAN]

    def _line(self, ev: dict) -> dict | None:
        kind = ev.get("kind")
        if kind == "tool":
            a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
            if ev.get("name") == "TaskUpdate" and a.get("status") == "in_progress" and not a.get("activeForm"):
                row = None if ev.get("parent") else self._task(_task_id(a))
                if row is not None:
                    self.said = ""
                    return self._set(Step(row.active or row.subject))
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
            self.said += str(ev.get("text") or "")
            line = self.said.strip().splitlines()[-1].strip() if self.said.strip() else ""
            if not line:
                return None
            return {"text": line[-200:], "risk": None, "command": None, "source": "agent"}
        if kind == "text":
            self.said = ""
            text = str(ev.get("text") or "").strip()
            if not text:
                return None
            return {"text": text.splitlines()[-1].strip()[-200:], "risk": None, "command": None, "source": "agent"}
        if kind == "output":
            # A typed "!command": its newest output line, keeping the command's mark.
            text = str(ev.get("text") or "").strip()
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
