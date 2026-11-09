// Files in pieces: attachments going out as blob events, and the stash they come back through.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { FakeClock, World, drain, start, ask, result, settle } from "./fake.js";
import { CHUNK_MAX, Stash, contentType, fileName } from "../../../share/mail/extension/files.js";

const b64 = bytes => Buffer.from(bytes).toString("base64");
const sha = bytes => createHash("sha256").update(bytes).digest("hex");

function bytesOf(size, seed = 1) {
  const out = new Uint8Array(size);
  for (let i = 0; i < size; i++) {
    out[i] = (i * 31 + seed) & 255;
  }
  return out;
}

async function withAttachment(content, partName = "1.2") {
  const world = new World();
  const { inbox } = world.standard("account1");
  world.message(inbox, {
    headerMessageId: "att@example.org",
    plain: "x",
    attachments: [{ name: "data.bin", contentType: "application/octet-stream", content, partName }],
  });
  const app = await start({ world });
  return { world, app };
}

const blobsOf = (app, xfer) => app.port().events("blob").filter(e => e.xfer === xfer);

// -- going out --

test("an attachment is answered, then sent as blob events of at most 384 KiB, in order, the last one saying so", async () => {
  const content = bytesOf(1024 * 1024);
  const { world, app } = await withAttachment(content);
  const info = await result(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  assert.deepEqual(Object.keys(info).sort(), ["content_type", "name", "size", "xfer"]);
  assert.equal(info.name, "data.bin");
  assert.equal(info.size, content.length);
  await drain(world);
  const events = blobsOf(app, info.xfer);
  assert.deepEqual(events.map(e => e.seq), [0, 1, 2]);
  assert.deepEqual(events.map(e => e.last), [false, false, true]);
  const pieces = events.map(e => Buffer.from(e.data, "base64"));
  assert.deepEqual(pieces.map(p => p.length), [CHUNK_MAX, CHUNK_MAX, content.length - 2 * CHUNK_MAX]);
  assert.equal(sha(Buffer.concat(pieces)), sha(content));
  const frames = app.port().sent.map(f => ("id" in f ? "answer" : f.event));
  assert.ok(frames.indexOf("answer") < frames.indexOf("blob"), "the answer comes first");
  for (const e of events) {
    assert.ok(JSON.stringify(e).length < 1_000_000);
  }
});

test("an empty file is one empty piece, and a file of exactly one piece is one piece", async () => {
  const empty = await withAttachment(new Uint8Array(0));
  const info = await result(empty.app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  await drain(empty.world);
  assert.deepEqual(blobsOf(empty.app, info.xfer).map(e => [e.seq, e.data, e.last]), [[0, "", true]]);
  const exact = await withAttachment(bytesOf(CHUNK_MAX));
  const second = await result(exact.app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  await drain(exact.world);
  assert.deepEqual(blobsOf(exact.app, second.xfer).map(e => [e.seq, e.last]), [[0, true]]);
});

test("an attachment that is not there is not_found, a bad part is a bad request", async () => {
  const { app } = await withAttachment(bytesOf(10));
  assert.equal((await ask(app, "attachment", { account: "account1", key: "att@example.org", part: "9.9" })).code, "not_found");
  assert.equal((await ask(app, "attachment", { account: "account1", key: "att@example.org" })).code, "bad_request");
  assert.equal((await ask(app, "attachment", { account: "account1", key: "att@example.org", part: "p".repeat(100) })).code, "bad_request");
  assert.equal((await ask(app, "attachment", { account: "account1", key: "none@example.org", part: "1.2" })).code, "not_found");
});

test("a file over 100 MiB is too_big before the first piece", async () => {
  const { world, app } = await withAttachment(bytesOf(10));
  world.messenger.messages.getAttachmentFile = async () => ({ size: 100 * 1024 * 1024 + 1, name: "big", type: "x/y", slice: () => assert.fail("read") });
  const answer = await ask(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  assert.equal(answer.code, "too_big");
  assert.equal(app.port().events("blob").length, 0);
});

test("pieces are not sent on a link that has since been replaced, and no more are read once it is gone", async () => {
  const content = bytesOf(5 * CHUNK_MAX);
  const { world, app } = await withAttachment(content);
  const first = app.port();
  const info = await result(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  await settle();
  first.die();
  await world.clock.advance(600);
  await drain(world);
  const second = app.port();
  assert.notEqual(first, second);
  assert.equal(second.events("blob").length, 0, "the new connection is not given the old file's pieces");
  assert.ok(first.events("blob").filter(e => e.xfer === info.xfer).length <= 5);
});

test("a file that cannot be read mid-way ends the transfer quietly, and nothing throws", async () => {
  const { world, app } = await withAttachment(bytesOf(3 * CHUNK_MAX));
  world.messenger.messages.getAttachmentFile = async () => ({
    size: 3 * CHUNK_MAX,
    name: "f",
    type: "a/b",
    slice: (from) => ({ arrayBuffer: async () => (from === 0 ? new ArrayBuffer(CHUNK_MAX) : Promise.reject(new Error("disk went"))) }),
  });
  const info = await result(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  await drain(world);
  const events = blobsOf(app, info.xfer);
  assert.equal(events.length, 1);
  assert.equal(events[0].last, false);
  assert.ok(app.logs.some(([what, why]) => what === "file" && /disk went/.test(why)));
});

test("only a few files go out at once", async () => {
  const { world, app } = await withAttachment(bytesOf(10));
  let release;
  world.messenger.messages.getAttachmentFile = async () => ({
    size: 3,
    name: "f",
    type: "a/b",
    slice: () => ({ arrayBuffer: () => new Promise(resolve => (release = () => resolve(new ArrayBuffer(3)))) }),
  });
  const answers = [];
  for (let i = 0; i < 6; i++) {
    answers.push(await ask(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" }));
  }
  assert.equal(answers.filter(a => a.ok).length, 4);
  assert.ok(answers.filter(a => !a.ok).every(a => a.code === "engine_error"));
  release?.();
});

// -- coming in --

const clock = () => new FakeClock();
const piece = (xfer, seq, bytes, last) => ({ xfer, seq, data: b64(bytes), last });

test("pieces come in order and a whole file is taken, once", () => {
  const stash = new Stash({ clock: clock() });
  stash.put(piece("f1", 0, bytesOf(10), false));
  stash.put(piece("f1", 1, bytesOf(5), true));
  const taken = stash.take(["f1"]);
  assert.equal(taken.get("f1").size, 15);
  stash.drop("f1");
  assert.equal(stash.held, 0);
  assert.throws(() => stash.take(["f1"]), /did not arrive whole/);
});

test("a file that is not whole yet, or never started, cannot be taken", () => {
  const stash = new Stash({ clock: clock() });
  stash.put(piece("f1", 0, bytesOf(10), false));
  assert.throws(() => stash.take(["f1"]), /did not arrive whole/);
  assert.throws(() => stash.take(["nothing"]), /did not arrive whole/);
});

test("a piece out of order, repeated, after the last, or not at the start ends that transfer", () => {
  for (const [first, next, error] of [
    [piece("f", 0, bytesOf(3), false), piece("f", 2, bytesOf(3), true), /out of order/],
    [piece("f", 0, bytesOf(3), false), piece("f", 0, bytesOf(3), false), /out of order/],
    [piece("f", 0, bytesOf(3), true), piece("f", 1, bytesOf(3), true), /out of order/],
    [piece("f", 1, bytesOf(3), false), null, /first piece/],
  ]) {
    const stash = new Stash({ clock: clock() });
    assert.throws(() => {
      stash.put(first);
      if (next) {
        stash.put(next);
      }
    }, error);
    assert.equal(stash.held, 0);
    assert.equal(stash.transfers.size, 0, "nothing is left of it");
  }
});

test("a piece larger than 384 KiB, not base64, or without a place is refused", () => {
  const stash = new Stash({ clock: clock() });
  assert.throws(() => stash.put(piece("f", 0, bytesOf(CHUNK_MAX + 1), true)), (e) => e.code === "too_big");
  assert.throws(() => stash.put({ xfer: "f", seq: 0, last: true, data: "***" }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "f", seq: 0, last: true, data: "abc" }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "f", seq: 0, last: true, data: 5 }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "f", seq: -1, last: true, data: "" }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "f", seq: 0, last: "yes", data: "" }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "../etc", seq: 0, last: true, data: "" }), (e) => e.code === "bad_request");
  assert.throws(() => stash.put({ xfer: "x".repeat(200), seq: 0, last: true, data: "" }), (e) => e.code === "bad_request");
  assert.equal(stash.transfers.size, 0);
});

test("a file is at most 100 MiB, whole, and one more byte is too_big", () => {
  const stash = new Stash({ clock: clock() });
  const data = b64(bytesOf(CHUNK_MAX));
  const whole = Math.floor((100 * 1024 * 1024) / CHUNK_MAX);
  for (let seq = 0; seq < whole; seq++) {
    stash.put({ xfer: "big", seq, data, last: false });
  }
  const rest = 100 * 1024 * 1024 - whole * CHUNK_MAX;
  stash.put({ xfer: "big", seq: whole, data: b64(bytesOf(rest)), last: false });
  assert.equal(stash.transfers.get("big").size, 100 * 1024 * 1024);
  assert.throws(() => stash.put({ xfer: "big", seq: whole + 1, data: b64(bytesOf(1)), last: true }), (e) => e.code === "too_big");
  assert.equal(stash.held, 0, "what had arrived is let go");
  stash.close();
});

test("no more than 64 transfers wait at a time, and no more than the stash's whole size", () => {
  const stash = new Stash({ clock: clock() });
  for (let i = 0; i < 64; i++) {
    stash.put(piece(`f${i}`, 0, bytesOf(1), false));
  }
  assert.throws(() => stash.put(piece("f64", 0, bytesOf(1), false)), (e) => e.code === "engine_error");
  assert.equal(stash.transfers.size, 64);
  const small = new Stash({ clock: clock(), heldMax: 100 });
  small.put(piece("a", 0, bytesOf(60), false));
  assert.throws(() => small.put(piece("b", 0, bytesOf(60), false)), (e) => e.code === "too_big");
  assert.equal(small.held, 60);
});

test("a transfer nobody takes is forgotten after ten minutes, with no traffic to prompt it", async () => {
  const time = clock();
  const stash = new Stash({ clock: time });
  stash.put(piece("old", 0, bytesOf(100), false));
  await time.advance(9 * 60_000);
  assert.equal(stash.transfers.size, 1);
  stash.put(piece("old", 1, bytesOf(100), false));   // a piece counts as activity
  await time.advance(9 * 60_000);
  assert.equal(stash.transfers.size, 1);
  await time.advance(2 * 60_000);
  assert.equal(stash.transfers.size, 0);
  assert.equal(stash.held, 0);
  assert.equal(time.pending(), 0, "and the timer is not left running");
});

test("an abandoned transfer is also swept the next time anything touches the stash", () => {
  const time = clock();
  const stash = new Stash({ clock: time });
  stash.put(piece("old", 0, bytesOf(100), false));
  time.time += 11 * 60_000;
  stash.put(piece("new", 0, bytesOf(1), true));
  assert.deepEqual([...stash.transfers.keys()], ["new"]);
});

test("the blob request answers {} and a bad one is a bad request", async () => {
  const world = new World();
  world.standard("account1");
  const app = await start({ world });
  assert.deepEqual(await result(app, "blob", piece("t1", 0, bytesOf(10), true)), {});
  assert.ok(app.engine.stash.transfers.has("t1"));
  const bad = await ask(app, "blob", { xfer: "t2", seq: 1, last: true, data: "" });
  assert.equal(bad.code, "bad_request");
});

test("names and types of files that came from outside are made safe", () => {
  assert.equal(fileName("../../etc/passwd"), "passwd");
  assert.equal(fileName("C:\\Users\\x\\a b.txt"), "a b.txt");
  assert.equal(fileName("a\r\nBcc: evil@example.org.txt"), "a Bcc: evil@example.org.txt");   // one line, at least
  assert.equal(fileName(""), "attachment");
  assert.equal(fileName(".."), "attachment");
  assert.equal(fileName("x".repeat(500)).length, 200);
  assert.equal(contentType("Text/Plain"), "text/plain");
  assert.equal(contentType("text/plain\r\nX: y"), "application/octet-stream");
  assert.equal(contentType(undefined), "application/octet-stream");
});

// -- the slow part: getting the file out of Thunderbird --

test("an attachment that Thunderbird takes thirty seconds to fetch from the mail server is still answered", async () => {
  const { world, app } = await withAttachment(bytesOf(5000));
  const real = world.messenger.messages.getAttachmentFile;
  world.messenger.messages.getAttachmentFile = async (...args) => {
    await new Promise(resolve => world.clock.setTimeout(resolve, 30_000));
    return real(...args);
  };
  const port = app.port();
  port.receive({ id: 70, op: "attachment", account: "account1", key: "att@example.org", part: "1.2" });
  await world.clock.advance(29_000);
  assert.equal(port.answerTo(70).length, 0, "the add-on is not the one that gives up at ten seconds");
  await world.clock.advance(2000);
  assert.equal(port.answerTo(70)[0].ok, true);
  await drain(world);
  assert.equal(blobsOf(app, port.answerTo(70)[0].result.xfer).length, 1);
});

test("an attachment that the mail server does not give in time is answered in the add-on's own words, before the service gives up", async () => {
  const { world, app } = await withAttachment(bytesOf(5000));
  world.messenger.messages.getAttachmentFile = () => new Promise(() => {});
  const port = app.port();
  port.receive({ id: 71, op: "attachment", account: "account1", key: "att@example.org", part: "1.2" });
  await world.clock.advance(97_000);
  assert.equal(port.answerTo(71).length, 0);
  await world.clock.advance(3000);
  const [answer] = port.answerTo(71);
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /from the mail server in time/);
  assert.equal(port.answerTo(71).length, 1);
  assert.equal(app.port().events("blob").length, 0);
});

test("a file that is listed as over 100 MiB is too_big without being fetched at all", async () => {
  const { world, app } = await withAttachment(bytesOf(10));
  let fetched = 0;
  world.messenger.messages.listAttachments = async () => [{ partName: "1.2", name: "big.bin", contentType: "x/y", size: 100 * 1024 * 1024 + 1 }];
  world.messenger.messages.getAttachmentFile = async () => (fetched++, new File([], "big.bin"));
  const answer = await ask(app, "attachment", { account: "account1", key: "att@example.org", part: "1.2" });
  assert.equal(answer.code, "too_big");
  assert.equal(fetched, 0, "a mail server is not asked for what cannot be sent");
});

test("the stash holds 100 MiB in all, whatever the files, and has room again when a file is let go", () => {
  const stash = new Stash({ clock: new FakeClock() });
  const chunk = new Uint8Array(CHUNK_MAX);
  const fill = (xfer, mib) => {
    const pieces = Math.ceil((mib * 1024 * 1024) / CHUNK_MAX);
    for (let seq = 0; seq < pieces; seq++) {
      stash.put({ xfer, seq, data: b64(chunk), last: seq === pieces - 1 });
    }
  };
  fill("one", 40);
  fill("two", 40);
  assert.throws(() => fill("three", 40), e => e.code === "too_big" && /too much of other files/.test(e.message));
  assert.equal(stash.transfers.has("three"), false, "the one that did not fit is not left half taken");
  stash.drop("one");
  fill("four", 40);
  assert.ok(stash.held <= 100 * 1024 * 1024);
});
