"""Recipes: what is particular to each work tool that speaks MCP, kept as data (docs/CONNECT.md, "Work tools,
through MCP").

`mcpconn.McpDriver` is one driver for every service. What differs between Linear, Notion, Jira, Todoist and
ClickUp is only where the server is, which scopes to ask for, which tool lists what waits on the person, which
tools create an item and add a comment, and where in each answer the things Bombadil needs are. That is a TOML
file per service, `share/connect/recipes/<service>.toml`, so a fork adds a service by adding a file.

A recipe is checked when it is loaded and a bad one raises `RecipeError` with a sentence that names the file and
the key: a driver given one says so as its connection's note and does nothing else. `verified = false` is
required to be written down and stays until someone has run the recipe against the live server; the shipped
recipes say in their comments what was looked at and what is a guess.

The files are found in the installed share directory first (`BOMBADIL_SHARE` moves it, which is also how a
fork or a test puts its own in front), then in this checkout's `share/`.

Paths ("issues.0.id") are dotted keys, with a number for a place in a list. They are how a recipe says where in a
tool's answer (structured content, else JSON in the text) a value is, and, in `arg`, where in a tool's
arguments a card's field goes ("tasks.0.content" for a tool that wants a list of one).

What this module does not do: talk to anything. It reads files.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import paths

EDITS = ("line", "text", "date")
GRANTS = ("authorization_code", "refresh_token")
LOOPBACK = ("127.0.0.1", "localhost", "::1")

_SERVICE = re.compile(r"[a-z][a-z0-9_-]{0,30}")
_TOOL = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,30}")
_PATH = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,100}")


class RecipeError(Exception):
    """A recipe that cannot be used: str() is one plain sentence naming the file and what is wrong."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    edit: str
    required: bool
    arg: str


@dataclass(frozen=True)
class Action:
    """A tool the person's press can run: create an item, or comment on one."""
    tool: str
    fields: tuple[Field, ...]
    arguments: dict            # fixed arguments, always passed
    target: str                # comment only: the argument that names the item commented on
    noun: str                  # "issue", for the receipt when the answer has no id worth showing
    id_at: str                 # where the item's id is in the answer
    url_at: str
    url_template: str


@dataclass(frozen=True)
class Listing:
    tool: str
    arguments: dict
    items: str                 # where the list is in the answer
    why: str                   # the fixed phrase for a Task's `why`
    map: dict                  # Task key -> path in an item ("ref", "title", "status", "due", "url", "why")
    url_template: str
    extras: tuple              # ((label, path), ...): more rows for the card
    done: tuple                # statuses that mean nothing waits any more (lower case)


@dataclass(frozen=True)
class Context:
    """A lookup made first, once, whose answer fills `{name}` in the arguments (Atlassian's site id)."""
    tool: str
    arguments: dict
    bind: dict                 # name -> path in the answer


@dataclass(frozen=True)
class Recipe:
    service: str
    name: str
    url: str
    scopes: tuple
    write_scopes: tuple
    grants: tuple
    reads: str
    verified: bool
    client_metadata_url: str
    context: Context | None
    listing: Listing | None
    create: Action | None
    comment: Action | None
    source: str

    @property
    def can_post(self) -> bool:
        return self.create is not None or self.comment is not None

    @property
    def scope(self) -> str:
        """What the sign-in asks for: the read scopes, and the write scopes only when this recipe has something that
        writes. A fork that takes `[create]` and `[comment]` out asks for reading only."""
        wanted = list(self.scopes) + (list(self.write_scopes) if self.can_post else [])
        return " ".join(dict.fromkeys(wanted))

    def task_fields(self) -> dict:
        out = {}
        for name, action in (("create", self.create), ("comment", self.comment)):
            if action is not None:
                out[name] = [{"key": f.key, "label": f.label, "edit": f.edit, "required": f.required}
                             for f in action.fields]
        return out


# -- paths into answers and arguments --

def dig(data: Any, path: str) -> Any:
    """The value at a dotted path, or None when it is not there. "" is the answer itself."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def put(target: dict, path: str, value: Any) -> None:
    """Set the value at a dotted path in arguments, making dicts and lists on the way (a number is a list place)."""
    parts = path.split(".")
    node: Any = target
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        following = [] if not last and parts[i + 1].isdigit() else {}
        if isinstance(node, list):
            index = int(part)
            while len(node) <= index:
                node.append(None)
            if last:
                node[index] = value
            else:
                if node[index] is None:
                    node[index] = following
                node = node[index]
        else:
            if last:
                node[part] = value
            else:
                node = node.setdefault(part, following)


def fill(value: Any, variables: dict) -> Any:
    """Arguments with `{name}` replaced: a string that is only a placeholder becomes the variable itself (a number
    stays a number), a longer one has the variables written into it."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return variables[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]), value)
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    return value


def placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        return set().union(*(placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholders(v) for v in value)) if value else set()
    return set()


# -- where recipes are --

def share_dirs() -> list[Path]:
    """Where `connect/` can be, best first: the installed share (both layouts), then this checkout's."""
    mine = Path(__file__).resolve().parents[3] / "share" / "connect"
    found = [paths.share_dir() / "share" / "connect", paths.share_dir() / "connect", mine]
    return list(dict.fromkeys(found))


def available() -> list[str]:
    names = {f.stem for d in share_dirs() for f in (d / "recipes").glob("*.toml") if _SERVICE.fullmatch(f.stem)}
    return sorted(names)


def find(relative: str) -> Path | None:
    for d in share_dirs():
        if (d / relative).is_file():
            return d / relative
    return None


def load(service: str) -> Recipe:
    if not isinstance(service, str) or not _SERVICE.fullmatch(service):
        raise RecipeError("That is not a service I know.")
    path = find(f"recipes/{service}.toml")
    if path is None:
        raise RecipeError(f"I have no recipe for {service}.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RecipeError(f"{path.name} cannot be read: {exc}.") from exc
    return parse(raw, path.name, service)


def client_metadata() -> dict:
    """The document Bombadil publishes for itself (share/connect/client-metadata.json): the name the sign-in page shows
    and what the client says about itself. Absent or unreadable, Bombadil's own defaults."""
    path = find("client-metadata.json")
    doc: Any = {}
    if path is not None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            doc = {}
    doc = doc if isinstance(doc, dict) else {}
    return {"client_name": str(doc.get("client_name") or "Bombadil")[:80],
            "grant_types": [g for g in doc.get("grant_types", GRANTS) if isinstance(g, str)] or list(GRANTS),
            "response_types": ["code"]}


# -- reading a recipe --

def parse(raw: dict, where: str, service: str | None = None) -> Recipe:
    """A Recipe from parsed TOML, or RecipeError. `where` is the file's name, for the sentence."""

    def bad(what: str):
        raise RecipeError(f"{where}: {what}")

    def table(parent: dict, key: str, required: bool = False) -> dict | None:
        value = parent.get(key)
        if value is None:
            if required:
                bad(f"[{key}] is missing.")
            return None
        if not isinstance(value, dict):
            bad(f"{key} must be a table.")
        return value

    def text(parent: dict, key: str, limit: int, *, required: bool = True, default: str = "") -> str:
        value = parent.get(key, None if required else default)
        if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
            bad(f"{key} must be text of at most {limit} characters.")
        return value

    def tool(parent: dict, section: str) -> str:
        value = parent.get("tool")
        if not isinstance(value, str) or not _TOOL.fullmatch(value):
            bad(f"[{section}] tool must be the tool's name.")
        return value

    def at(parent: dict, key: str, section: str) -> str:
        value = parent.get(key, "")
        if not isinstance(value, str) or (value and not _PATH.fullmatch(value)):
            bad(f"[{section}] {key} must be a dotted path such as issues.0.id.")
        return value

    def arguments(parent: dict, section: str) -> dict:
        value = parent.get("arguments", {})
        if not isinstance(value, dict):
            bad(f"[{section}] arguments must be a table.")
        return value

    def scopes(server: dict, key: str) -> tuple:
        value = server.get(key, [])
        if not isinstance(value, list) or not all(isinstance(s, str) and _SCOPE.fullmatch(s) for s in value):
            bad(f"{key} must be a list of scope names.")
        return tuple(value)

    server = table(raw, "server", required=True)
    name_of_service = text(server, "service", 31)
    if not _SERVICE.fullmatch(name_of_service) or (service and name_of_service != service):
        bad("service must be the file's own name.")
    url = text(server, "url", 300)
    parts = urlsplit(url)
    if not parts.hostname or parts.scheme not in ("https", "http") or (
            parts.scheme == "http" and parts.hostname not in LOOPBACK):
        bad("url must be an https address (http only on this machine).")
    grants = server.get("grants", list(GRANTS))
    if (not isinstance(grants, list) or not grants or "authorization_code" not in grants
            or not all(g in GRANTS for g in grants)):
        bad("grants must be a list that includes authorization_code.")
    if not isinstance(server.get("verified"), bool):
        bad("verified must be written down, true or false.")
    metadata_url = text(server, "client_metadata_url", 300, required=False)

    def fields_of(action: dict, section: str) -> tuple:
        listed = action.get("fields")
        if not isinstance(listed, list) or not listed:
            bad(f"[{section}] needs at least one field.")
        out, seen = [], set()
        for f in listed:
            if not isinstance(f, dict):
                bad(f"[{section}] each field must be a table.")
            key = f.get("key")
            if not isinstance(key, str) or not _KEY.fullmatch(key) or key in seen:
                bad(f"[{section}] each field needs its own key in lower case.")
            seen.add(key)
            if f.get("edit") not in EDITS:
                bad(f"[{section}] field {key}: edit must be one of {', '.join(EDITS)}.")
            label = f.get("label")
            if not isinstance(label, str) or not label.strip() or len(label) > 40:
                bad(f"[{section}] field {key} needs a label of at most 40 characters.")
            required = f.get("required", False)
            if not isinstance(required, bool):
                bad(f"[{section}] field {key}: required must be true or false.")
            arg = f.get("arg", key)
            if not isinstance(arg, str) or not _PATH.fullmatch(arg):
                bad(f"[{section}] field {key}: arg must be the argument's name or a dotted path.")
            out.append(Field(key, label.strip(), f["edit"], required, arg))
        return tuple(out)

    def action_of(section: str) -> Action | None:
        spec = table(raw, section)
        if spec is None:
            return None
        result = table(spec, "result") or {}
        target = text(spec, "target", 60, required=False) if section == "comment" else ""
        if section == "comment" and not _PATH.fullmatch(target):
            bad("[comment] target must name the argument that says which item.")
        template = text(result, "url_template", 300, required=False)
        return Action(tool=tool(spec, section), fields=fields_of(spec, section), arguments=arguments(spec, section),
                      target=target, noun=text(spec, "noun", 30, required=False, default="item"),
                      id_at=at(result, "id", f"{section}.result"), url_at=at(result, "url", f"{section}.result"),
                      url_template=template)

    context = None
    spec = table(raw, "context")
    if spec is not None:
        bind = spec.get("bind")
        if (not isinstance(bind, dict) or not bind or not all(
                isinstance(k, str) and _KEY.fullmatch(k) and isinstance(v, str) and _PATH.fullmatch(v)
                for k, v in bind.items())):
            bad("[context] bind must say which names take which paths of the answer.")
        context = Context(tool(spec, "context"), arguments(spec, "context"), dict(bind))
    names = set(context.bind) if context else set()

    listing = None
    spec = table(raw, "list")
    if spec is not None:
        mapping = table(spec, "map", required=True)
        allowed = {"ref", "title", "status", "due", "url", "why"}
        if set(mapping) - allowed or not all(isinstance(v, str) and _PATH.fullmatch(v) for v in mapping.values()):
            bad(f"[list.map] may map only {', '.join(sorted(allowed))}, each to a dotted path.")
        if "ref" not in mapping or "title" not in mapping:
            bad("[list.map] must say where ref and title are.")
        extras = []
        for extra in spec.get("fields", []):
            if (not isinstance(extra, dict) or not isinstance(extra.get("label"), str)
                    or not isinstance(extra.get("at"), str) or not _PATH.fullmatch(extra["at"])):
                bad("[[list.fields]] each needs a label and an at path.")
            extras.append((extra["label"][:40], extra["at"]))
        done = spec.get("done", [])
        if not isinstance(done, list) or not all(isinstance(s, str) for s in done):
            bad("[list] done must be a list of statuses.")
        listing = Listing(
            tool=tool(spec, "list"), arguments=arguments(spec, "list"), items=at(spec, "items", "list"),
            why=text(spec, "why", 80, required=False), map=dict(mapping),
            url_template=text(spec, "url_template", 300, required=False), extras=tuple(extras),
            done=tuple(s.lower() for s in done))
        unknown = (placeholders(listing.arguments) - names - {"limit"}) | (
            placeholders(listing.url_template) - names - {"id"})
        if unknown:
            bad(f"[list] uses {{{sorted(unknown)[0]}}}, which nothing provides.")
    create, comment = action_of("create"), action_of("comment")
    for section, action in (("create", create), ("comment", comment)):
        if action is not None:
            unknown = (placeholders(action.arguments) - names) | (placeholders(action.url_template) - names - {"id"})
            if unknown:
                bad(f"[{section}] uses {{{sorted(unknown)[0]}}}, which nothing provides.")
    if context is not None and placeholders(context.arguments):
        bad("[context] arguments cannot use placeholders.")
    if listing is None and create is None and comment is None:
        bad("a recipe needs at least one of [list], [create] and [comment].")

    return Recipe(
        service=name_of_service, name=text(server, "name", 40), url=url, scopes=scopes(server, "scopes"),
        write_scopes=scopes(server, "write_scopes"), grants=tuple(grants), reads=text(server, "reads", 160),
        verified=server["verified"], client_metadata_url=metadata_url, context=context, listing=listing,
        create=create, comment=comment, source=where)


def label(value: Any, limit: int = 24) -> str | None:
    """A short id a person can read in a receipt ("LIN-42"), or None for one that is too long to be worth showing."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    shown = " ".join(str(value).split())
    return shown if 0 < len(shown) <= limit else None
