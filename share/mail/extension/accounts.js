/*
 * The `accounts` rows, and what can be said about whether an account is still setting itself up.
 *
 * Thunderbird's API names an account, its identities and its folders, and nothing about the state of its
 * connection: there is no "signed in", no "syncing", no error. So the state here is a guess from what can
 * be seen, and it only ever says things that are true when they are seen:
 *
 * - "syncing" while an account has no Inbox yet (Thunderbird has not got its folders), or has never been seen
 *   with a message in its Inbox and was first seen less than two minutes ago. A mailbox that really is empty
 *   is "syncing" for those two minutes.
 * - After that, "ok" only if the server has been heard from: a message was seen, or there is a folder beside the
 *   Inbox (Thunderbird makes the Inbox of an account itself, whatever the server says, and the others, Sent,
 *   Archive and the rest, it has only from the server's list). An account that has an Inbox and nothing else,
 *   an empty one, is a sign-in that never worked as often as it is an empty mailbox; it is "syncing", with a
 *   detail that says nothing has come from it, and for no more than five minutes: then it is an "error".
 * - "error" when there is still no Inbox after five minutes, which in practice is a sign-in that did not work.
 *   The detail says it may be that, and not that it is.
 * - "error" for an account that has a send Thunderbird did not answer and this add-on gave up on (sending.js): it is
 *   waiting on a dialog nobody can click, and cannot send from that account until it is started again.
 * - Never "signin" and never "blocked": nothing in the API says either, so the service finds out its own way
 *   (a sign-in window Thunderbird opens, an admin's refusal) and its own notes are kept.
 *
 * `changes()` is what the `sync` events are made from: the accounts whose guess changed since the last time.
 */

import { pool } from "./mailbox.js";

const MAIL_GRACE_MS = 2 * 60 * 1000;
const FOLDERS_GRACE_MS = 5 * 60 * 1000;
const CONCURRENCY = 4;

const FETCHING = "Thunderbird is fetching this account's mail for the first time.";
const NOTHING_YET = "Nothing has come from this account yet.";
const NO_INBOX = "Thunderbird has not got this account's folders. The sign-in may not have worked.";
const NOTHING = "Thunderbird has not got any mail or folders from this account. The sign-in may not have worked.";
const STUCK = "Thunderbird is stuck on a mail it was sending, in a dialog nobody can see. It has to be restarted before it can send from this account.";
// What the server's own folder list gives; Thunderbird makes the Inbox, and the outbox and templates, itself.
const FROM_SERVER = ["sent", "drafts", "trash", "archive", "junk", "other"];

export class AccountsView {
  constructor({ messenger, clock, mailbox, stuck = () => false }) {
    this.messenger = messenger;
    this.clock = clock;
    this.mailbox = mailbox;
    this.stuck = stuck;           // account id -> whether a send for it was given up on and is still not answered
    this.firstSeen = new Map();   // account id -> when it was first seen
    this.hadMail = new Set();     // accounts seen with a message in their Inbox
    this.told = new Map();        // account id -> the state the last `sync` event (or the first look) said
    this.pending = false;         // whether any account is still being waited on
    this.looked = false;          // whether `changes` has looked once
  }

  /** The rows of `accounts`, in the order Thunderbird lists them. */
  async rows() {
    const accounts = await this.mailbox.accounts();
    const rows = await pool(accounts, CONCURRENCY, account => this.row(account));
    const live = new Set(accounts.map(account => account.id));
    for (const map of [this.firstSeen, this.told]) {
      for (const id of [...map.keys()]) {
        if (!live.has(id)) {
          map.delete(id);
        }
      }
    }
    for (const id of [...this.hadMail]) {
      if (!live.has(id)) {
        this.hadMail.delete(id);
      }
    }
    this.pending = rows.some(row => row.state !== "ok");
    return rows;
  }

  async row(account) {
    const table = this.mailbox.tables.get(account.id) ?? this.mailbox.adopt(account);
    const inboxes = table.byKind.get("inbox") ?? [];
    let unread = 0;
    let total = 0;
    for (const folder of inboxes.slice(0, 4)) {
      try {
        const info = await this.messenger.folders.getFolderInfo(folder.id);
        unread += Number.isFinite(info.unreadMessageCount) ? info.unreadMessageCount : 0;
        total += Number.isFinite(info.totalMessageCount) ? info.totalMessageCount : 0;
      } catch {
        // a folder that cannot be counted is counted as empty
      }
    }
    const identities = (account.identities ?? []).map(identity => ({
      id: String(identity.id),
      email: String(identity.email ?? "").trim().toLowerCase(),
      name: String(identity.name ?? ""),
    }));
    const has = kind => (table.byKind.get(kind) ?? []).length > 0;
    const heard = FROM_SERVER.some(has);
    const { state, detail } = this.guess(account.id, inboxes.length > 0, total, heard);
    return {
      engine_id: account.id,
      name: String(account.name ?? ""),
      type: String(account.type ?? ""),
      emails: [...new Set(identities.map(identity => identity.email).filter(Boolean))],
      identities,
      folders: { inbox: has("inbox"), sent: has("sent"), drafts: has("drafts"), archive: has("archive"), trash: has("trash") },
      unread,
      state,
      detail,
    };
  }

  guess(id, hasInbox, total, heard) {
    const now = this.clock.now();
    if (!this.firstSeen.has(id)) {
      this.firstSeen.set(id, now);
    }
    if (total > 0) {
      this.hadMail.add(id);
    }
    const age = now - this.firstSeen.get(id);
    if (this.stuck(id)) {
      return { state: "error", detail: STUCK };
    }
    if (!hasInbox) {
      return age < FOLDERS_GRACE_MS ? { state: "syncing", detail: FETCHING } : { state: "error", detail: NO_INBOX };
    }
    if (this.hadMail.has(id)) {
      return { state: "ok", detail: "" };
    }
    if (age < MAIL_GRACE_MS) {
      return { state: "syncing", detail: FETCHING };
    }
    if (heard) {
      return { state: "ok", detail: "" };
    }
    return age < FOLDERS_GRACE_MS ? { state: "syncing", detail: NOTHING_YET } : { state: "error", detail: NOTHING };
  }

  /**
   * The `sync` events owed: one for each account whose state is not the one last told. The first look tells
   * nothing (the service asks for the accounts when it connects); an account that appears later is told if
   * it is not "ok".
   */
  async changes() {
    const first = !this.looked;
    this.looked = true;
    const rows = await this.rows();
    const events = [];
    for (const row of rows) {
      const before = this.told.get(row.engine_id);
      if (before !== row.state && !first && !(before === undefined && row.state === "ok")) {
        events.push({
          event: "sync",
          account: row.engine_id,
          state: row.state === "ok" ? "idle" : row.state,
          detail: row.detail,
        });
      }
      this.told.set(row.engine_id, row.state);
    }
    return events;
  }
}
