/*
 * A Thunderbird for the add-on's tests: a clock that stands still until it is told to move, and a `messenger`
 * with accounts, folders, messages, compose windows, a native-messaging port and the events, behaving as
 * the real one was seen to (tests/mail/lab/FINDINGS.md): the same shapes, the same limits, and the same
 * ways of going wrong, so that a call the real Thunderbird would refuse is refused here.
 *
 * What is faithful, because the add-on depends on it:
 * - `messages.list` returns pages and a list id, sorted only when it is asked to be; `continueList` and
 *   `abortList` are counted. A list stays in `world.lists` until its last page has been read, and `abortList`
 *   does not end that (it did not, in the real one: pages piled up in Thunderbird) but stops more pages being
 *   made (`quirks.prepared` more, two by default), so `world.lists.size === 0` after an op says the add-on
 *   read what it began.
 * - `messages.query` only knows the keys the real one does (anything else is an error), takes dates as Date
 *   objects only (a string or a number never answers), matches subject, body and text case-sensitively, and
 *   returns what it finds in no useful order.
 * - A moved message gets a new id; `getFull` has lower-case header names with arrays of values.
 * - `compose.setComposeDetails` can be made to ignore a field, to see the add-on refuse to send.
 */

import { webcrypto } from "node:crypto";

if (!globalThis.crypto) {
  globalThis.crypto = webcrypto;
}

// -- time --

export const settle = () => new Promise(resolve => setImmediate(resolve));

export class FakeClock {
  constructor(start = Date.UTC(2026, 9, 1, 12, 0, 0)) {
    this.time = start;
    this.timers = new Map();
    this.next = 1;
  }

  now() {
    return this.time;
  }

  setTimeout(fn, ms) {
    const id = this.next++;
    this.timers.set(id, { at: this.time + Math.max(0, ms), fn, id });
    return id;
  }

  clearTimeout(id) {
    this.timers.delete(id);
  }

  /** Move time on by `ms`, running every timer that falls due, in order, with the promises settled between. */
  async advance(ms) {
    const target = this.time + ms;
    await settle();
    for (;;) {
      let due = null;
      for (const timer of this.timers.values()) {
        if (timer.at <= target && (due === null || timer.at < due.at || (timer.at === due.at && timer.id < due.id))) {
          due = timer;
        }
      }
      if (due === null) {
        break;
      }
      this.timers.delete(due.id);
      this.time = Math.max(this.time, due.at);
      due.fn();
      await settle();
    }
    this.time = target;
    await settle();
  }

  pending() {
    return this.timers.size;
  }
}

// -- events --

export class FakeEvent {
  constructor() {
    this.listeners = [];
  }

  addListener(fn) {
    this.listeners.push(fn);
  }

  removeListener(fn) {
    this.listeners = this.listeners.filter(x => x !== fn);
  }

  fire(...args) {
    for (const fn of [...this.listeners]) {
      fn(...args);
    }
  }
}

// -- messages --

const never = () => new Promise(() => {});

function boundary() {
  return "B" + Math.random().toString(16).slice(2, 10);
}

/** The part tree Thunderbird's getFull would give for these words and attachments. */
function structure(spec) {
  const leaf = (type, body, partName) => ({
    contentType: type,
    partName,
    size: body.length,
    body,
    headers: { "content-type": [`${type}; charset=utf-8`] },
  });
  const words = [];
  if (spec.plain !== undefined) {
    words.push(["text/plain", spec.plain]);
  }
  if (spec.html !== undefined) {
    words.push(["text/html", spec.html]);
  }
  const attachments = spec.attachments ?? [];
  let top;
  const base = attachments.length ? "1.1" : "1";
  if (words.length === 2) {
    top = {
      contentType: "multipart/alternative",
      partName: base,
      headers: { "content-type": [`multipart/alternative; boundary="${boundary()}"`] },
      parts: words.map(([type, body], i) => leaf(type, body, `${base}.${i + 1}`)),
    };
  } else if (words.length === 1) {
    top = leaf(words[0][0], words[0][1], base);
  }
  const parts = [];
  if (top) {
    parts.push(top);
  }
  attachments.forEach((a, i) => {
    parts.push({
      contentType: a.contentType,
      partName: a.partName ?? `1.${parts.length + 1}`,
      name: a.name,
      size: a.content.length,
      headers: {
        "content-type": [`${a.contentType}; name="${a.name}"`],
        "content-disposition": [`${a.inline ? "inline" : "attachment"}; filename="${a.name}"`],
        ...(a.contentId ? { "content-id": [`<${a.contentId}>`] } : {}),
      },
    });
    void i;
  });
  if (attachments.length) {
    return [
      {
        contentType: "multipart/mixed",
        partName: "1",
        headers: { "content-type": [`multipart/mixed; boundary="${boundary()}"`] },
        parts,
      },
    ];
  }
  return parts;
}

