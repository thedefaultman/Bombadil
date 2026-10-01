/*
 * What Thunderbird tells the add-on, turned into what the service is told.
 *
 * - `new_mail` for mail that arrives in an Inbox (not in any other folder, and Thunderbird only watches the
 *   Inbox of an IMAP account for it anyway), the newest twenty of a burst, each as an EMsg.
 * - `accounts_changed` when an account or a folder is made, removed, renamed or moved. The folder tables
 *   are dropped at the same time, so the next op sees the folders as they are.
 * - `counts_changed` when messages are read, flagged, moved, copied or deleted, or a folder's counts change.
 * - `sync` when the guess in accounts.js about an account changes.
 *
 * Thunderbird says a great deal while it syncs (a folder's counts change for every message it fetches), so
 * nothing is passed on one for one: each kind is held for a short while and told once for all that came in
 * it. That is a fixed number of events a second however large the mailbox, and a timer that is only armed
 * while there is something to tell. An event Thunderbird has no name for in this version is skipped, not an
 * error: the add-on works without it, a little less alive.
 */

import { abort } from "./streams.js";

const NEW_MAX = 20;
const NEW_QUEUE_MAX = 8;
const COUNTS_MS = 500;
const ACCOUNTS_MS = 1000;
const SYNC_MS = 1000;
const RECHECK_MS = 15_000;

/** Calls `fn` once, `ms` after the first of a run of pokes; the pokes in that time are that one call. */
export class Coalesce {
  constructor(clock, ms, fn) {
    this.clock = clock;
    this.ms = ms;
    this.fn = fn;
    this.timer = null;
  }

  poke() {
    if (this.timer === null) {
      this.timer = this.clock.setTimeout(() => {
        this.timer = null;
        this.fn();
      }, this.ms);
    }
  }

  cancel() {
    if (this.timer !== null) {
      this.clock.clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

export class Events {
  constructor({ messenger, clock, mailbox, view, emit, log = () => {} }) {
    this.messenger = messenger;
    this.clock = clock;
    this.mailbox = mailbox;
    this.view = view;
    this.emit = emit;
    this.log = log;
    this.listeners = [];
    this.waiting = 0;                   // new-mail events being turned into messages
    this.tail = Promise.resolve();
    this.counts = new Coalesce(clock, COUNTS_MS, () => emit({ event: "counts_changed" }));
    this.accounts = new Coalesce(clock, ACCOUNTS_MS, () => {
      emit({ event: "accounts_changed" });
      this.sync.poke();
    });
    this.sync = new Coalesce(clock, SYNC_MS, () => this.check());
    this.recheck = new Coalesce(clock, RECHECK_MS, () => this.check());
    this.stopped = false;
  }

  start() {
    const { messages, folders, accounts } = this.messenger;
    this.listen(messages?.onNewMailReceived, (folder, list) => this.newMail(folder, list));
    for (const name of ["onUpdated", "onMoved", "onCopied", "onDeleted"]) {
      this.listen(messages?.[name], () => this.counts.poke());
    }
    this.listen(folders?.onFolderInfoChanged, () => {
      this.counts.poke();
      if (this.view.pending) {
        this.sync.poke();
      }
    });
    for (const name of ["onCreated", "onDeleted", "onRenamed", "onMoved"]) {
      this.listen(folders?.[name], () => this.folderChanged());
    }
    for (const name of ["onCreated", "onDeleted", "onUpdated"]) {
      this.listen(accounts?.[name], () => this.folderChanged());
    }
    return this.check();   // the first look: nothing is told, but what is waited on is known from here
  }

  stop() {
    this.stopped = true;
    for (const [event, fn] of this.listeners) {
      try {
        event.removeListener(fn);
      } catch {
        // a listener that is already gone
      }
    }
    this.listeners = [];
    for (const timer of [this.counts, this.accounts, this.sync, this.recheck]) {
      timer.cancel();
    }
  }

  listen(event, fn) {
    if (!event || typeof event.addListener !== "function") {
      return;
    }
    const wrapped = (...args) => {
      try {
        Promise.resolve(fn(...args)).catch(e => this.log("event", e));
      } catch (e) {
        this.log("event", e);
      }
    };
    event.addListener(wrapped);
    this.listeners.push([event, wrapped]);
  }

  folderChanged() {
    this.mailbox.invalidate();
    this.accounts.poke();
  }

  /** Mail arrived in `folder`: told when it is an Inbox, at most NEW_MAX of it, newest first. */
  newMail(folder, list) {
    const headers = Array.isArray(list?.messages) ? list.messages.slice() : [];
    if (list?.id) {
      abort(this.messenger, list.id);
    }
    this.counts.poke();
    if (headers.length === 0 || this.waiting >= NEW_QUEUE_MAX) {
      return undefined;
    }
    this.waiting++;
    const work = this.tail.then(async () => {
      try {
        if (this.stopped || (await this.mailbox.kindOf(folder)) !== "inbox") {
          return;
        }
        headers.sort((a, b) => b.date.getTime() - a.date.getTime());
        const messages = await this.mailbox.emsgs(headers.slice(0, NEW_MAX));
        this.emit({ event: "new_mail", account: folder.accountId, messages });
      } finally {
        this.waiting--;
      }
    });
    this.tail = work.then(() => {}, () => {});   // the caller of `newMail` is the one told of a failure
    return work;
  }

  /** Look at the accounts' states, tell what changed, and come back later while one is still being waited on. */
  async check() {
    try {
      for (const event of await this.view.changes()) {
        this.emit(event);
      }
    } catch (e) {
      this.log("sync", e);
    }
    if (this.view.pending && !this.stopped) {
      this.recheck.poke();
    }
  }
}
