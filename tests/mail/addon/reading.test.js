// list, get, keys and folder kinds: what the service sees of a mailbox.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { World, start, ask, result } from "./fake.js";

const sha1 = text => createHash("sha1").update(text).digest("hex");

async function mailbox(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  const app = await start({ world });
  return { world, app, ...boxes };
}

const keys = list => list.messages.map(m => m.key);

test("a key is the Message-ID without angle brackets, else fp: and a SHA-1 of author, date and subject", async () => {
  const { world, app, inbox } = await mailbox();
  const date = new Date(Date.UTC(2026, 8, 30, 10, 0, 0));
  world.message(inbox, { headerMessageId: "plain-id@example.org", date });
  const bare = world.message(inbox, {
    headerMessageId: "md5:AbCdEf==",
    author: "Bob <bob@example.org>",
    subject: "No id here",
    date: new Date(date.getTime() - 1000),
  });
  world.message(inbox, { headerMessageId: "x".repeat(600), subject: "Long id", author: "Eve <eve@example.org>", date: new Date(date.getTime() - 2000) });
  const out = await result(app, "list", { account: "account1", folder: "inbox", limit: 10 });
  assert.equal(out.messages[0].key, "plain-id@example.org");
  assert.equal(out.messages[0].message_id, "plain-id@example.org");
  const seconds = Math.floor(bare.date.getTime() / 1000);
  assert.equal(out.messages[1].key, "fp:" + sha1(`Bob <bob@example.org>|${seconds}|No id here`));
  assert.equal(out.messages[1].message_id, "");
  assert.match(out.messages[2].key, /^fp:[0-9a-f]{40}$/);
  for (const m of out.messages) {
    assert.ok(m.key.length <= 512);
  }
});

test("a key that is the same on the next start: it does not depend on Thunderbird's ids", async () => {
  const one = await mailbox();
  one.world.message(one.inbox, { headerMessageId: "stable@example.org" });
  const two = await mailbox();
  two.world.nextMessage = 5000;
  two.world.message(two.inbox, { headerMessageId: "stable@example.org" });
  const a = await result(one.app, "list", { account: "account1", folder: "inbox", limit: 5 });
  const b = await result(two.app, "list", { account: "account1", folder: "inbox", limit: 5 });
  assert.deepEqual(keys(a), keys(b));
});

test("folders are told apart by what Thunderbird says they are for, never by their names", async () => {
  const world = new World();
  const account = world.account("account1");
  const inbox = world.folder(account, "Posteingang", { specialUse: ["inbox"] });
  const junk = world.folder(account, "Archive", { specialUse: ["junk"] });      // a name that lies
  const trash = world.folder(account, "Papierkorb", { specialUse: ["trash"] });
  const archives = world.folder(account, "Archives", { specialUse: ["archives"] });
  const year = world.folder(account, "2026", { parent: archives });             // Thunderbird files by year
  const labels = world.folder(account, "Projects");                             // no use of its own
  const sent = world.folder(account, "Gesendet", { specialUse: ["sent"] });
  const drafts = world.folder(account, "Entwürfe", { specialUse: ["drafts"] });
  for (const [folder, id] of [[inbox, "i"], [junk, "j"], [trash, "t"], [archives, "a"], [year, "y"], [labels, "l"], [sent, "s"], [drafts, "d"]]) {
    world.message(folder, { headerMessageId: `${id}@example.org`, date: new Date(Date.UTC(2026, 0, 1, 0, 0, id.charCodeAt(0))) });
  }
  const app = await start({ world });
  const kinds = {};
  for (const kind of ["inbox", "sent", "drafts", "archive", "trash", "other"]) {
    for (const m of (await result(app, "list", { account: "account1", folder: kind, limit: 20 })).messages) {
      kinds[m.key.split("@")[0]] = m.folder;
      assert.equal(m.folder, kind);
    }
  }
  // Junk is not listed under "other" (it is not what anyone goes looking through), but it is "other" when it is read.
  assert.deepEqual(kinds, { i: "inbox", t: "trash", a: "archive", y: "archive", l: "other", s: "sent", d: "drafts" });
  const spam = await result(app, "get", { account: "account1", key: "j@example.org" });
  assert.equal(spam.message.folder, "other");
  const rows = await result(app, "accounts");
  assert.deepEqual(rows[0].folders, { inbox: true, sent: true, drafts: true, archive: true, trash: true });
});