/**
 * The words of a mail as Thunderbird's own `body` matching sees them: plain parts, and HTML as text. The tags are
 * taken out in one pass (a pattern that looks for the closing `>` from every `<` is quadratic on a hostile mail,
 * and this is the fake's doing, not the add-on's), and an unclosed tag runs to the end, as in a real parser.
 */
function bodyText(spec) {
  const html = spec.html ?? "";
  const lower = html.toLowerCase();
  let text = "";
  let at = 0;
  while (at < html.length) {
    const open = html.indexOf("<", at);
    if (open < 0) {
      text += html.slice(at);
      break;
    }
    text += html.slice(at, open);
    const close = html.indexOf(">", open);
    if (close < 0) {
      break;
    }
    if (/^<style[\s>]/i.test(html.slice(open, open + 8))) {
      const end = lower.indexOf("</style>", close);
      at = end < 0 ? html.length : end + 8;
    } else {
      at = close + 1;
    }
  }
  return `${spec.plain ?? ""}\n${text}`;
}

export class World {
  constructor({ clock = new FakeClock(), pageSize = 100, messagesSend = true } = {}) {
    this.clock = clock;
    this.pageSize = pageSize;
    this.accounts = new Map();
    this.folders = new Map();
    this.messages = new Map();
    this.nextMessage = 1;
    this.nextList = 1;
    this.lists = new Map();
    this.nextTab = 1;
    this.windows = new Map();       // window id -> compose state
    this.calls = [];                // [name, ...args] of the calls that matter to a test
    this.sent = [];                 // what was sent: {via, details, files}
    this.behavior = { send: { kind: "ok" } };
    this.ignore = new Set();        // compose fields setComposeDetails does not take
    this.quirks = {};
    this.contacts = [];
    this.ports = [];
    this.connectError = null;
    this.events = {
      accounts: { onCreated: new FakeEvent(), onDeleted: new FakeEvent(), onUpdated: new FakeEvent() },
      identities: { onCreated: new FakeEvent(), onDeleted: new FakeEvent(), onUpdated: new FakeEvent() },
      folders: {
        onCreated: new FakeEvent(),
        onDeleted: new FakeEvent(),
        onRenamed: new FakeEvent(),
        onMoved: new FakeEvent(),
        onFolderInfoChanged: new FakeEvent(),
      },
      messages: {
        onNewMailReceived: new FakeEvent(),
        onUpdated: new FakeEvent(),
        onMoved: new FakeEvent(),
        onCopied: new FakeEvent(),
        onDeleted: new FakeEvent(),
      },
    };
    this.messenger = this.build({ messagesSend });
  }

  // -- the mailbox --

  account(id, { name = id, type = "imap", identities } = {}) {
    const account = {
      id,
      name,
      type,
      identities: identities ?? [{ id: `${id}-id1`, email: `${id}@example.test`, name: `The ${id}` }],
      rootFolders: [],
    };
    this.accounts.set(id, account);
    return account;
  }

  folder(account, name, { specialUse = [], parent = null } = {}) {
    const path = `${parent ? parent.path : ""}/${name}`;
    const folder = {
      id: `${account.id}:${path}`,
      accountId: account.id,
      name,
      path,
      specialUse,
      subFolders: [],
    };
    (parent ? parent.subFolders : account.rootFolders).push(folder);
    this.folders.set(folder.id, folder);
    return folder;
  }

