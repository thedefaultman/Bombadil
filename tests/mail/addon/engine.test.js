// Every request is answered once: whatever it asks, whatever Thunderbird does, and however many come at once.

import test from "node:test";
import assert from "node:assert/strict";
import { World, ask, result, settle, start } from "./fake.js";

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  const other = options.second ? world.standard("account2") : null;
  const app = await start({ world });
  return { world, app, other, ...boxes };
}

const never = () => new Promise(() => {});

test("an unknown op is a bad request, and so is anything that is only a name every object has", async () => {
  const { app } = await lab();
  for (const op of ["nonsense", "__proto__", "constructor", "toString", "hasOwnProperty", "", "INFO", "list ", 7, null, {}, ["info"]]) {
    const answer = await ask(app, op, {});
    assert.equal(answer.ok, false, String(op));
    assert.equal(answer.code, "bad_request", String(op));
    assert.equal(typeof answer.error, "string");
  }
});

test("an answer is {id, ok, result} or {id, ok: false, error, code} and nothing else", async () => {
  const { app } = await lab();
  const good = await ask(app, "info");
  assert.deepEqual(Object.keys(good).sort(), ["id", "ok", "result"]);
  const bad = await ask(app, "nonsense");
  assert.deepEqual(Object.keys(bad).sort(), ["code", "error", "id", "ok"]);
});

test("whatever Thunderbird throws, the answer is one sentence and a code, not what was thrown", async () => {
  const { world, app } = await lab();
  for (const thrown of [new Error("boom"), new TypeError("x is undefined"), "a string", 42, null, undefined, { message: "obj" }, Object.assign(new Error("a\nb\u0000c"), { code: "weird" })]) {
    world.messenger.accounts.list = async () => {
      throw thrown;
    };
    const answer = await ask(app, "accounts");
    assert.equal(answer.ok, false);
    assert.equal(answer.code, "engine_error");
    assert.match(answer.error, /^Thunderbird could not do that/);
    assert.doesNotMatch(answer.error, /\n|\u0000|TypeError|Error:/);
  }
});

test("an op that throws before it can even start is answered too", async () => {
  const { world, app } = await lab();
  world.messenger.accounts.list = () => {
    throw new Error("synchronous");
  };
  assert.equal((await ask(app, "accounts")).code, "engine_error");
});

test("an answer that cannot be sent is replaced by one that can", async () => {
  const { world, app } = await lab();
  const real = world.messenger.accounts.list;
  world.messenger.accounts.list = async (...args) => (await real(...args)).map(a => ({ ...a, id: 10n }));
  const answer = await ask(app, "accounts");
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
});

test("an op that takes too long is answered when its time is up, once, and what it says later is dropped", async () => {
  const { world, app } = await lab();
  let release;
  const real = world.messenger.messages.list;
  world.messenger.messages.list = (...args) => new Promise(resolve => (release = () => resolve(real(...args))));
  const port = app.port();
  port.receive({ id: 50, op: "list", account: "account1", folder: "inbox" });
  await world.clock.advance(9400);
  assert.equal(port.answerTo(50).length, 0, "the service allows ten seconds");
  await world.clock.advance(200);
  assert.equal(port.answerTo(50).length, 1);
  assert.equal(port.answerTo(50)[0].code, "engine_error");
  assert.match(port.answerTo(50)[0].error, /too long/);
  release();
  await world.clock.advance(1000);
  assert.equal(port.answerTo(50).length, 1);
  assert.equal(world.lists.size, 0);
});

test("a request with an id that is already being answered is ignored: it is not a second request", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  let release;
  const real = world.messenger.messages.list;
  world.messenger.messages.list = (...args) => new Promise(resolve => (release = () => resolve(real(...args))));
  const port = app.port();
  port.receive({ id: 60, op: "list", account: "account1", folder: "inbox" });
  port.receive({ id: 60, op: "list", account: "account1", folder: "inbox" });
  port.receive({ id: 60, op: "mark", account: "account1", key: "a@example.org", read: true });
  await settle();
  release();
  await settle();
  await settle();
  assert.equal(port.answerTo(60).length, 1);
  assert.equal(port.answerTo(60)[0].ok, true);
  assert.equal(world.calls.filter(c => c[0] === "update").length, 0);
  port.receive({ id: 60, op: "info" });   // the id is free again once answered
  await settle();
  assert.equal(port.answerTo(60).length, 2);
});