test("listing the archive takes in the folder Thunderbird made for the year, newest first", async () => {
  const world = new World();
  const account = world.account("account1");
  world.folder(account, "INBOX", { specialUse: ["inbox"] });
  const archives = world.folder(account, "Archives", { specialUse: ["archives"] });
  const y2025 = world.folder(account, "2025", { parent: archives });
  const y2026 = world.folder(account, "2026", { parent: archives });
  world.message(y2025, { headerMessageId: "old@example.org", date: new Date(Date.UTC(2025, 5, 1)) });
  world.message(y2026, { headerMessageId: "new@example.org", date: new Date(Date.UTC(2026, 5, 1)) });
  world.message(archives, { headerMessageId: "mid@example.org", date: new Date(Date.UTC(2025, 11, 1)) });
  const app = await start({ world });
  const out = await result(app, "list", { account: "account1", folder: "archive", limit: 10 });
  assert.deepEqual(keys(out), ["new@example.org", "mid@example.org", "old@example.org"]);
  assert.equal(out.more, false);
  assert.equal(world.lists.size, 0);
});

test("130 messages come in pages, newest first, with before inclusive, and every message once", async () => {
  const { world, app, inbox } = await mailbox();
  for (let i = 0; i < 130; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(Date.UTC(2026, 8, 1, 0, 0, 0) + i * 60_000) });
  }
  const seen = [];
  let before;
  for (let page = 0; page < 10; page++) {
    const args = { account: "account1", folder: "inbox", limit: 50 };
    if (before !== undefined) {
      args.before = before;
    }
    const out = await result(app, "list", args);
    const times = out.messages.map(m => m.ts);
    assert.deepEqual(times, [...times].sort((a, b) => b - a));
    if (before !== undefined) {
      assert.equal(out.messages[0].ts, before, "before is inclusive: the message at that second is shown again");
    }
    for (const m of out.messages) {
      if (!seen.includes(m.key)) {
        seen.push(m.key);
      }
    }
    if (!out.more) {
      break;
    }
    before = out.messages.at(-1).ts;
  }
  assert.equal(seen.length, 130);
  assert.equal(seen[0], "m129@example.org");
  assert.equal(seen.at(-1), "m0@example.org");
  assert.equal(world.lists.size, 0, "no list is left open for Thunderbird to go on preparing");
});

test("a page that is full says there is more only when there is, and a list is not read past the page", async () => {
  const { world, app, inbox } = await mailbox({ pageSize: 10 });
  for (let i = 0; i < 25; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(Date.UTC(2026, 8, 1) + i * 1000) });
  }
  const first = await result(app, "list", { account: "account1", folder: "inbox", limit: 5 });
  assert.equal(first.messages.length, 5);
  assert.equal(first.more, true);
  const continues = world.calls.filter(c => c[0] === "continueList").length;
  assert.equal(continues, 0, "five messages came from the first page of ten");
  const exact = await result(app, "list", { account: "account1", folder: "inbox", limit: 25 });
  assert.equal(exact.messages.length, 25);
  assert.equal(exact.more, false);
  assert.equal(world.lists.size, 0);
});

test("a folder with a hundred thousand messages costs a page, not the folder", async () => {
  const { world, app, inbox } = await mailbox({ pageSize: 100 });
  for (let i = 0; i < 3000; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(Date.UTC(2026, 0, 1) + i * 1000) });
  }
  const out = await result(app, "list", { account: "account1", folder: "inbox", limit: 20 });
  assert.equal(out.messages.length, 20);
  assert.equal(world.calls.filter(c => c[0] === "continueList").length, 0);
  assert.equal(world.lists.size, 0);
});