  /** A message in `folder`. Returns its header (what `messages.get` gives). */
  message(folder, spec = {}) {
    const id = this.nextMessage++;
    const header = {
      id,
      author: spec.author ?? "Alice Example <alice@example.org>",
      recipients: spec.recipients ?? [`${folder.accountId}@example.test`],
      ccList: spec.ccList ?? [],
      bccList: spec.bccList ?? [],
      subject: spec.subject ?? `Message ${id}`,
      date: spec.date ?? new Date(this.clock.now() - id * 60_000),
      read: spec.read ?? false,
      flagged: spec.flagged ?? false,
      headerMessageId: spec.headerMessageId ?? `m${id}@example.org`,
      folder: this.folderRef(folder),
      size: 1000,
    };
    this.messages.set(id, {
      header,
      folderId: folder.id,
      spec,
      raw: spec.raw ?? `Subject: ${header.subject}\r\n\r\n${spec.plain ?? ""}`,
    });
    return header;
  }

  folderRef(folder) {
    const { id, accountId, name, path, specialUse } = folder;
    return { id, accountId, name, path, specialUse: [...specialUse] };
  }

  /** A mailbox as most accounts have: Inbox, Sent, Drafts, Trash, Junk, Archives, with their special uses. */
  standard(accountId = "account1", options = {}) {
    const account = this.account(accountId, options);
    const folders = {};
    for (const [key, name, use] of [
      ["inbox", "INBOX", "inbox"],
      ["sent", "Sent", "sent"],
      ["drafts", "Drafts", "drafts"],
      ["trash", "Trash", "trash"],
      ["junk", "Spam", "junk"],
      ["archive", "Archives", "archives"],
    ]) {
      folders[key] = this.folder(account, name, { specialUse: [use] });
    }
    return { account, ...folders };
  }

  inFolder(folderId) {
    return [...this.messages.values()].filter(m => m.folderId === folderId);
  }

  headers(folderId) {
    return this.inFolder(folderId).map(m => ({ ...m.header }));
  }

  /** What a test sees of Thunderbird's view: the header now, by id. */
  header(id) {
    return this.messages.get(id)?.header;
  }

  headerOf(messageId) {
    return [...this.messages.values()].find(m => m.header.headerMessageId === messageId)?.header;
  }

  // -- the messenger --

  tree(folder, withFolders) {
    return withFolders ? { ...this.folderRef(folder), subFolders: folder.subFolders.map(f => this.tree(f, true)) } : undefined;
  }

  accountOut(account, withFolders) {
    const out = {
      id: account.id,
      name: account.name,
      type: account.type,
      identities: account.identities.map(i => ({
        composeHtml: this.quirks.htmlCompose === true,
        signature: "",
        signatureIsPlainText: true,
        ...i,
      })),
    };
    if (withFolders) {
      out.folders = account.rootFolders.map(f => this.tree(f, true));
    }
    return out;
  }

  /**
   * A page of `items`, and the list that holds the rest. As in Thunderbird, a list is kept until its last page
   * has been read (`abortList` does not forget it), and a page has the list's id until then.
   */
  page(items, kind, listId = null) {
    if (this.quirks.pageCost) {
      this.clock.time += this.quirks.pageCost;   // a slow Thunderbird: every page takes this long
    }
    const first = items.slice(0, this.pageSize);
    const rest = items.slice(this.pageSize);
    if (rest.length === 0) {
      if (listId) {
        this.lists.delete(listId);
      }
      return { id: null, messages: first.map(h => ({ ...h })) };
    }
    const id = listId ?? `${kind}${this.nextList++}`;
    this.lists.set(id, rest);
    return { id, messages: first.map(h => ({ ...h })) };
  }

