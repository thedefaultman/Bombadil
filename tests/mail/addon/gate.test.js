// gate.js: what runs when, and what a place is held by: the request, never a call that may not come back.

import test from "node:test";
import assert from "node:assert/strict";
import { FakeClock, World, ask, result, settle, start } from "./fake.js";
import { Gate } from "../../../share/mail/extension/gate.js";

const never = () => new Promise(() => {});
const deferred = () => {
  let resolve;
  const promise = new Promise(r => (resolve = r));
  return { promise, resolve };
};
const gate = (options = {}) => new Gate({ clock: new FakeClock(), ...options });
const outcome = promise => promise.then(value => ({ value }), error => ({ error }));

test("a call that never answers frees its place when the request that made it is answered", async () => {
  const g = gate({ slots: 1 });
  const first = new AbortController();
  const stuck = outcome(g.run([], never, first.signal));
  await settle();
  const second = outcome(g.run([], async () => "second"));
  await settle();
  assert.equal(g.waiters.length, 1, "the second is waiting for the one place");
  first.abort();
  assert.match((await stuck).error.message, /answered already/);
  assert.equal((await second).value, "second", "and has it now");
  assert.equal(g.free, 1);
});

test("a request that is answered while it waits for a place does not begin when one comes free", async () => {
  const g = gate({ slots: 1, slotWaitMs: 60_000 });
  const hold = deferred();
  const holder = g.run([], () => hold.promise);
  await settle();
  let began = false;
  const over = new AbortController();
  const waiting = outcome(g.run([], async () => (began = true), over.signal));
  await settle();
  over.abort();
  assert.match((await waiting).error.message, /answered already/);
  assert.equal(g.waiters.length, 0, "it is no longer in the line");
  hold.resolve("done");
  await holder;
  await settle();
  assert.equal(began, false, "a mark or a move that was reported as failed does not happen after all");
  assert.equal(g.free, 1);
});

test("a request answered before it has a place never begins at all", async () => {
  const g = gate();
  const over = new AbortController();
  over.abort();
  let began = false;
  const out = await outcome(g.run([], async () => (began = true), over.signal));
  assert.match(out.error.message, /answered already/);
  assert.equal(began, false);
  assert.equal(g.free, 8);
});

test("a request answered while it waits for a send leaves that wait, and does not begin", async () => {
  const g = gate();
  const sending = deferred();
  const lane = g.send("a", () => sending.promise);
  await settle();
  const over = new AbortController();
  let began = false;
  const waiting = outcome(g.run(["a"], async () => (began = true), over.signal));
  await settle();
  over.abort();
  await waiting;
  assert.equal(began, false);
  assert.equal(g.clock.pending(), 0, "the three seconds' timer is not left running");
  sending.resolve("sent");
  await lane;
});

test("sends for one account go one after another, and one that is stuck does not hold up another account", async () => {
  const g = gate();
  const stuck = deferred();
  const order = [];
  const a1 = g.send("a", () => (order.push("a1"), stuck.promise));
  const a2 = g.send("a", async () => (order.push("a2"), "a2"));
  const b1 = g.send("b", async () => (order.push("b1"), "b1"));
  await settle();
  assert.equal(await b1, "b1", "b is not behind a");
  assert.deepEqual(order, ["a1", "b1"], "a2 waits for a1");
  stuck.resolve("a1");
  assert.equal(await a1, "a1");
  assert.equal(await a2, "a2");
  assert.deepEqual(order, ["a1", "b1", "a2"]);
  assert.equal(g.lanes.size, 0, "nothing is kept for an account with nothing to send");
  assert.equal(g.queued, 0);
});

test("a send whose request was answered while it waited its turn is never begun, and the ones behind it still go", async () => {
  const g = gate();
  const first = deferred();
  const over = new AbortController();
  let began = [];
  const a1 = g.send("a", () => first.promise);
  const a2 = outcome(g.send("a", async () => (began.push("a2"), "a2"), over.signal));
  const a3 = g.send("a", async () => (began.push("a3"), "a3"));
  await settle();
  over.abort();
  first.resolve("a1");
  await a1;
  assert.match((await a2).error.message, /answered already/);
  assert.equal(await a3, "a3");
  assert.deepEqual(began, ["a3"]);
  assert.equal(g.queued, 0);
});

test("a send that is stuck lets go of its lane when its request is answered, so the next goes", async () => {
  const g = gate();
  const over = new AbortController();
  const stuck = outcome(g.send("a", never, over.signal));
  const next = g.send("a", async () => "next");
  await settle();
  over.abort();
  assert.match((await stuck).error.message, /answered already/);
  assert.equal(await next, "next");
});

