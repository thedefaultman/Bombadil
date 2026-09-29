"""What gets into the brain, what stays private, and which area a thing belongs to.

What gets in is what a person would call a thing. Caches, most dot-folders, build output,
editors' swap and temp files and the Trash stay out; a project is one thing with its
tracked files inside (git-ignored files stay out, see GitIgnore), and an app is one thing
with its data: writes under ~/Apps/<name>/data/ count as the app changing.

Private things are known by name only. Their contents are never read, previewed or
described: keys, keyrings, password stores and files that look like them.

Areas are where things live and where you go, never tags you keep: a project in
~/Projects, an app in ~/Apps, a folder in your home, a site you read, and System (/etc
and packages).
"""

import os
import re
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath

# Directory names that are never things, wherever they are.
NOISE_DIRS = {
    "node_modules", "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".gradle", ".cargo", ".rustup", ".npm", ".pnpm-store",
    ".yarn", ".cache", ".direnv", ".terraform", ".idea", ".vscode-server", ".eggs",
}
# Dot-entries under home that are things after all (anything else starting with "." is not).
DOT_ALLOWED = (
    ".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", ".gitconfig", ".bombadil",
    ".config/hypr", ".config/bombadil", ".config/quickshell", ".config/foot", ".config/git",
    ".ssh", ".gnupg", ".password-store", ".local/share/keyrings",
)
# The browser's profile (cookies, saved logins, caches) is not something a person made.
DOT_SKIPPED = (".config/bombadil/chromium",)
# Whose contents are never read: names only.
PRIVATE_DIRS = (".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".local/share/kwalletd")
# The same under /etc: keys, saved Wi-Fi and VPN passwords.
ETC_PRIVATE_DIRS = ("/etc/wireguard", "/etc/NetworkManager/system-connections", "/etc/ssl/private",
                    "/etc/openvpn", "/etc/iwd", "/etc/wpa_supplicant", "/etc/cryptsetup-keys.d", "/etc/ppp")
PRIVATE_SUFFIXES = (".kdbx", ".kdb", ".key", ".pem", ".p12", ".pfx", ".gpg", ".pgp", ".asc", ".jks",
                    ".keystore", ".vault", ".age")
PRIVATE_NAME = re.compile(r"(^id_[a-z0-9]+(\.pub)?$)|password|passwd|secret|credential|\.env(\..*)?$|^\.netrc$",
                          re.IGNORECASE)
# Editors' and browsers' in-between files: the rename that ends them is the save.
TEMP_NAME = re.compile(
    r"(~$)|(\.sw[a-p]$)|(\.tmp$)|(\.temp$)|(\.part$)|(\.partial$)|(\.crdownload$)|(\.kate-swp$)|(^#.*#$)"
    r"|(^\.~lock\..*#$)|(^4913$)|(\.bak$)|(^\.goutputstream-)|(\.lock$)|(\.pid$)|(-journal$)|(-wal$)|(-shm$)",
    re.IGNORECASE)
# Files in /etc that change on their own and mean nothing to a person.
ETC_NOISE = re.compile(r"^/etc/(ld\.so\.cache|\.pwd\.lock|\.updated|machine-id|adjtime|mtab|resolv\.conf\.bak)$"
                       r"|^/etc/pacman\.d/gnupg/|^/etc/ssl/certs/|^/etc/ca-certificates/extracted/")
TRASH = ".local/share/Trash"
# Folders in home that hold areas rather than being one: ~/Documents/Lease is the area "Lease".
CONTAINERS = ("Documents", "Desktop")


@dataclass(frozen=True)
class Verdict:
    kind: str            # "skip", "thing", "rollup" (counts as its app changing), "trash"
    private: bool = False
    app: str = ""        # for rollups: the app whose data this is


SKIP = Verdict("skip")


def _rel(path: str, home: str) -> str | None:
    home = home.rstrip("/")
    if path == home:
        return ""
    if path.startswith(home + "/"):
        return path[len(home) + 1:]
    return None


def _allowed_dot(rel: str) -> bool:
    return any(rel == a or rel.startswith(a + "/") for a in DOT_ALLOWED)


def private(path: str, home: str) -> bool:
    rel = _rel(path, home)
    name = PurePosixPath(path).name
    if rel is not None and any(rel == d or rel.startswith(d + "/") for d in PRIVATE_DIRS):
        return True
    if path.startswith("/etc/") and (name in ("shadow", "gshadow", "shadow-", "gshadow-")
                                     or path.startswith(("/etc/sudoers", "/etc/ssh/ssh_host_"))
                                     or any(path == d or path.startswith(d + "/") for d in ETC_PRIVATE_DIRS)):
        return True
    return name.lower().endswith(PRIVATE_SUFFIXES) or bool(PRIVATE_NAME.search(name))


def classify(path: str, home: str) -> Verdict:
    """Whether a path is a thing, and how. Pure: never touches the disk."""
    if not path.startswith("/") or "\n" in path:
        return SKIP
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return SKIP   # a name that is not UTF-8 (surrogate-escaped bytes) cannot be stored or shown
    if path.startswith("/etc/") or path == "/etc":
        if ETC_NOISE.search(path) or TEMP_NAME.search(PurePosixPath(path).name):
            return SKIP
        return Verdict("thing", private=private(path, home))
    rel = _rel(path, home)
    if rel is None:
        return SKIP
    if rel == "":
        return Verdict("thing")
    if rel == TRASH or rel.startswith(TRASH + "/"):
        return Verdict("trash")
    parts = rel.split("/")
    if any(p in NOISE_DIRS for p in parts):
        return SKIP
    if rel.startswith(".local/state/bombadil") or rel.startswith(".bombadil-smoke"):
        return SKIP
    if any(p.startswith(".") for p in parts) and not _allowed_dot(rel):
        return SKIP
    if any(rel == d or rel.startswith(d + "/") for d in DOT_SKIPPED):
        return SKIP
    if TEMP_NAME.search(parts[-1]):
        return SKIP
    if parts[0] == "Projects" and len(parts) > 2 and parts[2] == "target":
        return SKIP   # a Rust build
    if parts[0] == "Apps" and len(parts) >= 3 and parts[2] == "data":
        return Verdict("rollup", private=True, app=parts[1])
    return Verdict("thing", private=private(path, home))


@dataclass(frozen=True)
class Area:
    kind: str       # "folder", "project", "app", "system", "home"
    title: str
    path: str = ""  # the area's own folder, when it has one
    key: str = ""   # for areas without a folder ("system")


def area_of(path: str, home: str, is_dir: bool = False, is_project=None) -> Area | None:
    """The area a path belongs to. `is_project(dir)` says whether a folder directly under
    home is a git repository (a project even outside ~/Projects); None means no."""
    if path.startswith("/etc/") or path == "/etc":
        return Area("system", "System", key="system")
    rel = _rel(path, home)
    if rel is None:
        return None
    home = home.rstrip("/")
    if rel == "":
        return Area("home", "Home", home)
    parts = rel.split("/")
    if parts[0] == "Projects" and len(parts) >= 2:
        return Area("project", parts[1], f"{home}/Projects/{parts[1]}")
    if parts[0] == "Apps" and len(parts) >= 2:
        return Area("app", parts[1], f"{home}/Apps/{parts[1]}")
    if len(parts) == 1 and not is_dir:
        return Area("home", "Home", home)   # a file right in home
    if parts[0] in CONTAINERS and (len(parts) >= 3 or (len(parts) == 2 and is_dir)):
        return Area("folder", parts[1], f"{home}/{parts[0]}/{parts[1]}")
    top = f"{home}/{parts[0]}"
    if is_project is not None and is_project(top):
        return Area("project", parts[0], top)
    return Area("folder", parts[0], top)


def kind_of_folder(path: str, home: str) -> str:
    """What a folder is as a thing: a project, an app, or a plain folder."""
    rel = _rel(path, home)
    if rel is None:
        return "folder"
    parts = rel.split("/") if rel else []
    if len(parts) == 2 and parts[0] == "Projects":
        return "project"
    if len(parts) == 2 and parts[0] == "Apps":
        return "app"
    return "folder"


class GitIgnore:
    """Asks git which paths it ignores, a batch at a time, and remembers the answers.

    Git's own rules (.gitignore at every level, info/exclude, the global excludes file) are
    the only ones that are right, so git answers; one `check-ignore --stdin` per repository
    per batch keeps that cheap."""

    def __init__(self, runner=subprocess.run, ttl: float = 600.0, size: int = 20000):
        self._run = runner
        self.ttl = ttl
        self.size = size
        self._cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()
        self._roots: dict[str, tuple[float, str | None]] = {}

    def root(self, path: str, stop: str = "/") -> str | None:
        """The repository a path is in, or None. Cached per directory."""
        d = os.path.dirname(path.rstrip("/"))
        seen = []
        now = time.monotonic()
        found = None
        while d and d != "/" and d != stop.rstrip("/"):
            hit = self._roots.get(d)
            if hit and now - hit[0] < self.ttl:
                found = hit[1]
                break
            seen.append(d)
            if os.path.exists(os.path.join(d, ".git")):
                found = d
                break
            d = os.path.dirname(d)
        for s in seen:
            self._roots[s] = (now, found)
        return found

    def forget(self, repo: str) -> None:
        """A .gitignore changed: ask again."""
        prefix = repo.rstrip("/") + "/"
        for k in [k for k in self._cache if k.startswith(prefix)]:
            del self._cache[k]

    def ignored(self, paths: list[str], stop: str = "/") -> set[str]:
        out: set[str] = set()
        now = time.monotonic()
        by_repo: dict[str, list[str]] = {}
        for p in paths:
            hit = self._cache.get(p)
            if hit and now - hit[0] < self.ttl:
                if hit[1]:
                    out.add(p)
                continue
            repo = self.root(p, stop)
            if repo is not None:
                by_repo.setdefault(repo, []).append(p)
        for repo, ps in by_repo.items():
            rels = [os.path.relpath(p, repo) for p in ps]
            try:
                r = self._run(["git", "-C", repo, "check-ignore", "--stdin", "-z"], input="\0".join(rels) + "\0",
                              capture_output=True, text=True, timeout=10, check=False)
                hits = {x for x in (r.stdout or "").split("\0") if x} if r.returncode in (0, 1) else set()
            except (OSError, subprocess.TimeoutExpired):
                hits = set()
            for p, rel in zip(ps, rels):
                ign = rel in hits
                self._cache[p] = (now, ign)
                if ign:
                    out.add(p)
        while len(self._cache) > self.size:
            self._cache.popitem(last=False)
        return out