  build({ messagesSend }) {
    const world = this;
    const hook = (name, ...args) => world.calls.push([name, ...args]);
    const messages = {
      ...world.events.messages,
      async get(id) {
        const m = world.messages.get(id);
        if (!m) {
          throw new Error(`Message not found: ${id}.`);
        }
        return { ...m.header };
      },
      async list(folderId, options = {}) {
        hook("list", folderId, options);
        if (!world.folders.has(folderId)) {
          throw new Error(`Invalid MailFolder: ${folderId}`);
        }
        const items = world.headers(folderId);
        if (options.sortType === "date") {
          items.sort((a, b) => (options.sortOrder === "ascending" ? 1 : -1) * (a.date - b.date));
        }
        return world.page(items, "list");
      },
      async query(info = {}) {
        hook("query", info);
        const allowed = [
          "folderId", "accountId", "headerMessageId", "fromDate", "toDate", "unread", "flagged", "attachment",
          "author", "recipients", "subject", "fullText", "body", "tags", "toMe", "fromMe", "size", "junk",
        ];
        for (const key of Object.keys(info)) {
          if (!allowed.includes(key)) {
            throw new Error(`Unexpected property: "${key}"`);
          }
        }
        for (const key of ["fromDate", "toDate"]) {
          if (key in info && !(info[key] instanceof Date)) {
            return never();   // the real one never answers when it is given anything but a Date
          }
        }
        let folders = [...world.folders.values()];
        if (info.folderId !== undefined) {
          const ids = Array.isArray(info.folderId) ? info.folderId : [info.folderId];
          folders = folders.filter(f => ids.includes(f.id));
        }
        if (info.accountId !== undefined) {
          folders = folders.filter(f => f.accountId === info.accountId);
        }
        const ids = new Set(folders.map(f => f.id));
        const found = [...world.messages.values()]
          .filter(m => ids.has(m.folderId))
          .filter(({ header, spec }) => {
            const has = (text, needle) => String(text).includes(needle);
            return (
              (info.headerMessageId === undefined || header.headerMessageId === info.headerMessageId) &&
              (info.fromDate === undefined || header.date >= info.fromDate) &&
              (info.toDate === undefined || header.date <= info.toDate) &&
              (info.unread === undefined || header.read === !info.unread) &&
              (info.flagged === undefined || header.flagged === info.flagged) &&
              (info.attachment === undefined || Boolean(spec.attachments?.length) === info.attachment) &&
              (info.author === undefined || has(header.author.toLowerCase(), info.author.toLowerCase())) &&
              (info.recipients === undefined ||
                [...header.recipients, ...header.ccList].some(r => r.toLowerCase().includes(info.recipients.toLowerCase()))) &&
              (info.subject === undefined || has(header.subject, info.subject)) &&
              (info.fullText === undefined || has(header.subject + (spec.plain ?? ""), info.fullText)) &&
              (info.body === undefined || has(bodyText(spec), info.body))
            );
          })
          .map(m => m.header)
          .sort((a, b) => (a.id * 7919) % 101 - (b.id * 7919) % 101);   // no useful order
        return world.page(found, "query");
      },
      async continueList(id) {
        hook("continueList", id);
        const rest = world.lists.get(id);
        if (!rest) {
          throw new Error(`No message list for id ${id}. Have you reached the end of a list?`);
        }
        return world.page(rest, "list", id);
      },
      async abortList(id) {
        hook("abortList", id);
        if (!world.lists.has(id)) {
          throw new Error(`No message list for id ${id}. Have you reached the end of a list?`);
        }
        // Thunderbird stops preparing pages, and keeps the list and the pages it has until they are read.
        const rest = world.lists.get(id);
        world.lists.set(id, rest.slice(0, (world.quirks.prepared ?? 2) * world.pageSize));
      },
      async getFull(id, options = {}) {
        hook("getFull", id, options);
        const m = world.messages.get(id);
        if (!m) {
          throw new Error(`Message not found: ${id}.`);
        }
        if (m.spec.failRead) {
          throw new Error("could not read");
        }
        const { header, spec } = m;
        const headers = {
          subject: [header.subject],
          from: [header.author],
          to: header.recipients,
          date: [header.date.toUTCString()],
          ...(header.headerMessageId && !header.headerMessageId.startsWith("md5:")
            ? { "message-id": [`<${header.headerMessageId}>`] }
            : {}),
          ...(spec.headers ?? {}),
        };
        return {
          contentType: "message/rfc822",
          headers: spec.noHeaders ? {} : headers,
          parts: structure(spec),
        };
      },
      async getRaw(id) {
        const m = world.messages.get(id);
        if (!m) {
          throw new Error(`Message not found: ${id}.`);
        }
        return new File([m.raw], "message.eml");
      },
      async listAttachments(id) {
        hook("listAttachments", id);
        const m = world.messages.get(id);
        if (!m) {
          throw new Error(`Message not found: ${id}.`);
        }
        if (m.spec.failAttachments) {
          throw new Error("no attachments for you");
        }
        const found = structure(m.spec)
          .flatMap(function flat(p) {
            return p.parts ? p.parts.flatMap(flat) : [p];
          })
          .filter(p => p.name);
        return found.map(p => {
          const a = (m.spec.attachments ?? []).find(x => x.name === p.name);
          return {
            contentType: p.contentType,
            name: p.name,
            partName: p.partName,
            size: p.size,
            contentDisposition: a?.inline ? "inline" : "attachment",
            ...(a?.contentId ? { contentId: a.contentId } : {}),
          };
        });
      },
      async getAttachmentFile(id, partName) {
        const m = world.messages.get(id);
        const found = (m?.spec.attachments ?? []).find(a => a.partName === partName);
        if (!found) {
          throw new Error(`Part ${partName} not found.`);
        }
        return new File([found.content], found.name, { type: found.contentType });
      },
      async update(id, changes) {
        hook("update", id, changes);
        const m = world.messages.get(id);
        if (!m) {
          throw new Error(`Message not found: ${id}.`);
        }
        Object.assign(m.header, changes);
        world.events.messages.onUpdated.fire({ ...m.header }, changes);
      },
      async move(ids, folderId) {
        hook("move", ids, folderId);
        const folder = world.folders.get(typeof folderId === "string" ? folderId : folderId?.id);
        if (!folder) {
          throw new Error(`Invalid MailFolder: ${folderId}`);
        }
        for (const id of ids) {
          world.relocate(id, folder);
        }
      },
      async archive(ids) {
        hook("archive", ids);
        for (const id of ids) {
          const m = world.messages.get(id);
          const account = world.accounts.get(m.header.folder.accountId);
          const year = account.rootFolders.find(f => f.name === "Archives") ?? world.folder(account, "Archives", { specialUse: ["archives"] });
          world.relocate(id, year);
        }
      },
    };
    if (messagesSend) {
      messages.sendMessage = async (details, options) => world.doSend("messages", null, details, options);
    }
    const compose = {
      async beginNew(details) {
        return world.openWindow("new", null, details);
      },
      async beginReply(id, type, details) {
        return world.openWindow(`reply:${type}`, id, details);
      },
      async beginForward(id, type, details) {
        return world.openWindow(`forward:${type}`, id, details);
      },
      async getComposeDetails(tabId) {
        return world.compose(tabId).read();
      },
      async setComposeDetails(tabId, details) {
        hook("setComposeDetails", tabId, details);
        return world.compose(tabId).write(details);
      },
      async listAttachments(tabId) {
        return world.compose(tabId).attachments.map(({ id, name, file }) => ({ id, name, size: file.size }));
      },
      async addAttachment(tabId, attachment) {
        hook("addAttachment", tabId, attachment.name);
        const w = world.compose(tabId);
        const entry = { id: w.nextAttachment++, name: attachment.name ?? attachment.file.name, file: attachment.file };
        w.attachments.push(entry);
        return { id: entry.id, name: entry.name, size: entry.file.size };
      },
      async removeAttachment(tabId, attachmentId) {
        const w = world.compose(tabId);
        w.attachments = w.attachments.filter(a => a.id !== attachmentId);
      },
      async sendMessage(tabId, options) {
        hook("compose.sendMessage", tabId);
        const w = world.compose(tabId);
        const result = await world.doSend("compose", w, w.read(), options);
        world.closeWindow(w.windowId);
        return result;
      },
    };
    const windows = {
      async remove(windowId) {
        hook("windows.remove", windowId);
        if (!world.windows.has(windowId)) {
          throw new Error(`Invalid window ID: ${windowId}`);
        }
        world.closeWindow(windowId);
      },
    };
    return {
      identities: { ...world.events.identities },
      accounts: {
        ...world.events.accounts,
        async list(withFolders) {
          return [...world.accounts.values()].map(a => world.accountOut(a, withFolders));
        },
        async get(id, withFolders) {
          const account = world.accounts.get(id);
          return account ? world.accountOut(account, withFolders) : null;
        },
      },
      folders: {
        ...world.events.folders,
        async getFolderInfo(folderId) {
          if (!world.folders.has(folderId)) {
            throw new Error(`Invalid MailFolder: ${folderId}`);
          }
          const here = world.headers(folderId);
          return { totalMessageCount: here.length, unreadMessageCount: here.filter(h => !h.read).length, favorite: false };
        },
      },
      messages,
      compose,
      windows,
      contacts: {
        async quickSearch(query) {
          hook("quickSearch", query);
          return world.contacts.filter(c => JSON.stringify(c.properties).toLowerCase().includes(query.searchString.toLowerCase()));
        },
      },
      runtime: {
        async getBrowserInfo() {
          if (world.quirks.browserInfoHangs) {
            return never();
          }
          return { name: "Thunderbird", version: "157.0", vendor: "Mozilla", buildID: "1" };
        },
        connectNative(name) {
          if (world.connectError) {
            throw world.connectError;
          }
          const port = new FakePort(name);
          world.ports.push(port);
          return port;
        },
        lastError: null,
      },
    };
  }