test("no more than eight sends wait, and the ninth is refused with a sentence", async () => {
  const g = gate();
  const hold = deferred();
  const accepted = [g.send("a", () => hold.promise)];
  await settle();
  for (let i = 0; i < 8; i++) {
    accepted.push(g.send("a", async () => i));
  }
  const refused = await outcome(g.send("a", async () => "never"));
  assert.match(refused.error.message, /too many mails waiting/);
  assert.match(refused.error.message, /Nothing was sent/);
  hold.resolve(0);
  assert.equal((await Promise.all(accepted)).length, 9);
  assert.equal(g.queued, 0);
});

test("an ordinary op for an account waits for a send for it, three seconds at most, and others do not", async () => {
  const g = gate();
  const sending = deferred();
  const lane = g.send("a", () => sending.promise);
  await settle();
  const log = [];
  const forA = g.run(["a"], async () => log.push("a"));
  const forB = g.run(["b"], async () => log.push("b"));
  const forNone = g.run([], async () => log.push("none"));
  const forAll = g.run(null, async () => log.push("all"));
  await settle();
  assert.deepEqual(log.sort(), ["b", "none"]);
  await g.clock.advance(2900);
  assert.deepEqual(log.sort(), ["b", "none"]);
  await g.clock.advance(200);
  await Promise.all([forA, forB, forNone, forAll]);
  assert.deepEqual(log.sort(), ["a", "all", "b", "none"]);
  sending.resolve("sent");
  await lane;
});

// -- through the engine --

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  if (options.second) {
    world.standard("account2");
  }
  const app = await start({ world });
  return { world, app, ...boxes };
}

const mail = extra => ({
  account: "account1",
  kind: "new",
  reply_to: null,
  to: [{ name: "", email: "zed@example.net" }],
  cc: [],
  bcc: [],
  subject: "Hello",
  body: "Words",
  attachments: [],
  ...extra,
});

test("eight reads that Thunderbird never answers are answered at their time, and then reads work again", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  const real = world.messenger.messages.list;
  world.messenger.messages.list = never;
  const port = app.port();
  for (let i = 0; i < 8; i++) {
    port.receive({ id: 600 + i, op: "list", account: "account1", folder: "inbox" });
  }
  await settle();
  await world.clock.advance(9600);
  for (let i = 0; i < 8; i++) {
    assert.equal(port.answerTo(600 + i)[0].ok, false);
    assert.match(port.answerTo(600 + i)[0].error, /too long/);
  }
  world.messenger.messages.list = real;
  const next = await ask(app, "list", { account: "account1", folder: "inbox" });
  assert.equal(next.ok, true, "the places are not held for ever by calls that never came back");
  assert.equal(app.engine.gate.free, 8);
});

test("a send for an account whose outgoing server is stuck does not hold up the mail of another account", async () => {
  const { world, app } = await lab({ second: true });
  world.behavior.accounts = { account1: { kind: "slow", ms: 40_000 }, account2: { kind: "ok" } };
  const port = app.port();
  port.receive({ id: 700, op: "send", ...mail({ subject: "stuck" }) });
  await settle();
  port.receive({ id: 701, op: "send", ...mail({ account: "account2", subject: "free" }) });
  await settle();
  await settle();
  assert.equal(port.answerTo(701).length, 1, "the other account's mail is sent at once");
  assert.equal(port.answerTo(701)[0].ok, true);
  assert.equal(port.answerTo(700).length, 0);
  await world.clock.advance(41_000);
  assert.equal(port.answerTo(700)[0].ok, true);
  assert.deepEqual(world.sent.map(s => s.details.subject), ["stuck", "free"]);
});

test("the files of a send that is refused for want of room are forgotten, not kept for ten minutes", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 30_000 };
  const port = app.port();
  const upload = async xfer => result(app, "blob", { xfer, seq: 0, last: true, data: Buffer.from("file").toString("base64") });
  const ids = [];
  for (let i = 0; i < 10; i++) {
    await upload(`file${i}`);
    ids.push(800 + i);
    port.receive({ id: 800 + i, op: "send", ...mail({ subject: `mail ${i}`, attachments: [{ name: "f.txt", content_type: "text/plain", xfer: `file${i}` }] }) });
    await settle();
  }
  const refused = ids.filter(id => port.answerTo(id)[0]?.ok === false);
  assert.equal(refused.length, 1, "one of ten does not find room");
  assert.match(port.answerTo(refused[0])[0].error, /too many mails waiting/);
  const xfer = `file${refused[0] - 800}`;
  assert.equal(app.engine.stash.transfers.has(xfer), false, "its file is let go with it");
  assert.equal(refused[0], 809, "the tenth: one is being sent, eight wait");
  assert.equal(app.engine.stash.transfers.size, 8, "the files of the eight that wait are kept for their turn");
});