test("unread asks for the unread ones only", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "a@example.org", read: true });
  world.message(inbox, { headerMessageId: "b@example.org", read: false });
  const out = await result(app, "list", { account: "account1", folder: "inbox", unread: true, limit: 10 });
  assert.deepEqual(keys(out), ["b@example.org"]);
});

test("a bad list request is refused, an account Thunderbird does not have is not found", async () => {
  const { app } = await mailbox();
  assert.equal((await ask(app, "list", { account: "account1", folder: "everything" })).code, "bad_request");
  assert.equal((await ask(app, "list", { account: 7, folder: "inbox" })).code, "bad_request");
  assert.equal((await ask(app, "list", { account: "account1", folder: "inbox", limit: "ten" })).code, "bad_request");
  assert.equal((await ask(app, "list", { account: "nobody", folder: "inbox" })).code, "not_found");
  const none = await result(app, "list", { account: "account1", folder: "other" });
  assert.deepEqual(none, { messages: [], more: false });
});

test("the message as the service sees it: addresses, time, flags, and no attachment flag without one", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, {
    headerMessageId: "full@example.org",
    author: '"Müller, Jürgen" <JUERGEN@example.de>',
    recipients: ["Lab Tester <test@example.test>", "second@example.test"],
    ccList: ["Cc Person <cc@example.test>"],
    subject: "Grüße ☃",
    date: new Date(Date.UTC(2026, 8, 30, 8, 30, 15)),
    flagged: true,
    read: false,
  });
  const [m] = (await result(app, "list", { account: "account1", folder: "inbox" })).messages;
  assert.deepEqual(m, {
    key: "full@example.org",
    account: "account1",
    folder: "inbox",
    from: { name: "Müller, Jürgen", email: "juergen@example.de" },
    to: [{ name: "Lab Tester", email: "test@example.test" }, { name: "", email: "second@example.test" }],
    cc: [{ name: "Cc Person", email: "cc@example.test" }],
    subject: "Grüße ☃",
    ts: Date.UTC(2026, 8, 30, 8, 30, 15) / 1000,
    unread: true,
    flagged: true,
    attachments: false,
    thread: null,
    message_id: "full@example.org",
  });
});

test("whether a listed mail has attachments is asked of Thunderbird, and inline images do not count", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, {
    headerMessageId: "with@example.org",
    date: new Date(Date.UTC(2026, 8, 3)),
    attachments: [{ name: "a.pdf", contentType: "application/pdf", content: new Uint8Array([1, 2, 3]), partName: "1.2" }],
    plain: "x",
  });
  world.message(inbox, {
    headerMessageId: "inline@example.org",
    date: new Date(Date.UTC(2026, 8, 2)),
    attachments: [{ name: "logo.png", contentType: "image/png", content: new Uint8Array([1]), partName: "1.2", inline: true, contentId: "logo@x" }],
    plain: "x",
  });
  world.message(inbox, { headerMessageId: "none@example.org", date: new Date(Date.UTC(2026, 8, 1)), plain: "x" });
  const out = await result(app, "list", { account: "account1", folder: "inbox" });
  assert.deepEqual(out.messages.map(m => [m.key, m.attachments]), [
    ["with@example.org", true],
    ["inline@example.org", false],
    ["none@example.org", false],
  ]);
  const before = world.calls.filter(c => c[0] === "listAttachments").length;
  await result(app, "list", { account: "account1", folder: "inbox" });
  assert.equal(world.calls.filter(c => c[0] === "listAttachments").length, before, "what was asked once is remembered");
});

test("a message Thunderbird cannot be asked about is listed without attachments, not left out", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "odd@example.org", failAttachments: true });
  const out = await result(app, "list", { account: "account1", folder: "inbox" });
  assert.equal(out.messages.length, 1);
  assert.equal(out.messages[0].attachments, false);
});

