/*
 * Messages and folders as the service sees them.
 *
 * Thunderbird's message ids are numbers that change when it restarts, so a message is named by its key
 * (docs/MAIL.md): the Message-ID header without angle brackets, else "fp:" and a SHA-1 of its author, date
 * and subject. Thunderbird gives a message with no Message-ID one of its own that starts with "md5:", which
 * is how those are told from the rest. A key is turned back into a message by asking for the Message-ID
 * (the folders of an account are searched), and for "fp:" keys by looking through the account newest first.
 *
 * Folders are told apart by what Thunderbird says they are for (`specialUse`), never by their names: its own
 * Junk folder is shown as "Spam" and its Archive as "Archives", and a server may call them anything. A
 * folder with no use of its own (Archive/2026, which Thunderbird makes when it files mail by year) is what
 * the folder it sits in is, when that is an archive, a trash, a junk, a sent or a drafts folder.
 *
 * What is kept in memory is bounded by things other than the size of the mailbox: the folder tables (one per
 * account, as many entries as the account has folders, dropped after a minute and when folders change) and
 * a short list of recently seen messages' Thunderbird ids, so that opening a message just listed does not
 * mean searching for it. No message is kept.
 */

import { parseMailbox, parseMailboxes } from "./addr.js";
import { badRequest, notFound } from "./errors.js";
import { withDeadline } from "./clock.js";
import { abort, folderStream, newestFirst } from "./streams.js";

export const MAX_KEY = 500;           // the service drops a key of more than 512 characters
const TABLE_TTL_MS = 60_000;
const HINTS_MAX = 256;
const PROBE_CONCURRENCY = 6;
const PROBE_BUDGET_MS = 3000;         // what telling which of a page's mails have attachments may take
const READ_BUDGET_MS = 8000;          // the service gives up on a read at 10 s
const SCAN_MAX = 20_000;
const SCAN_FOLDERS = 40;              // an account with hundreds of labels is not searched in all of them at once
const SCAN_BUDGET_MS = 6000;
const LOCAL_ONLY = new Set(["none", "local", "rss", "nntp"]);   // accounts that are not somewhere mail arrives from

const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

// Thunderbird's special uses, most telling first, and what each is for here.
const USES = [
  ["inbox", "inbox"],
  ["drafts", "drafts"],
  ["sent", "sent"],
  ["trash", "trash"],
  ["junk", "junk"],
  ["archives", "archive"],
  ["templates", "hidden"],
  ["outbox", "hidden"],
];
const INHERITED = new Set(["archive", "trash", "junk", "sent", "drafts"]);

/** What a folder is for from its special uses, or "" when it has none. */
export function kindFromUse(specialUse) {
  const uses = Array.isArray(specialUse) ? specialUse : [];
  for (const [use, kind] of USES) {
    if (uses.includes(use)) {
      return kind;
    }
  }
  return "";
}

/** The folder kinds the service knows: junk, templates and the outbox are "other" to it. */
export const publicKind = kind => (kind === "junk" || kind === "hidden" || !kind ? "other" : kind);

// -- keys --

