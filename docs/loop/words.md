# Words, the launcher's noticed, and refine

Three small pieces: `loop/words.py` (the file of words Bombadil makes from repeats), `launcher.py`
(matches them, and knows the name "noticed"), and `loop/refine.py` (the one optional model call).
A word can only open an app or show a panel. Nothing here is on a turn's path except
`launcher.match`, which costs one `stat` more than before.

## words.toml (`paths.words_file()`)

```toml
[[word]]
phrase = "show me my passwords"          # normalised as launcher.normalize does
opens = { kind = "app", name = "passwords" }   # kind is "app" or "panel", nothing else
made = 1790000000                        # epoch seconds (bring_back sets it to now)
from_group = "g-ab12"                    # the loop's group, "" when written by hand
away = false                             # put away: kept, matches nothing
```

`from bombadil.loop import words`

| call | does |
|---|---|
| `words.add(phrase, opens, group=None, now=None, known=None) -> Word` | Makes a word. `opens` is `{"kind": "app"\|"panel", "name": ...}` (an app's folder name, or `browser`/`terminal`/`files`). Raises `WordRefused` (`.why` is `shell`, `empty`, `long`, `kind`, `target`, `means`, `word`) or `WordsFull` (`.why == "full"`); both are `WordError(ValueError)` and `str()` is one plain sentence. OSError if the file cannot be written. `known(phrase) -> str` says what the normalised phrase already does (`""` when free); the default is `launcher.means`. Does not check that the target exists. |
| `words.remove(phrase) -> Word \| None` | The word taken out (put away or not), or None. The undo of `add`. |
| `words.put_away(phrase) -> Word \| None`, `words.bring_back(phrase, now=None) -> Word \| None` | The changed word, or None when there was nothing to do. `bring_back` raises `WordsFull` at 30 active and restarts the 28 days. |
| `words.words_unused(last_used, days=28, now=None) -> [phrase]` | Active words not used for `days`; `last_used` maps phrase to epoch of last use, a word never used counts from `made`. Put them away with `put_away`. |
| `words.load() -> [Word]`, `words.active()`, `words.get(phrase)`, `words.lookup(phrase)` | Reads, cached by the file's (mtime, size, inode). Never raise: a file that does not parse gives `[]` and one stderr line (`words: words.toml does not parse ...`); a wrong row is skipped with one line. `lookup` is active words only. |
| `Word` | frozen: `phrase, kind, name, made, from_group, away`, and `.opens` -> `{"kind", "name"}`. |
| `MAX_WORDS = 30` (active), `MAX_PHRASE = 80`, `UNUSED_DAYS = 28` | |

Writes are all or nothing (temp file, `fsync`, `os.replace`), the text is parsed back before it
replaces anything, and a file that did not read cleanly is copied to `words.toml.bad` first.

## launcher.py

- `Action` has a new last field, `word: str = ""`: the phrase that matched, when a word did.
  agentd logs `via: "word"` and `word` from it.
- `match(text, app_list=None, *, use_words=True)`: order is unchanged (core commands, apps, panels,
  utility words), then "noticed", then words.toml **last**; other branches' projects, sessions and
  aliases go above the words line. A word matches the whole normalised text, or the text after an
  opening verb (`OPEN_VERBS`), never after close/hide. Returns
  `Action("app"|"panel", target, "open", title, word=phrase)`. A word that is put away, or whose app
  is gone, matches nothing. "!..." never matches.
- "noticed": `Action("noticed", verb, verb, "Noticed")` (`kind == "noticed"`, `verb` and `target`
  both `"open"` or `"hide"`) for `noticed`, `open|show|launch|... noticed` (open) and
  `hide|put away noticed` (hide). "close noticed" is not a word (goes to the agent). An app he made
  called Noticed wins every time, and then `entries()` lists no second "noticed". agentd must intercept
  `kind == "noticed"` before `Launcher.run`; `Launcher._noticed` answers `(False, "Noticed is not
  running.")` when it did not. "show noticed" after a hide is still verb `"open"`: the service decides.
- `means(text, app_list=None) -> str`: `"an app"`, `"a panel"`, `"a utility word"`, `"a command"` or `""`:
  what a phrase already does without words.toml. It is `words.add`'s default `known`.
- `entries()` ends with `{"name": "noticed", "title": "Noticed", "kind": "command", "words": ["noticed"]}`.

## refine.py

Off until `[refine] enabled = true` in `loop_dir()/config.toml` (`model = "..."` names the model; a
bad name is ignored). `from bombadil.loop import refine`

| call | does |
|---|---|
| `refine.ask(members, allowed_forms=FORMS, away=False, provider=None, now=None, runner=subprocess.run, timeout=60) -> Answer \| None` | The whole step. `members` is `[(id, prompt)]` (ids are strings, the ledger's per-turn ids); the first eight are used. Order: disabled -> None; a pinned answer -> returned free; not away, or today's call spent -> None; one call, counted before it is made; strict parse; pinned. `Answer(same_ids, label, form)`: the members that really are one request, a label of at most four words, an allowed form. None always means "keep the group as counted". Blocking (up to `timeout`): call it from a thread. |
| `refine.build_prompt(members, allowed_forms)` (strings or `(id, prompt)` pairs), `refine.parse_answer(text, n_members, allowed_forms) -> {"same", "label", "form"} \| None` | The fixed instruction with up to eight prompts quoted as JSON strings (numbered from 0); the strict validator. `same` can only name shown prompts (no extras, repeats, booleans), label at most four words and 40 characters once quotes and markup are removed (longer is refused, not cut), form must be allowed. An empty `same` is valid; drop the offer when fewer than two are left. |
| `refine.pinned(ids)`, `refine.pin(ids, answer)`, `refine.forget()` | `loop_dir()/refine.json`. Keyed by the sorted member ids; reused while fewer than two members differ. `forget()` drops the answers, not the day's count. |
| `refine.calls_left(now)`, `refine.enabled()`, `refine.model_name(provider)` | |
| `refine.oneshot_command(provider, model)`, `refine.run(provider, prompt, timeout, runner, model=None) -> str \| None` | Claude: `claude -p --output-format json --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence [--model M]`; codex: `codex exec --json --sandbox read-only --skip-git-repo-check [--model M] -`. The prompt goes on stdin; the working directory is `loop_dir()`; any failure is None plus a stderr line. |

The service passes `allowed_forms` from `forms.py` (the day-one default here is `("word", "app")`;
the ids must be the ones forms.py uses). Treat `label` as untrusted display text, never as an
instruction to put into a prompt.

## Traps

- words.toml is a file he may edit; `add` and friends rewrite it whole, so comments and keys the
  loop does not know are not kept (a file that did not read cleanly is saved as `words.toml.bad` first).
- Two processes adding in the same instant: the last writer wins and nothing is corrupted. The lock
  is per process.
- A word is matched by its exact normalised text. "show me my passwords?" matches it (the launcher
  drops `.!?,;:` at the ends); "show me my passwords please" does not.
- The launcher's `PillState.qml` hides commands after a verb, so "show noticed" works in agentd but
  the bar does not mark it as an exact word, and Tab does not complete "show noti". Only bare
  "noti" completes.
- The refine budget is spent before the call, so a CLI that hangs or a model that answers junk costs
  the day's call: the group stays as counted and is tried again tomorrow at the earliest.

## Not checked against anything real

No Claude or Codex CLI could be run where this was written, so `refine.oneshot_command` and the
parsers of each CLI's output were tested only against canned output shaped like what the existing
provider adapters already read. Unchecked: each flag above exists in the installed versions
(`--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, `--model haiku` as an alias,
codex `--sandbox read-only`), that `claude -p --output-format json` prints one object with a
`"result"` string, that `codex exec --json` ends with an `agent_message` item, that codex does not
start the MCP servers from his own config (there is no flag here to stop it), that a headless call
draws from the same plan window as a turn, and that either fast model answers the JSON shape. That is
why `[refine] enabled` defaults to false. Also needing a real machine: the launcher with real apps
under `~/Apps`, and a real Hyprland to see the line above the pill after a word opens an app.