test("frames that are not requests are ignored: no answer, no error, nothing thrown", async () => {
  const { app } = await lab();
  const port = app.port();
  const before = port.sent.length;
  for (const frame of [null, undefined, 5, "text", true, [], [1, 2], {}, { op: "info" }, { id: {}, op: "info" }, { id: 1.5, op: "info" }, { id: NaN, op: "info" }, { id: "", op: "info" }, { id: "x".repeat(65), op: "info" }, { id: [1], op: "info" }, { id: 2 ** 60, op: "info" }]) {
    port.onMessage.fire(frame);
  }
  await settle();
  assert.equal(port.sent.length, before);
});

test("string ids are allowed and answered with the same id", async () => {
  const { app } = await lab();
  const port = app.port();
  port.receive({ id: "req-1", op: "info" });
  await settle();
  assert.equal(port.answerTo("req-1").length, 1);
});

test("a thousand requests of every kind, some of them nonsense, are each answered once, and nothing is left behind", async () => {
  const { world, app, inbox } = await lab({ second: true });
  for (let i = 0; i < 40; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, subject: `Subject ${i}`, plain: `body ${i}`, date: new Date(world.clock.now() - i * 3600_000) });
  }
  let seed = 12345;
  const random = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const pick = list => list[Math.floor(random() * list.length)];
  const junk = () => pick([undefined, null, 0, -1, 1e308, "", "x", "account1", "account2", "inbox", "m3@example.org", "fp:" + "a".repeat(40), "a".repeat(700), true, false, [], ["account1"], [null], {}, { name: "n", email: "a@b.example" }, "../../etc/passwd", "\u0000", "k".repeat(70)]);
  const ops = ["info", "accounts", "list", "find", "get", "mark", "move", "attachment", "blob", "send", "known", "nonsense"];
  const fields = ["account", "accounts", "key", "folder", "folders", "limit", "before", "unread", "text", "from", "to", "subject", "since", "until", "read", "flagged", "part", "xfer", "seq", "data", "last", "kind", "reply_to", "cc", "bcc", "body", "attachments", "emails", "identity", "extra"];
  const unhandled = [];
  const hook = e => unhandled.push(e);
  process.on("unhandledRejection", hook);
  const port = app.port();
  const sent = [];
  for (let i = 0; i < 1000; i++) {
    const frame = { id: 5000 + i, op: pick(ops) };
    for (let f = 0; f < Math.floor(random() * 6); f++) {
      frame[pick(fields)] = junk();
    }
    if (random() < 0.5) {
      frame.account = "account1";
    }
    sent.push(frame.id);
    port.receive(frame);
    if (i % 25 === 0) {
      await settle();
    }
  }
  for (let i = 0; i < 50; i++) {
    await world.clock.advance(2000);   // time for the ones that wait, and for the watchdogs
  }
  process.off("unhandledRejection", hook);
  assert.deepEqual(unhandled, []);
  const bad = sent.filter(id => port.answerTo(id).length !== 1);
  assert.deepEqual(bad, [], "each request has exactly one answer");
  for (const id of sent) {
    const [a] = port.answerTo(id);
    assert.equal(typeof a.ok, "boolean");
    if (!a.ok) {
      assert.equal(typeof a.error, "string");
      assert.ok(["bad_request", "not_found", "too_big", "engine_error", "unknown_outcome"].includes(a.code), a.code);
      assert.doesNotMatch(a.error, /\n|\u0000/);
    }
  }
  assert.equal(world.sent.filter(s => s.state === "sent").length <= 1000, true);
  assert.equal(world.windows.size, 0);
  assert.equal(world.lists.size, 0, "no list was left open");
  const session = [...[0]].map(() => app.link.session)[0];
  assert.equal(session.ids.size, 0);
  assert.ok(app.engine.mailbox.hints.size <= 256);
  assert.ok(app.engine.mailbox.tables.size <= 2);
  assert.equal(app.engine.stash.held >= 0, true);
});

// -- who waits for whom --

test("at most eight ordinary requests are inside Thunderbird at once; the rest wait their turn", async () => {
  const { world, app } = await lab();
  const real = world.messenger.messages.list;
  const waiting = [];
  world.messenger.messages.list = (...args) => new Promise(resolve => waiting.push(() => resolve(real(...args))));
  const port = app.port();
  for (let i = 0; i < 12; i++) {
    port.receive({ id: 100 + i, op: "list", account: "account1", folder: "inbox" });
  }
  await settle();
  assert.equal(waiting.length, 8);
  while (waiting.length) {
    waiting.shift()();
    await settle();
    await settle();
  }
  await settle();
  for (let i = 0; i < 12; i++) {
    assert.equal(port.answerTo(100 + i).length, 1, `request ${i}`);
  }
  assert.ok(port.answerTo(111)[0].ok);
});

