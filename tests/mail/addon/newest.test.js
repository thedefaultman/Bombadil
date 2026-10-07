// newest.js: the newest messages that match something, by window of time, without walking a mailbox.

import test from "node:test";
import assert from "node:assert/strict";
import { World, settle } from "./fake.js";
import { Newest, scan, sameMessage, windowQuery, windows } from "../../../share/mail/extension/newest.js";

const DAY = 86_400_000;
const header = (id, ms, extra = {}) => ({
  id,
  headerMessageId: `m${id}@example.org`,
  author: "A <a@example.org>",
  subject: `s${id}`,
  date: new Date(ms),
  folder: { id: "f1", accountId: "acc" },
  ...extra,
});

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

test("the windows are counted back from where the search ends, so that one that starts long ago has a small first window", () => {
  const now = Date.UTC(2026, 9, 1);
  const until = now - 800 * DAY;
  const old = windows(undefined, until, now);
  assert.equal(old[0].to, until);
  assert.equal(old[0].to - old[0].from, DAY, "a day wide, not three years");
  assert.equal(old[1].to - old[1].from, 6 * DAY - 1, "then a week, back from the day");
  const future = windows(undefined, now + 100 * DAY, now);
  assert.equal(future[0].to, now + 100 * DAY);
  assert.equal(future[0].from, now - DAY, "an end in the future counts from now");
});

test("Newest keeps the newest of what it is given in any order, and no more than it was asked for", () => {
  const newest = new Newest(3);
  [5, 1, 9, 3, 7, 8, 2].forEach(n => newest.add(header(n, n * 1000)));
  assert.deepEqual(newest.items.map(h => h.id), [9, 8, 7]);
  newest.add(header(10, 500));
  assert.deepEqual(newest.items.map(h => h.id), [9, 8, 7], "an older one is not kept");
  const tie = new Newest(2);
  [1, 2, 3].forEach(n => tie.add(header(n, 5000)));
  assert.deepEqual(tie.items.map(h => h.id), [1, 2], "a tie keeps what came first");
  assert.deepEqual(new Newest(0).items, []);
});

test("with distinct a message in several folders is kept once, as the copy that ranks first", () => {
  const inbox = new Set(["f-inbox"]);
  const newest = new Newest(3, { distinct: true, rank: h => (inbox.has(h.folder.id) ? 0 : 1) });
  const copy = (id, folder, ms) => header(id, ms, { headerMessageId: "same@example.org", folder: { id: folder, accountId: "acc" } });
  newest.add(copy(1, "f-archive", 9000));
  newest.add(copy(2, "f-sent", 9000));
  newest.add(copy(3, "f-inbox", 9000));
  newest.add(copy(4, "f-label", 9000));
  assert.deepEqual(newest.items.map(h => h.id), [3], "one message, and it is the inbox's");
  newest.add(header(5, 1000));
  newest.add(header(6, 2000));
  newest.add(header(7, 3000));
  assert.deepEqual(newest.items.map(h => h.id), [3, 7, 6], "the copies did not use up the room");
});

test("the same Message-ID in another account is another message, and so are two with none that differ", () => {
  const a = header(1, 1000);
  const b = header(2, 1000, { folder: { id: "f2", accountId: "other" }, headerMessageId: a.headerMessageId });
  assert.notEqual(sameMessage(a), sameMessage(b));
  const none = (author, ms) => header(3, ms, { headerMessageId: "md5:xyz", author, subject: "x" });
  assert.equal(sameMessage(none("A", 5000)), sameMessage(none("A", 5000)));
  assert.notEqual(sameMessage(none("A", 5000)), sameMessage(none("B", 5000)));
});

async function world(count, { pageSize = 10 } = {}) {
  const w = new World({ pageSize });
  const { inbox } = w.standard("acc");
  for (let i = 0; i < count; i++) {
    w.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(w.clock.now() - i * 60_000) });
  }
  return { w, inbox, env: { messenger: w.messenger, clock: w.clock } };
}

test("scan reads every page of a query, and the list is gone when it has", async () => {
  const { w, inbox, env } = await world(95);
  const newest = new Newest(200);
  const whole = await scan(env, windowQuery({ from: -Infinity, to: Infinity }, [inbox.id]), newest, { end: Infinity });
  assert.equal(whole, true);
  assert.equal(newest.items.length, 95);
  assert.equal(w.lists.size, 0);
});

test("scan gives the query's own dates and filters, as Dates and never as numbers", () => {
  const q = windowQuery({ from: 1000, to: 5000 }, ["f"], { unread: true });
  assert.deepEqual(Object.keys(q).sort(), ["folderId", "fromDate", "toDate", "unread"]);
  assert.ok(q.fromDate instanceof Date && q.toDate instanceof Date);
  assert.deepEqual(Object.keys(windowQuery({ from: -Infinity, to: Infinity }, ["f"])), ["folderId"]);
});

test("scan stops when time is up, says it did not read to the end, and lets go of the list", async () => {
  const { w, inbox, env } = await world(95);
  w.quirks.pageCost = 1000;
  const newest = new Newest(200);
  const whole = await scan(env, windowQuery({ from: -Infinity, to: Infinity }, [inbox.id]), newest, { end: w.clock.now() + 2500 });
  assert.equal(whole, false);
  assert.ok(newest.items.length > 0 && newest.items.length < 95);
  await settle();
  assert.equal(w.lists.size, 0);
});

test("scan stops when the request it is for was answered, and lets go of the list", async () => {
  const { w, inbox, env } = await world(95);
  let answered = false;
  const real = w.messenger.messages.continueList;
  w.messenger.messages.continueList = async id => {
    const page = await real(id);
    answered = true;   // the add-on answered the request while Thunderbird was making this page
    return page;
  };
  const newest = new Newest(200);
  const whole = await scan(env, windowQuery({ from: -Infinity, to: Infinity }, [inbox.id]), newest, { end: Infinity, cancelled: () => answered });
  assert.equal(whole, false);
  assert.equal(newest.items.length, 20, "two pages, and not the rest");
  await settle();
  assert.equal(w.lists.size, 0);
});

test("scan uses only what accept lets through", async () => {
  const { inbox, env } = await world(30);
  const newest = new Newest(200);
  await scan(env, windowQuery({ from: -Infinity, to: Infinity }, [inbox.id]), newest, { end: Infinity, accept: h => h.id % 2 === 0 });
  assert.equal(newest.items.length, 15);
});

test("a query that fails is the caller's failure, and nothing is left open", async () => {
  const { w, env } = await world(5);
  await assert.rejects(scan(env, { bogus: 1 }, new Newest(5), { end: Infinity }), /Unexpected property/);
  assert.equal(w.lists.size, 0);
});