export async function sha1Hex(text) {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

/** The Message-ID of a message without angle brackets, or "" when it has none worth using. */
export function realMessageId(header) {
  let id = String(header.headerMessageId ?? "").trim();
  if (id.startsWith("<") && id.endsWith(">")) {
    id = id.slice(1, -1).trim();
  }
  return !id || id.startsWith("md5:") || id.length > MAX_KEY || CONTROL.test(id) ? "" : id;
}

export const seconds = header => {
  const ms = header.date instanceof Date ? header.date.getTime() : Date.parse(header.date);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
};

export async function messageKey(header) {
  return realMessageId(header) || "fp:" + (await sha1Hex(`${header.author}|${seconds(header)}|${header.subject}`));
}

const timeOf = header => {
  const ms = header.date instanceof Date ? header.date.getTime() : Date.parse(header.date);
  return Number.isFinite(ms) ? ms / 1000 : 0;
};

export const isInline = attachment =>
  String(attachment.contentDisposition ?? "").toLowerCase() === "inline" && Boolean(attachment.contentId);

function walk(folders, inherited, out) {
  for (const folder of folders ?? []) {
    const own = kindFromUse(folder.specialUse);
    const kind = own || (INHERITED.has(inherited) ? inherited : "other");
    out.push({ id: folder.id, path: folder.path, name: folder.name, kind });
    walk(folder.subFolders, kind, out);
  }
}

/** Run `fn` over `items` with at most `limit` in flight; the results in order. */
export async function pool(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await fn(items[index], index);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

export class Mailbox {
  constructor({ messenger, clock }) {
    this.messenger = messenger;
    this.clock = clock;
    this.tables = new Map();   // account id -> {at, folders, byId, byPath, byKind}
    this.hints = new Map();    // "<account>\n<key>" -> {id, attachments}, the newest last
  }

  // -- accounts and folders --

  /** Mail accounts, each with its folders; also what the folder tables are made from. */
  async accounts() {
    const all = await this.messenger.accounts.list(true);
    const mail = all.filter(account => !LOCAL_ONLY.has(account.type));
    for (const account of mail) {
      this.adopt(account);
    }
    for (const id of [...this.tables.keys()]) {
      if (!all.some(account => account.id === id)) {
        this.tables.delete(id);
      }
    }
    return mail;
  }

  adopt(account) {
    const folders = [];
    walk(account.folders, "", folders);
    const byKind = new Map();
    for (const folder of folders) {
      byKind.set(folder.kind, [...(byKind.get(folder.kind) ?? []), folder]);
    }
    const table = {
      at: this.clock.now(),
      folders,
      byId: new Map(folders.map(f => [f.id, f])),
      byPath: new Map(folders.map(f => [f.path, f])),
      byKind,
    };
    this.tables.set(account.id, table);
    return table;
  }

  invalidate(accountId) {
    if (accountId === undefined) {
      this.tables.clear();
    } else {
      this.tables.delete(accountId);
    }
  }

  async table(accountId) {
    const known = this.tables.get(accountId);
    if (known && this.clock.now() - known.at < TABLE_TTL_MS) {
      return known;
    }
    let account = null;
    try {
      account = await this.messenger.accounts.get(accountId, true);
    } catch {
      // handled below: there is no such account
    }
    if (!account) {
      this.tables.delete(accountId);
      throw notFound("Thunderbird has no such account.");
    }
    return this.adopt(account);
  }

  /** The folders of an account for one kind ("inbox", "sent", ..., or "junk" and "other"), possibly none. */
  async foldersOf(accountId, kind) {
    return (await this.table(accountId)).byKind.get(kind) ?? [];
  }

  /** What a folder of a message is for, as the service names it. */
  async kindOf(folder) {
    if (!folder) {
      return "other";
    }
    const own = kindFromUse(folder.specialUse);
    if (own) {
      return publicKind(own);
    }
    let table = await this.table(folder.accountId);
    let found = table.byPath.get(folder.path);
    if (!found && this.clock.now() - table.at > 2000) {
      this.invalidate(folder.accountId);
      table = await this.table(folder.accountId);
      found = table.byPath.get(folder.path);
    }
    return publicKind(found?.kind);
  }

  // -- messages --

  hintKey(accountId, key) {
    return `${accountId}\n${key}`;
  }

  remember(accountId, key, id, attachments) {
    const hk = this.hintKey(accountId, key);
    const before = this.hints.get(hk);
    this.hints.delete(hk);
    this.hints.set(hk, { id, attachments: attachments ?? before?.attachments });
    if (this.hints.size > HINTS_MAX) {
      this.hints.delete(this.hints.keys().next().value);
    }
  }

  forget(accountId, key) {
    this.hints.delete(this.hintKey(accountId, key));
  }

  /**
   * The messages of Thunderbird's headers as the service's EMsg. Whether a message has attachments is not in
   * a header, so it is asked of Thunderbird for the page (a few at a time, for no longer than a few seconds,
   * and what was asked once is remembered); a message it could not be asked about says false.
   */
  async emsgs(headers, { probe = true } = {}) {
    const keys = await Promise.all(headers.map(messageKey));
    const kinds = await Promise.all(headers.map(header => this.kindOf(header.folder)));
    const flags = headers.map((header, i) => this.hints.get(this.hintKey(header.folder?.accountId, keys[i]))?.attachments);
    if (probe) {
      const end = this.clock.now() + PROBE_BUDGET_MS;
      const todo = headers.map((_, i) => i).filter(i => flags[i] === undefined);
      await pool(todo, PROBE_CONCURRENCY, async i => {
        const left = end - this.clock.now();
        if (left <= 0) {
          return;
        }
        try {
          const listed = await withDeadline(this.clock, this.messenger.messages.listAttachments(headers[i].id), left, () => new Error("slow"));
          flags[i] = listed.some(attachment => !isInline(attachment));
        } catch {
          // not known: false, and not remembered, so the next page asks again
        }
      });
    }
    return headers.map((header, i) => {
      const account = header.folder?.accountId;
      this.remember(account, keys[i], header.id, flags[i]);
      return this.shape(header, keys[i], kinds[i], flags[i] === true);
    });
  }

  shape(header, key, kind, attachments) {
    return {
      key,
      account: header.folder?.accountId ?? "",
      folder: kind,
      from: parseMailbox(header.author),
      to: parseMailboxes(header.recipients),
      cc: parseMailboxes(header.ccList),
      subject: String(header.subject ?? ""),
      ts: timeOf(header),
      unread: header.read === false,
      flagged: header.flagged === true,
      attachments,
      thread: null,
      message_id: realMessageId(header),
    };
  }

  /**
   * The message an account's key names: {header, kind, candidates}. The same Message-ID can be in several
   * folders (Gmail keeps a mail in All Mail and in each label's folder), so every one found is given, and
   * `header` is the best of them by `rank` (a function of the folder kind, lower is better), the inbox first.
   * A message just listed is found again from what was remembered of it, which is one copy; `all` looks for the
   * rest, for what is done to every copy.
   */
  async locate(accountId, key, rank = kind => (kind === "inbox" ? 0 : 1), { all = false } = {}) {
    if (typeof accountId !== "string" || typeof key !== "string" || !key || key.length > MAX_KEY) {
      throw badRequest("That is not a message.");
    }
    const hint = all ? undefined : this.hints.get(this.hintKey(accountId, key));
    let found = [];
    if (hint) {
      try {
        const header = await this.messenger.messages.get(hint.id);
        if (header && header.folder?.accountId === accountId && (await messageKey(header)) === key) {
          found = [header];
        }
      } catch {
        // Thunderbird no longer has that id: the message moved, or it restarted
      }
    }
    if (found.length === 0) {
      this.forget(accountId, key);
      found = key.startsWith("fp:") ? await this.scan(accountId, key) : await this.byMessageId(accountId, key);
    }
    if (found.length === 0) {
      throw notFound("That message is not in Thunderbird any more.");
    }
    const candidates = await Promise.all(found.map(async header => ({ header, kind: await this.kindOf(header.folder) })));
    candidates.sort((a, b) => rank(a.kind) - rank(b.kind));
    this.remember(accountId, key, candidates[0].header.id);
    return { header: candidates[0].header, kind: candidates[0].kind, candidates };
  }

  async byMessageId(accountId, key) {
    const search = async () => {
      const found = [];
      let page = await this.messenger.messages.query({ accountId, headerMessageId: key });
      try {
        while (true) {
          found.push(...page.messages);
          if (found.length || !page.id) {
            return found;
          }
          page = await this.messenger.messages.continueList(page.id);
        }
      } finally {
        if (page && page.id) {
          abort(this.messenger, page.id);
        }
      }
    };
    return withDeadline(this.clock, search(), READ_BUDGET_MS, () => notFound("Thunderbird did not find that message in time."));
  }

  /** A message with no Message-ID: it has to be looked for, newest first, in every folder but the junk. */
  async scan(accountId, key) {
    const table = await this.table(accountId);
    const folders = table.folders
      .filter(folder => folder.kind !== "junk" && folder.kind !== "hidden")
      .slice(0, SCAN_FOLDERS);
    const end = this.clock.now() + SCAN_BUDGET_MS;
    let looked = 0;
    for await (const header of newestFirst(folders.map(folder => folderStream(this.messenger, folder.id)))) {
      if (++looked > SCAN_MAX || this.clock.now() > end) {
        break;
      }
      if (!realMessageId(header) && (await messageKey(header)) === key) {
        return [header];
      }
    }
    return [];
  }
}
