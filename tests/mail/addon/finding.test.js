// find and known: searching without reading the mailbox, and who the person has dealt with.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, ask, result } from "./fake.js";
import { windows } from "../../../share/mail/extension/finding.js";

const DAY = 86_400_000;

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  const app = await start({ world });
  const at = days => new Date(world.clock.now() - days * DAY);
  return { world, app, at, ...boxes };
}

const keys = out => out.messages.map(m => m.key);

test("the windows go back in time, each ending where the next begins, and cover everything", () => {
  const now = Date.UTC(2026, 9, 1);
  const all = windows(undefined, undefined, now);
  assert.equal(all[0].to, Infinity);
  assert.equal(all.at(-1).from, -Infinity);
  for (let i = 1; i < all.length; i++) {
    assert.equal(all[i].to, all[i - 1].from - 1, "no gap and no overlap");
  }
  const bounded = windows(now - 10 * DAY, now - DAY, now);
  assert.equal(bounded[0].to, now - DAY);
  assert.equal(bounded.at(-1).from, now - 10 * DAY);
  assert.ok(bounded.every(w => w.from <= w.to));
  assert.deepEqual(windows(now + 5, now + 1, now), []);
});

test("find matches text in the subject without regard to case, which Thunderbird's own query does not", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org", subject: "Invoice 4711", date: at(2) });
  world.message(inbox, { headerMessageId: "b@example.org", subject: "Lunch?", date: at(1) });
  const out = await result(app, "find", { text: "invoice", limit: 10 });
  assert.deepEqual(keys(out), ["a@example.org"]);
  const upper = await result(app, "find", { text: "LUNCH", limit: 10 });
  assert.deepEqual(keys(upper), ["b@example.org"]);
});

test("find looks in the people too: sender and recipients, by name or address", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org", author: "Carol Accounts <carol@example.com>", subject: "x", date: at(2) });
  world.message(inbox, { headerMessageId: "b@example.org", recipients: ["Zed Zebra <zed@example.net>"], subject: "y", date: at(1) });
  assert.deepEqual(keys(await result(app, "find", { text: "carol" })), ["a@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "zebra" })), ["b@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "example.net" })), ["b@example.org"]);
});

test("find's from is a whole address (Thunderbird filters on it) or part of a name (matched here)", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org", author: "Carol Accounts <carol@example.com>", date: at(2) });
  world.message(inbox, { headerMessageId: "b@example.org", author: "Dave <dave@example.org>", date: at(1) });
  assert.deepEqual(keys(await result(app, "find", { from: "carol@example.com" })), ["a@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { from: "DAV" })), ["b@example.org"]);
  const asked = world.calls.filter(c => c[0] === "query").map(c => c[1]);
  assert.ok(asked.some(q => q.author === "carol@example.com"), "a whole address goes to Thunderbird as the author");
  assert.ok(asked.every(q => q.author === undefined || q.author.includes("@")), "a part of a name does not");
});

test("find's subject and to are narrower than text", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org", subject: "Quarterly report", recipients: ["Team <team@example.test>"], date: at(3) });
  world.message(inbox, { headerMessageId: "b@example.org", subject: "Hello", author: "Quarterly Person <q@example.org>", date: at(2) });
  assert.deepEqual(keys(await result(app, "find", { subject: "quarterly" })), ["a@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { to: "team" })), ["a@example.org"]);
});

test("find: unread, flagged and attachments are left to Thunderbird", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "r@example.org", read: true, date: at(4) });
  world.message(inbox, { headerMessageId: "u@example.org", read: false, date: at(3) });
  world.message(inbox, { headerMessageId: "f@example.org", flagged: true, read: true, date: at(2) });
  world.message(inbox, {
    headerMessageId: "a@example.org",
    read: true,
    date: at(1),
    plain: "x",
    attachments: [{ name: "a.txt", contentType: "text/plain", content: new Uint8Array(3), partName: "1.2" }],
  });
  assert.deepEqual(keys(await result(app, "find", { unread: true })), ["u@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { flagged: true })), ["f@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { attachments: true })), ["a@example.org"]);
});