  relocate(id, folder) {
    const m = this.messages.get(id);
    if (!m) {
      throw new Error(`Message not found: ${id}.`);
    }
    this.messages.delete(id);
    const newId = this.nextMessage++;
    m.header = { ...m.header, id: newId, folder: this.folderRef(folder) };
    m.folderId = folder.id;
    this.messages.set(newId, m);
  }

  // -- compose windows --

  async openWindow(kind, messageId, details) {
    const tabId = this.nextTab++;
    const windowId = 1000 + tabId;
    const original = messageId === null ? null : this.messages.get(messageId)?.header;
    if (messageId !== null && !original) {
      throw new Error(`Message not found: ${messageId}.`);
    }
    const account = original ? this.accounts.get(original.folder.accountId) : [...this.accounts.values()][0];
    const state = new ComposeWindow(this, { tabId, windowId, kind, original, account, details });
    this.windows.set(windowId, state);
    this.calls.push(["open", kind, messageId]);
    return { id: tabId, windowId, type: "messageCompose" };
  }

  compose(tabId) {
    const w = [...this.windows.values()].find(x => x.tabId === tabId);
    if (!w) {
      throw new Error(`Invalid compose tab ID: ${tabId}`);
    }
    return w;
  }

  closeWindow(windowId) {
    this.windows.delete(windowId);
  }