// -- get --

test("get: plain text, and the headers the service wants as Thunderbird has them", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, {
    headerMessageId: "plain@example.org",
    plain: "Hello there.\n",
    headers: { "in-reply-to": ["<root@example.org>"], references: ["<root@example.org> <mid@example.org>"], "reply-to": ["Reply <reply@example.org>"] },
  });
  const got = await result(app, "get", { account: "account1", key: "plain@example.org" });
  assert.equal(got.text, "Hello there.\n");
  assert.equal(got.html, null);
  assert.deepEqual(got.headers, {
    "message-id": "<plain@example.org>",
    "in-reply-to": "<root@example.org>",
    references: "<root@example.org> <mid@example.org>",
    "reply-to": "Reply <reply@example.org>",
  });
  assert.equal(got.message.thread, "root@example.org");
  assert.deepEqual(got.attachments, []);
});

test("get: a mail with only HTML gives the HTML, one with both gives the text alone", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "html@example.org", html: "<p>Only <b>html</b></p>" });
  world.message(inbox, { headerMessageId: "both@example.org", plain: "The words", html: "<p>The words</p>" });
  const html = await result(app, "get", { account: "account1", key: "html@example.org" });
  assert.equal(html.text, null);
  assert.equal(html.html, "<p>Only <b>html</b></p>");
  const both = await result(app, "get", { account: "account1", key: "both@example.org" });
  assert.equal(both.text, "The words");
  assert.equal(both.html, null);
});

test("get: unicode comes through whole", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "u@example.org", plain: "Grüße aus Zürich ☃ 日本語 🙂\n", subject: "Grüße" });
  const got = await result(app, "get", { account: "account1", key: "u@example.org" });
  assert.equal(got.text, "Grüße aus Zürich ☃ 日本語 🙂\n");
  assert.equal(got.message.subject, "Grüße");
});

test("get leaves the mail unread, and does nothing to it", async () => {
  const { world, app, inbox } = await mailbox();
  const header = world.message(inbox, { headerMessageId: "u@example.org", plain: "x", read: false });
  await result(app, "get", { account: "account1", key: "u@example.org" });
  assert.equal(world.header(header.id).read, false);
  assert.deepEqual(world.calls.filter(c => c[0] === "update" || c[0] === "move"), []);
});

test("get: attachments are listed with their part, and attached files are not the words", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, {
    headerMessageId: "att@example.org",
    plain: "See the file.",
    attachments: [
      { name: "invoice.pdf", contentType: "application/pdf", content: new Uint8Array(325), partName: "1.2" },
      { name: "logo.png", contentType: "image/png", content: new Uint8Array(9), partName: "1.3", inline: true, contentId: "logo@x" },
    ],
  });
  const got = await result(app, "get", { account: "account1", key: "att@example.org" });
  assert.equal(got.text, "See the file.");
  assert.deepEqual(got.attachments, [
    { part: "1.2", name: "invoice.pdf", content_type: "application/pdf", size: 325, inline: false },
    { part: "1.3", name: "logo.png", content_type: "image/png", size: 9, inline: true },
  ]);
  assert.equal(got.message.attachments, true);
});

test("get: a mail's words are cut at a million characters", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "big@example.org", plain: "x".repeat(1_200_000) });
  const got = await result(app, "get", { account: "account1", key: "big@example.org" });
  assert.equal(got.text.length, 1_000_000);
});

test("get: headers are read from the raw mail when Thunderbird gives none", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, {
    headerMessageId: "raw@example.org",
    plain: "x",
    noHeaders: true,
    raw: "Message-ID: <raw@example.org>\r\nIn-Reply-To: <before@example.org>\r\nReferences: <first@example.org>\r\n <before@example.org>\r\nReply-To: r@example.org\r\n\r\nbody\r\n",
  });
  const got = await result(app, "get", { account: "account1", key: "raw@example.org" });
  assert.equal(got.headers["in-reply-to"], "<before@example.org>");
  assert.equal(got.headers.references, "<first@example.org> <before@example.org>");
  assert.equal(got.headers["reply-to"], "r@example.org");
  assert.equal(got.message.thread, "first@example.org");
});