test("find with since and until sends Thunderbird dates, not numbers (a number never answers), inclusive at both ends", async () => {
  const { world, app, inbox } = await lab();
  const base = world.clock.now() - 30 * DAY;
  for (const [i, id] of ["a", "b", "c", "d"].entries()) {
    world.message(inbox, { headerMessageId: `${id}@example.org`, date: new Date(base + i * DAY) });
  }
  const out = await result(app, "find", { since: (base + DAY) / 1000, until: (base + 2 * DAY) / 1000 });
  assert.deepEqual(keys(out).sort(), ["b@example.org", "c@example.org"]);
  for (const [, query] of world.calls.filter(c => c[0] === "query")) {
    for (const key of ["fromDate", "toDate"]) {
      assert.ok(!(key in query) || query[key] instanceof Date);
    }
  }
});

test("find is newest first, and stops asking older windows once it has enough", async () => {
  const { world, app, at, inbox } = await lab();
  for (let i = 0; i < 6; i++) {
    world.message(inbox, { headerMessageId: `new${i}@example.org`, subject: "topic", date: new Date(at(0).getTime() - i * 3_600_000) });
  }
  for (let i = 0; i < 6; i++) {
    world.message(inbox, { headerMessageId: `old${i}@example.org`, subject: "topic", date: at(400 + i) });
  }
  const queries = () => world.calls.filter(c => c[0] === "query").length;
  const out = await result(app, "find", { text: "topic", limit: 4 });
  assert.deepEqual(keys(out), ["new0@example.org", "new1@example.org", "new2@example.org", "new3@example.org"]);
  assert.equal(queries(), 1, "the last day had enough, so no older window was asked");
  const more = await result(app, "find", { text: "topic", limit: 8 });
  assert.equal(more.messages.length, 8);
  assert.deepEqual(keys(more).slice(6), ["old0@example.org", "old1@example.org"]);
  assert.ok(more.messages.every((m, i, all) => i === 0 || all[i - 1].ts >= m.ts));
});

test("find keeps no more than it was asked for however many match", async () => {
  const { world, app, at, inbox } = await lab({ pageSize: 50 });
  for (let i = 0; i < 2000; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, subject: "match", date: new Date(at(1).getTime() - i * 1000) });
  }
  const out = await result(app, "find", { text: "match", limit: 7 });
  assert.deepEqual(keys(out), Array.from({ length: 7 }, (_, i) => `m${i}@example.org`));
  assert.equal(world.lists.size, 0);
});

test("find leaves out trash, junk and drafts unless it is asked to look there", async () => {
  const { world, app, at, inbox, trash, junk, drafts, archive, sent } = await lab();
  for (const [folder, id] of [[inbox, "i"], [trash, "t"], [junk, "j"], [drafts, "d"], [archive, "a"], [sent, "s"]]) {
    world.message(folder, { headerMessageId: `${id}@example.org`, subject: "everywhere", date: at(1 + id.charCodeAt(0) / 1000) });
  }
  const normal = await result(app, "find", { text: "everywhere" });
  assert.deepEqual(keys(normal).map(k => k[0]).sort(), ["a", "i", "s"]);
  assert.deepEqual(keys(await result(app, "find", { text: "everywhere", folders: ["trash"] })), ["t@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "everywhere", folders: ["junk"] })), ["j@example.org"]);
  assert.equal((await ask(app, "find", { text: "x", folders: ["elsewhere"] })).code, "bad_request");
});

test("find can be limited to some accounts", async () => {
  const world = new World();
  const one = world.standard("account1");
  const two = world.standard("account2");
  world.message(one.inbox, { headerMessageId: "one@example.org", subject: "same", date: new Date(world.clock.now() - 1000) });
  world.message(two.inbox, { headerMessageId: "two@example.org", subject: "same", date: new Date(world.clock.now() - 2000) });
  const app = await start({ world });
  assert.deepEqual(keys(await result(app, "find", { text: "same" })), ["one@example.org", "two@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "same", accounts: ["account2"] })), ["two@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "same", accounts: ["nobody"] })), []);
});