  /** Both ways of sending end here: what was sent is recorded, and the behaviour is the test's to choose. */
  async doSend(via, window, details, options) {
    this.calls.push([`${via}.send`, options]);
    // `behavior.accounts` ({account id: behaviour}) is for a test where one account's outgoing server is not the others'
    const account = [...this.accounts.values()].find(a => a.identities.some(i => i.id === details.identityId));
    const behavior = this.behavior.accounts?.[account?.id] ?? this.behavior.send;
    const record = { via, details: JSON.parse(JSON.stringify({ ...details, attachments: undefined })), options };
    record.files = (window ? window.attachments : details.attachments ?? []).map(a => ({
      name: a.name ?? a.file?.name,
      file: a.file,
    }));
    const entry = { ...record, state: "pending" };
    this.sent.push(entry);   // what was handed to Thunderbird to send, whatever becomes of it
    if (behavior.kind === "hang") {
      entry.state = "hung";
      return never();
    }
    if (behavior.kind === "slow") {
      await new Promise(resolve => this.clock.setTimeout(resolve, behavior.ms));
    }
    if (behavior.kind === "fail") {
      entry.state = "failed";
      throw new Error(behavior.message ?? `${via}.sendMessage failed: Sending FAILED!`);
    }
    entry.state = "sent";
    const headerMessageId = `sent-${this.sent.indexOf(entry) + 1}@example.test`;
    const folder = [...this.folders.values()].find(f => f.specialUse.includes("sent"));
    const copies = [];
    if (folder && behavior.copy !== false) {
      copies.push(this.message(folder, { subject: details.subject, headerMessageId, read: true }));
    }
    return { mode: behavior.mode ?? "sendNow", headerMessageId, messages: behavior.quiet ? [] : copies };
  }
}

