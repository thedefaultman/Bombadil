# Counting what you ask (loop core)

`ledger.py`, `route.py`, `habits.py`, `forms.py`, `offers.py`, `store.py` under `src/bombadil/loop/`.
Stdlib only. Nothing here is on a turn's path: it reads `turns.jsonl` and the per-turn logs after the
fact, calls no model, and a failure inside it costs a count, never a turn or the pill.

## What it does

1. `ledger` reads new rows of `turns.jsonl` by byte offset (torn last line, rotation and old rows are
   tolerated) into `Request`s, and a turn's log into tool events.
2. `route` turns a turn's tool events into topics (`app:passwords`, `net:wifi`, `git:push`, `files:home`,
   `kind:image`, `opened`…): what the turn did, not what he called it.
3. `habits` decides whether a request counts (`counted`) and whether two asks are the same request
   (`joins`), and keeps groups with decay.
4. `forms` says what a group could become (forms A to I as data; day one builds A, the word, and D,
   the app) and picks one. `offers` says when a group is ripe and how often he may be asked.
5. `store` keeps it all in `loop.db` and is the only thing the service needs.

## API

```python
store = LoopStore(path=None, *, app_list=None, built=None, config=None, logs_dir=None)
store.ingest(log=None, now=None) -> IngestResult     # idempotent, cheap when nothing is new
store.ripe_offer(now) -> offers.Offer | None         # the one offer to show; showing it records it
store.answer(group_id, op, form=None, now=None)      # accept | not_now | never | got_it | expired
store.groups(states=None, now=None, listed_only=False) / .group(id) / .members(id)
store.asks_report(limit, now) / .asks_text(limit, now)   # for `bombadil loop asks` and Noticed
store.said_no() / .bring_back(group_id) / .forget_asks() / .resting(now) / .waiting() / .status(now)
store.note_word_made(phrase) / .note_word_used(phrase) / .words_unused(days=28)
store.regroup(now)                                   # decide every stored ask again (after a rule change)
LoopStore.replay(turns_jsonl, now=None, corpus_dir=None, app_names=(), pairs=100) -> dict
offers.Offer.to_row(titles) -> dict                  # the Noticed row (`id` is the group id)
```

Every time-dependent call takes `now`; nothing reads a clock otherwise, so each rule is tested with
an injected time. Thresholds come from `config.toml` `[offers]` (`offers.Config`).

## Rules worth knowing

- Counted: typed asks that are not empty, shell, sign-in, private (route or words), over 300
  characters, stopped, failed, undone within 60 s, a rephrase within 120 s of a failed turn, or
  something he said Never to. Each has a reason in `requests.reason`.
- Private words: words about secrets (ssh keys, tokens, a keyring, "credentials", a seed phrase), a secret
  said aloud ("my password is ...", "sudo password ...", "login as x pass y", "my pin is ...", an SSN, a
  CVV), and what a secret looks like (a key's prefix such as `sk-`, `ghp_`, `AKIA` or `xox`, a JWT, a long
  token, a card or SSN number). Such an ask is never counted and its words are never written to `loop.db`.
  "Passwords" on its own (the passwords app), "login" and "sign in", and "pin" without a value or "my" in
  front count like any other ask.
- Same request: verb kinds agree, and `0.4 * text + 0.6 * route` is at least 0.5, or text alone is at
  least 0.8. Different named things never join. Open, ask and tell-me-when are one "looking" kind.
  Make and fix asks ignore the route.
- Ripe: 3 asks on 2 days in 21 days, worth 45 s or 3 steps each (or a near miss of an existing word).
  Bar of 5 when fewer than 3 of the last 10 offers were taken. One a day, three a week.
- Decay: an ask halves every 14 days, leaves the list at 30 days, loses its words at 90.

## Traps

- Group ids are `"g" + first member id` and members never move, so two groups that later turn out to
  be one stay two. Friction (retry, stop, undo) attaches to a group without raising its count.
- A Never is stored as a signature (stems and route), not a sentence. It survives `forget_asks` and
  keeps later look-alikes from counting; `bring_back` lifts it and regroups.
- `forget_asks` keeps the file offset and sets a mark, or the forgotten rows would be read in again.
- `regroup` leaves a group that was offered, answered or hidden exactly as it was.
- The verb and named things of an ask are fixed when it is read, so an app made later does not change
  what it was. Day one's word form does nothing if the phrase already exists: that offer is `got_it`.
- The service must call `note_word_made` when it writes a word row; the store cannot see `words.toml`.
- `db.schema` checks the version then creates, so two first opens race; `store._open` works around it.

## How far to trust it

The design was checked only on a synthetic corpus: 180 asks, 32 repeated requests among them and
the rest one-offs or asks that should not count (`tests/fixtures/loop/golden_asks.jsonl`), 150
hand-labelled pairs (`golden_pairs.jsonl`), and 49 more asks (`golden_holdout.jsonl`). One hand wrote the rules and the examples, so they share blind spots. No
real `turns.jsonl` from his machine was available. Numbers on the synthetic corpus: hand-labelled
pairs precision 0.99, recall 0.99; every pair of the corpus precision 0.995, recall 0.83; groups as
made, 0.995 and 0.88.

The second corpus was looked at once before anything was changed, when precision was 0.67 (route
topics were too coarse: different things done with one program read alike). That was fixed in the route
and verb rules, and it now scores 1.00 and 0.88. It is no longer unseen, so it says nothing about
new data. Known misses: paraphrases whose tools differ, asks with no verb ("I need ffmpeg"), style
paraphrases (joined only by kind), and `journalctl` routes that are too coarse.

The brief wants 90% precision on "same request" on his own asks. To check that, on his machine:

```python
import json, os
from bombadil.loop.store import LoopStore

r = LoopStore.replay(os.path.expanduser("~/.local/state/bombadil/turns.jsonl"))
print(r["reasons"], r["offers"])                  # what counted, and what would have been offered
for p in r["pairs"]:                              # label each: is `a` the same request as `b`?
    print(p["same"], "|", p["a"], "|", p["b"])
```

`replay` runs in memory, day by day, and touches nothing on disk. It reads each turn's log from where
the row says; if the logs were copied, pass `corpus_dir=` (without logs the route is empty and only
text joins). Pass `app_names=[...]` when this is not the machine the apps live on. The sheet has
neighbours inside groups (was it really the same?) and the closest pairs kept apart (should it have
been?); precision is the share of the `same: True` pairs he agrees with.

## Needs a real machine

- Precision and recall on his own asks (above), and whether the offers it would have made are ones he
  would have wanted.
- Everything Hyprland: nothing in this part touches it.
- The builders behind forms A and D (`words.py`, the app kit, `per_app_git`) live elsewhere; here they
  are named and described but not run.

## Known gaps

- A secret that is neither a keyword nor a known shape ("the word is swordfish") still counts, and a group that repeats it shows the sentence in Noticed and in the asks tool until the group is 90 days quiet.