test("find reads the body of the newest mails that did not match in their headers, and no more than 300", async () => {
  const { world, app, at, inbox } = await lab();
  world.message(inbox, { headerMessageId: "deep@example.org", subject: "Nothing here", plain: "The Secret phrase is inside.", date: at(5) });
  world.message(inbox, { headerMessageId: "html@example.org", subject: "Newsletter", html: "<style>.x{}</style><p>Fancy <b>Marker</b> text</p>", date: at(4) });
  assert.deepEqual(keys(await result(app, "find", { text: "secret phrase" })), ["deep@example.org"]);
  assert.deepEqual(keys(await result(app, "find", { text: "fancy marker text" })), ["html@example.org"], "tags are not text");
  assert.deepEqual(keys(await result(app, "find", { text: "x{}" })), [], "nor is a style sheet");

  const big = await lab();
  for (let i = 0; i < 400; i++) {
    big.world.message(big.inbox, { headerMessageId: `n${i}@example.org`, subject: "dull", plain: "dull", date: new Date(big.at(1).getTime() - i * 1000) });
  }
  const before = big.world.calls.filter(c => c[0] === "getFull").length;
  await result(big.app, "find", { text: "needle" });
  const read = big.world.calls.filter(c => c[0] === "getFull").length - before;
  assert.ok(read <= 300, `${read} bodies were read`);
});

test("find that runs out of time says what it has and that it is partial", async () => {
  const { world, app, at, inbox } = await lab({ pageSize: 10 });
  for (let i = 0; i < 100; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, subject: i % 2 ? "match" : "other", date: new Date(at(1).getTime() - i * 1000) });
  }
  world.quirks.pageCost = 2500;   // every page of Thunderbird's answer takes two and a half seconds
  const out = await result(app, "find", { text: "match", limit: 50 });
  assert.equal(out.partial, true);
  assert.ok(out.messages.length > 0 && out.messages.length < 50);
  assert.equal(world.lists.size, 0, "the query is given up on, not left to go on");
});

test("a bad find is refused", async () => {
  const { app } = await lab();
  for (const args of [{ text: 7 }, { since: "yesterday" }, { unread: "yes" }, { accounts: "account1" }, { limit: "all" }]) {
    assert.equal((await ask(app, "find", args)).code, "bad_request", JSON.stringify(args));
  }
});

// -- known --

test("known: an address in an address book is known, one that is not is not", async () => {
  const { world, app } = await lab();
  world.contacts.push({ id: "c1", properties: { PrimaryEmail: "Friend@Example.org", DisplayName: "A friend" } });
  const out = await result(app, "known", { emails: ["friend@example.org", "stranger@example.net"] });
  assert.deepEqual(out, { "friend@example.org": true, "stranger@example.net": false });
  assert.ok(world.calls.filter(c => c[0] === "quickSearch").every(c => c[1].includeRemote === false), "a remote address book is never asked");
});

test("known: someone the person has written to, from the Sent folders", async () => {
  const { world, app, at, sent } = await lab();
  world.message(sent, { headerMessageId: "s1@example.org", recipients: ["Zed Zebra <zed@example.net>"], ccList: ["Cc <cc@example.net>"], bccList: ["bcc@example.net"], date: at(3) });
  const out = await result(app, "known", { emails: ["zed@example.net", "CC@example.net", "bcc@example.net", "who@example.net"] });
  assert.deepEqual(out, { "zed@example.net": true, "CC@example.net": true, "bcc@example.net": true, "who@example.net": false });
});

test("known reads no more of the Sent folders than it has to", async () => {
  const { world, app, at, sent } = await lab({ pageSize: 50 });
  for (let i = 0; i < 4000; i++) {
    world.message(sent, { headerMessageId: `s${i}@example.org`, recipients: [`r${i}@example.net`], date: new Date(at(1).getTime() - i * 1000) });
  }
  const out = await result(app, "known", { emails: ["r0@example.net", "r1@example.net"] });
  assert.deepEqual(out, { "r0@example.net": true, "r1@example.net": true });
  assert.ok(world.calls.filter(c => c[0] === "continueList").length < 3);
  assert.equal(world.lists.size, 0);
});

test("known refuses what is not a list of addresses", async () => {
  const { app } = await lab();
  assert.equal((await ask(app, "known", { emails: "a@b.example" })).code, "bad_request");
  assert.equal((await ask(app, "known", {})).code, "bad_request");
  assert.equal((await ask(app, "known", { emails: [7] })).code, "bad_request");
  assert.deepEqual(await result(app, "known", { emails: [] }), {});
});
