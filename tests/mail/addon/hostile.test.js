// Mail is other people's words, and a header or a body can be made to be anything: the add-on has one thread,
// and what it reads must not be able to keep it busy. Each of these was a pattern that took minutes (or never
// came back) on the input it is given here; they must now take a moment.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, result } from "./fake.js";
import { parseMailbox, parseMailboxes, recipient } from "../../../share/mail/extension/addr.js";
import { composed } from "../../../share/mail/extension/sending.js";

const quickly = (what, fn, ms = 400) => {
  const began = performance.now();
  const out = fn();
  const took = performance.now() - began;
  assert.ok(took < ms, `${what} took ${took.toFixed(0)} ms`);
  return out;
};

test("a sender made of thousands of at signs and no end is cut, not read from every position", () => {
  for (const raw of ["a@".repeat(60_000) + " x", "a@a".repeat(120_000) + " (x", "(".repeat(200_000) + "@", "<".repeat(300_000), "a@b ".repeat(100_000) + "(", "@\\".repeat(150_000)]) {
    const got = quickly(`a mailbox of ${raw.length} characters`, () => parseMailbox(raw));
    assert.equal(typeof got.email, "string");
    assert.ok(got.email.length <= 320 && got.name.length <= 320);
  }
});

test("the ordinary forms of a mailbox are read as they were", () => {
  assert.deepEqual(parseMailbox("Alice <A@Example.org>"), { name: "Alice", email: "a@example.org" });
  assert.deepEqual(parseMailbox("a@example.org (Alice Example)"), { name: "Alice Example", email: "a@example.org" });
  assert.deepEqual(parseMailbox("A@Example.org"), { name: "", email: "a@example.org" });
  assert.deepEqual(parseMailbox('"Müller, Jürgen" <j@example.de>'), { name: "Müller, Jürgen", email: "j@example.de" });
  assert.deepEqual(parseMailbox("just a name"), { name: "", email: "just a name" });
  assert.deepEqual(parseMailbox("a@b (x) (y)"), { name: "x) (y", email: "a@b" });
  assert.deepEqual(parseMailbox("(x)"), { name: "", email: "(x)" });
  assert.deepEqual(parseMailbox("Name a@b (x)"), { name: "", email: "Name a@b (x)" });
  assert.deepEqual(parseMailbox(""), { name: "", email: "" });
});

test("a list of recipients is cut at fifty before it is read", () => {
  const many = Array.from({ length: 4000 }, (_, i) => `Person ${i} <p${i}@example.org>`);
  assert.equal(quickly("four thousand addresses", () => parseMailboxes(many, 50)).length, 50);
  assert.equal(parseMailboxes(many).length, 4000, "without a limit it is as it was");
});

test("an address written to is checked in one pass, whatever it is made of", () => {
  for (const raw of ["a@" + "b.".repeat(200_000), "@".repeat(300_000), "a".repeat(500_000) + "@b"]) {
    quickly("a recipient", () => {
      try {
        recipient({ name: "", email: raw });
      } catch {
        // refused or not, it does not take long
      }
    });
  }
});

test("the person's words are put above Thunderbird's with no pattern that can be made to take for ever", () => {
  const words = "start" + " ".repeat(500_000) + "end";
  assert.equal(quickly("words with half a million spaces", () => composed(words, "\n\nquoted\n")).startsWith("start"), true);
  assert.equal(quickly("a tail of spaces", () => composed("x" + " ".repeat(800_000), "\n-- \nSig\n")), "x\n\n-- \nSig\n");
  assert.equal(quickly("a quote of spaces", () => composed("x", " ".repeat(800_000) + "y")), "x\n\ny");
});

test("a list of mails whose headers are made to be huge is answered at once, and what is sent is cut", async () => {
  const world = new World({ pageSize: 100 });
  const { inbox } = world.standard("account1");
  const app = await start({ world });
  const hostile = "a@".repeat(60_000) + " x";
  for (let i = 0; i < 100; i++) {
    world.message(inbox, {
      headerMessageId: `h${i}@example.org`,
      author: hostile,
      recipients: Array.from({ length: 4000 }, (_, n) => `R ${n} <r${n}@example.org>`),
      ccList: Array.from({ length: 4000 }, (_, n) => `C ${n} <c${n}@example.org>`),
      subject: "S".repeat(100_000),
      date: new Date(world.clock.now() - i * 1000),
    });
  }
  const began = performance.now();
  const out = await result(app, "list", { account: "account1", folder: "inbox", limit: 100 });
  assert.ok(performance.now() - began < 5000, "the page took a long time");
  assert.equal(out.messages.length, 100);
  for (const m of out.messages) {
    assert.ok(m.to.length <= 50 && m.cc.length <= 50);
    assert.ok(m.subject.length <= 1000);
    assert.ok(m.from.email.length <= 320);
  }
  assert.ok(JSON.stringify(out).length < 2_000_000, "a page of a hundred hostile mails is not tens of megabytes");
});

test("an HTML mail made of unclosed tags is never taken apart by the add-on: not by a search, and not by a read", async () => {
  const world = new World({ pageSize: 100 });
  const { inbox } = world.standard("account1");
  const app = await start({ world });
  world.message(inbox, { headerMessageId: "evil@example.org", subject: "Quarterly", html: "<a ".repeat(200_000) });
  const began = performance.now();
  const found = await result(app, "find", { text: "quarterly report" });
  assert.deepEqual(found.messages.map(m => m.key), []);
  assert.equal(world.calls.filter(c => c[0] === "getFull").length, 0, "no body went through the add-on's own hands");
  assert.ok(performance.now() - began < 5000);
  const read = await result(app, "get", { account: "account1", key: "evil@example.org" });
  assert.equal(read.text, null);
  assert.equal(read.html.length, 600_000, "the HTML is handed over as it is, for the service to read");
});