/** What Thunderbird puts at the foot of a plain text mail for an identity's signature. */
const signatureOf = identity => (identity?.signature ? `\n-- \n${identity.signature}\n` : "");

class ComposeWindow {
  /**
   * A compose window as Thunderbird makes it: the quoted mail and its attribution for a reply (with the cursor
   * on the empty lines above), the forwarded mail for a forward, the identity's signature at the foot, and
   * nothing but the signature for a new mail. Setting the body replaces all of that (it did in the real one),
   * and changing the identity swaps the signature of the old one for the new one's.
   */
  constructor(world, { tabId, windowId, kind, original, account, details }) {
    this.world = world;
    this.tabId = tabId;
    this.windowId = windowId;
    this.kind = kind;
    this.original = original;
    this.account = account;
    this.nextAttachment = 1;
    const sender = world.accountOut(account, false).identities[0];
    this.fields = {
      identityId: sender.id,
      from: `${sender.name} <${sender.email}>`,
      to: [],
      cc: [],
      bcc: [],
      subject: "",
      isPlainText: !sender.composeHtml,
      plainTextBody: "",
      body: "<p></p>",
      type: kind.split(":")[0],
      relatedMessageId: original?.id,
      isModified: false,
    };
    this.attachments = [];
    let made = "";
    if (original) {
      const spec = world.messages.get(original.id)?.spec ?? {};
      const said = spec.plain ?? "";
      this.fields.to = [original.author];
      if (kind.startsWith("forward")) {
        this.fields.subject = `Fwd: ${original.subject}`;
        made = `\n\n\n-------- Forwarded Message --------\nSubject: \t${original.subject}\nFrom: \t${original.author}\n\n\n\n${said}\n`;
        // A forward brings the original's attachments.
        (spec.attachments ?? []).forEach(a =>
          this.attachments.push({ id: this.nextAttachment++, name: a.name, file: new File([a.content], a.name) })
        );
      } else {
        this.fields.subject = `Re: ${original.subject}`;
        const who = original.author.replace(/\s*<.*>\s*$/, "");
        made = `\n\nOn 9/29/26 1:00 PM, ${who} wrote:\n${said.split("\n").map(line => `> ${line}`).join("\n")}\n`;
      }
    }
    this.fields.plainTextBody = made + signatureOf(sender);
    if (details) {
      this.write(details);
    }
  }

  read() {
    return JSON.parse(JSON.stringify(this.fields));
  }

  write(details) {
    for (const [key, value] of Object.entries(details)) {
      if (this.world.ignore.has(key)) {
        continue;
      }
      if (key === "isPlainText" && this.fields.isPlainText === false) {
        continue;   // a window that opened as HTML stays HTML (the real one did not make it plain text)
      }
      if (key === "identityId") {
        const identities = this.world.accountOut(this.account, false).identities;
        const identity = identities.find(i => i.id === value);
        if (!identity) {
          throw new Error(`Identity not found: ${value}`);
        }
        const old = signatureOf(identities.find(i => i.id === this.fields.identityId));
        const body = this.fields.plainTextBody;
        this.fields.plainTextBody = old && body.includes(old) ? body.replace(old, signatureOf(identity)) : body + signatureOf(identity);
      }
      this.fields[key] = value;
    }
  }
}

// -- the native-messaging port --