test("get: a mail that moved since it was listed is found again by its Message-ID", async () => {
  const { world, app, inbox, archive } = await mailbox();
  const header = world.message(inbox, { headerMessageId: "moved@example.org", plain: "still here" });
  await result(app, "list", { account: "account1", folder: "inbox" });
  world.relocate(header.id, archive);
  const got = await result(app, "get", { account: "account1", key: "moved@example.org" });
  assert.equal(got.text, "still here");
  assert.equal(got.message.folder, "archive");
});

test("get: a mail with no Message-ID is found by its fp: key", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "md5:zzzz", subject: "Nameless", author: "A <a@example.org>", plain: "found you" });
  const [m] = (await result(app, "list", { account: "account1", folder: "inbox" })).messages;
  assert.match(m.key, /^fp:/);
  const fresh = await start({ world });   // a restart: no memory of ids
  const got = await result(fresh, "get", { account: "account1", key: m.key });
  assert.equal(got.text, "found you");
});

test("get: no such mail is not_found, and a key that is not one is a bad request", async () => {
  const { app } = await mailbox();
  assert.equal((await ask(app, "get", { account: "account1", key: "nope@example.org" })).code, "not_found");
  assert.equal((await ask(app, "get", { account: "account1", key: "fp:" + "0".repeat(40) })).code, "not_found");
  assert.equal((await ask(app, "get", { account: "account1", key: "k".repeat(520) })).code, "bad_request");
  assert.equal((await ask(app, "get", { account: "account1" })).code, "bad_request");
  assert.equal((await ask(app, "get", { key: "a@b" })).code, "bad_request");
});

test("get: a message Thunderbird cannot read is an engine_error with a sentence, not a stack trace", async () => {
  const { world, app, inbox } = await mailbox();
  world.message(inbox, { headerMessageId: "bad@example.org", failRead: true });
  const answer = await ask(app, "get", { account: "account1", key: "bad@example.org" });
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /^Thunderbird could not/);
  assert.doesNotMatch(answer.error, /\n|at .*\.js/);
});

test("the same Message-ID in two folders is one key, and the inbox's copy is the one read", async () => {
  const { world, app, inbox, archive } = await mailbox();
  world.message(inbox, { headerMessageId: "twin@example.org", plain: "in the inbox" });
  world.message(archive, { headerMessageId: "twin@example.org", plain: "in the archive" });
  const got = await result(app, "get", { account: "account1", key: "twin@example.org" });
  assert.equal(got.message.folder, "inbox");
});

test("accounts are the mail accounts, with their senders, folders and unread counts", async () => {
  const world = new World();
  const boxes = world.standard("account1", {
    name: "Lab",
    identities: [
      { id: "id1", email: "Test@Example.test", name: "Lab Tester" },
      { id: "id2", email: "alias@example.test", name: "" },
    ],
  });
  world.account("local", { name: "Local Folders", type: "none" });
  for (let i = 0; i < 3; i++) {
    world.message(boxes.inbox, { read: i === 0 });
  }
  const app = await start({ world });
  const rows = await result(app, "accounts");
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].identities, [
    { id: "id1", email: "test@example.test", name: "Lab Tester" },
    { id: "id2", email: "alias@example.test", name: "" },
  ]);
  assert.deepEqual(rows[0].emails, ["test@example.test", "alias@example.test"]);
  assert.equal(rows[0].engine_id, "account1");
  assert.equal(rows[0].type, "imap");
  assert.equal(rows[0].unread, 2);
  assert.equal(rows[0].name, "Lab");
});