test("a request that cannot get a place in eight seconds is told Thunderbird is busy", async () => {
  const { world, app } = await lab();
  world.messenger.messages.list = () => never();
  const port = app.port();
  for (let i = 0; i < 9; i++) {
    port.receive({ id: 200 + i, op: "list", account: "account1", folder: "inbox" });
  }
  await world.clock.advance(8100);
  const ninth = port.answerTo(208);
  assert.equal(ninth.length, 1);
  assert.equal(ninth[0].code, "engine_error");
  assert.match(ninth[0].error, /busy/);
});

test("when sixty-four are already waiting, the next is told at once", async () => {
  const { world, app } = await lab();
  world.messenger.messages.list = () => never();
  const port = app.port();
  for (let i = 0; i < 8 + 64 + 1; i++) {
    port.receive({ id: 300 + i, op: "list", account: "account1", folder: "inbox" });
  }
  await settle();
  const last = port.answerTo(300 + 72);
  assert.equal(last.length, 1);
  assert.match(last[0].error, /busy/);
  assert.equal(port.answerTo(300 + 71).length, 0);
});

test("info and blob never wait for a place, nor for a send", async () => {
  const { world, app } = await lab();
  world.messenger.messages.list = () => never();
  world.behavior.send = { kind: "slow", ms: 20_000 };
  const port = app.port();
  for (let i = 0; i < 8; i++) {
    port.receive({ id: 400 + i, op: "list", account: "account1", folder: "inbox" });
  }
  port.receive({ id: 450, op: "send", account: "account1", kind: "new", reply_to: null, to: [{ name: "", email: "a@example.org" }], cc: [], bcc: [], subject: "s", body: "b", attachments: [] });
  await settle();
  assert.equal((await ask(app, "info")).ok, true);
  assert.equal((await ask(app, "blob", { xfer: "t", seq: 0, last: true, data: "" })).ok, true);
});

test("an ordinary request for the account that is being sent from waits for the send, three seconds at most", async () => {
  const { world, app, inbox } = await lab({ second: true });
  world.message(inbox, { headerMessageId: "a@example.org" });
  world.behavior.send = { kind: "slow", ms: 20_000 };
  const port = app.port();
  port.receive({ id: 500, op: "send", account: "account1", kind: "new", reply_to: null, to: [{ name: "", email: "a@example.org" }], cc: [], bcc: [], subject: "s", body: "b", attachments: [] });
  await settle();
  port.receive({ id: 501, op: "list", account: "account1", folder: "inbox" });
  port.receive({ id: 502, op: "list", account: "account2", folder: "inbox" });
  port.receive({ id: 503, op: "accounts" });
  await settle();
  await settle();
  assert.equal(port.answerTo(502).length, 1, "another account's mail does not wait");
  assert.equal(port.answerTo(503).length, 1, "nor does the list of accounts");
  assert.equal(port.answerTo(501).length, 0, "this account's is held back behind the send");
  await world.clock.advance(2900);
  assert.equal(port.answerTo(501).length, 0);
  await world.clock.advance(200);
  assert.equal(port.answerTo(501).length, 1, "and goes ahead when the three seconds are up, send or no send");
  assert.equal(port.answerTo(501)[0].ok, true);
  assert.equal(port.answerTo(500).length, 0, "the send is still going");
});

test("a request for the account a send has finished with does not wait", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  await result(app, "send", { account: "account1", kind: "new", reply_to: null, to: [{ name: "", email: "a@example.org" }], cc: [], bcc: [], subject: "s", body: "b", attachments: [] });
  const out = await ask(app, "list", { account: "account1", folder: "inbox" });
  assert.equal(out.ok, true);
});

test("nothing the add-on keeps grows with the number of requests", async () => {
  const { world, app, inbox } = await lab();
  for (let i = 0; i < 300; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(world.clock.now() - i * 60_000) });
  }
  for (let round = 0; round < 6; round++) {
    const out = await result(app, "list", { account: "account1", folder: "inbox", limit: 100, ...(round ? { before: (world.clock.now() - round * 6_000_000) / 1000 } : {}) });
    for (const m of out.messages.slice(0, 40)) {
      await result(app, "get", { account: "account1", key: m.key });
    }
  }
  assert.ok(app.engine.mailbox.hints.size <= 256, `${app.engine.mailbox.hints.size} remembered messages`);
  assert.ok(app.engine.mailbox.tables.size <= 1);
  assert.equal(app.link.session.ids.size, 0);
  assert.equal(world.lists.size, 0);
});