export class FakePort {
  constructor(name) {
    this.name = name;
    this.onMessage = new FakeEvent();
    this.onDisconnect = new FakeEvent();
    this.sent = [];
    this.closed = false;
    this.error = null;
  }

  postMessage(frame) {
    if (this.closed) {
      throw new Error("Attempt to postMessage on disconnected port");
    }
    this.sent.push(JSON.parse(JSON.stringify(frame)));
  }

  disconnect() {
    this.closed = true;
  }

  /** The host sends a frame. */
  receive(frame) {
    this.onMessage.fire(JSON.parse(JSON.stringify(frame)));
  }

  /** The host goes away. */
  die(error = null) {
    this.closed = true;
    this.error = error;
    this.onDisconnect.fire(this);
  }

  answers() {
    return this.sent.filter(f => "id" in f);
  }

  answerTo(id) {
    return this.sent.filter(f => f.id === id);
  }

  events(name) {
    return this.sent.filter(f => f.event === name);
  }
}

/**
 * The `continueList` calls that read on for the add-on's own use: not the ones that only empty a list it has
 * given up on (after `abortList`, which is what lets Thunderbird forget the list).
 */
export function readOn(world) {
  const aborted = new Set();
  const reads = [];
  for (const [name, id] of world.calls) {
    if (name === "abortList") {
      aborted.add(id);
    } else if (name === "continueList" && !aborted.has(id)) {
      reads.push(id);
    }
  }
  return reads;
}

/** The add-on, started on a fake Thunderbird. */
export async function start({ world = new World(), engineOptions = {} } = {}) {
  const { Engine } = await import("../../../share/mail/extension/engine.js");
  const { Link } = await import("../../../share/mail/extension/link.js");
  const logs = [];
  const log = (what, e) => logs.push([what, e && e.message ? e.message : e]);
  const engine = new Engine({ messenger: world.messenger, clock: world.clock, log, randomId: counter(), ...engineOptions });
  const link = new Link({ messenger: world.messenger, clock: world.clock, engine, log });
  await engine.init();
  link.start();
  await engine.start();
  await settle();
  return { world, engine, link, logs, port: () => world.ports.at(-1) };
}

function counter() {
  let n = 0;
  return () => `x${String(++n).padStart(4, "0")}`;
}

/** Let what is waiting for the next turn of the clock run: a few milliseconds, many times over. */
export async function drain(world, turns = 60) {
  for (let i = 0; i < turns; i++) {
    await world.clock.advance(1);
  }
}

/** Turns of the event loop, for up to `ms` of real time, until the port has an answer to `id` (or `ms` is up). */
async function answered(port, id, ms = 5000) {
  const end = Date.now() + ms;
  for (let turn = 0; port.answerTo(id).length === 0 && Date.now() < end; turn++) {
    // crypto.subtle answers from another thread, so on a busy machine some turns pass before it does
    await (turn % 100 === 99 ? new Promise(resolve => setTimeout(resolve, 1)) : settle());
  }
}

/** Ask the add-on something on its port and wait for the one answer. */
let nextId = 1;
export async function ask(app, op, args = {}, { advance = 0 } = {}) {
  const port = app.port();
  const id = nextId++;
  port.receive({ id, op, ...args });
  // Some of what the add-on waits on is real (crypto.subtle), so it is given real time to answer; time that is
  // faked only moves when the test says so. An answer that never comes is found out by the 5 s, not waited for
  // for ever, and one that is slow only costs the time it takes.
  await answered(port, id, advance ? 50 : 5000);
  if (advance) {
    await app.world.clock.advance(advance);
    await answered(port, id);
  }
  const answers = port.answerTo(id);
  if (answers.length !== 1) {
    throw new Error(`expected exactly one answer to ${op} #${id}, got ${answers.length}`);
  }
  return answers[0];
}

/** `ask`, taking the result and throwing the failure. */
export async function result(app, op, args, options) {
  const answer = await ask(app, op, args, options);
  if (!answer.ok) {
    throw Object.assign(new Error(answer.error), { code: answer.code });
  }
  return answer.result;
}
