// send: the one op that cannot be taken back.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, ask, result, settle } from "./fake.js";
import { SEND_DEADLINE_MS } from "../../../share/mail/extension/sending.js";

const b64 = text => Buffer.from(text).toString("base64");

async function lab({ messagesSend = true, extra = false } = {}) {
  const world = new World({ messagesSend });
  const boxes = world.standard("account1", {
    identities: [
      { id: "id1", email: "me@example.test", name: "Me Myself" },
      { id: "id2", email: "work@example.test", name: "Me At Work" },
    ],
  });
  if (extra) {
    world.standard("account2");
  }
  const orig = world.message(boxes.inbox, {
    headerMessageId: "orig@example.org",
    author: "Dave <dave@example.org>",
    subject: "Project plan",
    plain: "The original words",
    attachments: [{ name: "plan.pdf", contentType: "application/pdf", content: new Uint8Array([1, 2, 3]), partName: "1.2" }],
  });
  const app = await start({ world });
  return { world, app, orig, ...boxes };
}

const base = extra => ({
  account: "account1",
  kind: "new",
  reply_to: null,
  to: [{ name: "Zed Zebra", email: "zed@example.net" }],
  cc: [],
  bcc: [],
  subject: "Hello",
  body: "Words\n\nMore words ✓\n",
  attachments: [],
  ...extra,
});

async function upload(app, name, text, xfer = name) {
  await result(app, "blob", { xfer, seq: 0, last: true, data: b64(text) });
  return { name, content_type: "text/plain", xfer };
}

const sendOk = (app, extra) => result(app, "send", base(extra));
const noWindowsLeft = world => assert.equal(world.windows.size, 0, "a compose window was left open");

// -- the quiet way --

test("a new mail goes through messages.sendMessage with no window, as plain text, from the first identity", async () => {
  const { world, app } = await lab();
  const out = await sendOk(app);
  assert.deepEqual(out, { message_id: "sent-1@example.test", saved: true });
  assert.equal(world.sent.length, 1);
  const { via, details, options } = world.sent[0];
  assert.equal(via, "messages");
  assert.deepEqual(options, { mode: "sendNow" });
  assert.equal(details.identityId, "id1");
  assert.deepEqual(details.to, ['"Zed Zebra" <zed@example.net>']);
  assert.equal(details.cc, undefined);
  assert.equal(details.subject, "Hello");
  assert.equal(details.isPlainText, true);
  assert.equal(details.plainTextBody, "Words\n\nMore words ✓\n");
  assert.equal(world.calls.filter(c => c[0] === "open").length, 0, "no compose window was opened");
  noWindowsLeft(world);
});

test("the identity can be chosen by its id, and one the account does not have is refused", async () => {
  const { world, app } = await lab();
  await sendOk(app, { identity: "id2" });
  assert.equal(world.sent[0].details.identityId, "id2");
  const bad = await ask(app, "send", base({ identity: "id9" }));
  assert.equal(bad.code, "bad_request");
  assert.equal(world.sent.length, 1);
});

test("cc and bcc are sent, and the names are quoted so that no name can end the address early", async () => {
  const { world, app } = await lab();
  await sendOk(app, {
    to: [{ name: 'Evil" <evil@example.net>, "x', email: "zed@example.net" }],
    cc: [{ name: "", email: "cc@example.net" }],
    bcc: [{ name: "B, Cc", email: "bcc@example.net" }],
  });
  const { details } = world.sent[0];
  assert.deepEqual(details.to, ['"Evil\\" <evil@example.net>, \\"x" <zed@example.net>']);
  assert.deepEqual(details.cc, ["cc@example.net"]);
  assert.deepEqual(details.bcc, ['"B, Cc" <bcc@example.net>']);
});

test("attachments are the pieces that were uploaded, named and typed as asked, and they are forgotten once sent", async () => {
  const { world, app } = await lab();
  const one = await upload(app, "notes.txt", "first file", "t1");
  const two = await upload(app, "other.txt", "second file", "t2");
  await sendOk(app, { attachments: [one, { ...two, name: "../sneaky/name.txt" }] });
  const files = world.sent[0].files;
  assert.deepEqual(files.map(f => f.name), ["notes.txt", "name.txt"]);
  assert.equal(await files[0].file.text(), "first file");
  assert.equal(await files[1].file.text(), "second file");
  assert.equal(app.engine.stash.transfers.size, 0);
  assert.equal(app.engine.stash.held, 0);
});

test("the pieces are forgotten when the send is refused or fails, too", async () => {
  const { world, app } = await lab();
  const t = await upload(app, "a.txt", "x", "t1");
  assert.equal((await ask(app, "send", base({ attachments: [t], to: [] }))).code, "bad_request");
  assert.equal(app.engine.stash.transfers.size, 0);
  const u = await upload(app, "a.txt", "x", "t2");
  world.behavior.send = { kind: "fail" };
  assert.equal((await ask(app, "send", base({ attachments: [u] }))).ok, false);
  assert.equal(app.engine.stash.transfers.size, 0);
});

test("an attachment that did not arrive whole is a refusal before anything is sent", async () => {
  const { world, app } = await lab();
  await result(app, "blob", { xfer: "half", seq: 0, last: false, data: b64("part") });
  const answer = await ask(app, "send", base({ attachments: [{ name: "a.txt", content_type: "text/plain", xfer: "half" }] }));
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "bad_request");
  assert.match(answer.error, /Nothing was sent/);
  assert.equal(world.sent.length, 0);
  const missing = await ask(app, "send", base({ attachments: [{ name: "a.txt", content_type: "text/plain", xfer: "never" }] }));
  assert.equal(missing.code, "bad_request");
});

test("a request that is not a sendable mail is refused, and nothing is sent", async () => {
  const { world, app } = await lab();
  const bad = [
    base({ kind: "post" }),
    base({ kind: "reply", reply_to: null }),
    base({ kind: "new", reply_to: "orig@example.org" }),
    base({ to: [], cc: [], bcc: [] }),
    base({ to: [{ name: "x", email: "nobody" }] }),
    base({ to: [{ name: "x", email: "a b@example.org" }] }),
    base({ to: [{ name: "x", email: "a@example.org\r\nBcc: evil@example.net" }] }),
    base({ to: [{ name: "Bad\nName", email: "a@example.org" }] }),
    base({ to: [{ name: "x", email: "<a@example.org>" }] }),
    base({ to: [{ name: "x", email: "a@example.org, b@example.org" }] }),
    base({ body: 7 }),
    base({ body: "x".repeat(950_000) }),
    base({ attachments: "a.txt" }),
    base({ attachments: [{ name: "a", content_type: "x/y", xfer: "../../x" }] }),
    base({ attachments: Array.from({ length: 21 }, (_, i) => ({ name: `f${i}`, content_type: "x/y", xfer: `t${i}` })) }),
    { ...base(), account: undefined },
    { ...base(), account: 5 },
  ];
  for (const args of bad) {
    const answer = await ask(app, "send", args);
    assert.equal(answer.ok, false, JSON.stringify(args).slice(0, 100));
    assert.equal(answer.code, "bad_request", JSON.stringify(args).slice(0, 100));
  }
  assert.equal((await ask(app, "send", base({ account: "nobody" }))).code, "not_found");
  assert.equal(world.sent.length, 0);
});

test("a subject with line breaks becomes one line: a subject cannot end the header and start another", async () => {
  const { world, app } = await lab();
  await sendOk(app, { subject: "Hi\r\nBcc: evil@example.net\u0000 there" });
  assert.equal(world.sent[0].details.subject, "Hi Bcc: evil@example.net there");
});

test("an empty body is sent as a single line break, not as nothing", async () => {
  const { world, app } = await lab();
  await sendOk(app, { body: "" });
  assert.equal(world.sent[0].details.plainTextBody, "\n");
});

test("saved says whether the copy is in Sent, which Thunderbird files a moment after it answers", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "ok", quiet: true };   // the copy is there, but not in the answer
  assert.equal((await sendOk(app)).saved, true);
  world.behavior.send = { kind: "ok", quiet: true, copy: false };
  const out = await ask(app, "send", base(), { advance: 3000 });
  assert.equal(out.result.saved, false);
  assert.equal(out.ok, true, "a copy that is not found is not a failed send");
});

// -- the window --

test("a send through a window says whether the copy is in Sent in the same way", async () => {
  const { world, app } = await lab();
  const args = base({ kind: "reply", reply_to: "orig@example.org", subject: "Re: Project plan" });
  world.behavior.send = { kind: "ok", quiet: true };
  assert.equal((await result(app, "send", args)).saved, true);
  world.behavior.send = { kind: "ok", quiet: true, copy: false };
  const out = await ask(app, "send", args, { advance: 3000 });
  assert.equal(out.ok, true);
  assert.equal(out.result.saved, false);
  noWindowsLeft(world);
});

test("a reply goes through a window opened on the mail it answers, filled in, read back, sent, and closed", async () => {
  const { world, app, orig } = await lab();
  const out = await result(app, "send", base({ kind: "reply", reply_to: "orig@example.org", to: [{ name: "Dave", email: "dave@example.org" }], subject: "Re: Project plan", body: "Thanks.\n\n> The original words\n" }));
  assert.equal(out.saved, true);
  assert.match(out.message_id, /^sent-/);
  assert.deepEqual(world.calls.find(c => c[0] === "open"), ["open", "reply:replyToSender", orig.id]);
  const sent = world.sent[0];
  assert.equal(sent.via, "compose");
  assert.equal(sent.details.type, "reply");
  assert.equal(sent.details.relatedMessageId, orig.id, "it is a reply to that mail, so it keeps the thread");
  assert.deepEqual(sent.details.to, ['"Dave" <dave@example.org>']);
  assert.equal(sent.details.subject, "Re: Project plan");
  assert.equal(sent.details.plainTextBody, "Thanks.\n\n> The original words\n");
  assert.equal(sent.details.isPlainText, true);
  assert.equal(sent.details.identityId, "id1");
  assert.deepEqual(sent.options, { mode: "sendNow" });
  noWindowsLeft(world);
});

test("reply all and forward open the window for their kind, and a forward does not carry the original's attachments", async () => {
  const { world, app, orig } = await lab();
  await result(app, "send", base({ kind: "reply_all", reply_to: "orig@example.org", subject: "Re: Project plan", cc: [{ name: "", email: "cc@example.net" }] }));
  assert.deepEqual(world.calls.find(c => c[0] === "open"), ["open", "reply:replyToAll", orig.id]);
  assert.deepEqual(world.sent[0].details.cc, ["cc@example.net"]);
  world.calls.length = 0;
  await result(app, "send", base({ kind: "forward", reply_to: "orig@example.org", subject: "Fwd: Project plan", body: "FYI\n" }));
  assert.deepEqual(world.calls.find(c => c[0] === "open"), ["open", "forward:forwardInline", orig.id]);
  assert.equal(world.sent[1].details.type, "forward");
  assert.deepEqual(world.sent[1].files, [], "the forward's attachments are the ones asked for, which are none");
  noWindowsLeft(world);
});

test("a forward carries the files that were uploaded for it", async () => {
  const { world, app } = await lab();
  const t = await upload(app, "mine.txt", "my file", "t1");
  await result(app, "send", base({ kind: "forward", reply_to: "orig@example.org", subject: "Fwd: x", attachments: [t] }));
  assert.deepEqual(world.sent[0].files.map(f => f.name), ["mine.txt"]);
  assert.equal(await world.sent[0].files[0].file.text(), "my file");
});

test("a reply to a mail that is gone is not_found and no window is opened", async () => {
  const { world, app } = await lab();
  const answer = await ask(app, "send", base({ kind: "reply", reply_to: "gone@example.org", subject: "Re: x" }));
  assert.equal(answer.code, "not_found");
  assert.equal(world.calls.filter(c => c[0] === "open").length, 0);
  assert.equal(world.sent.length, 0);
});

test("a new mail with no messages.send permission goes through a window as well, from the identity asked for", async () => {
  const { world, app } = await lab({ messagesSend: false });
  assert.equal((await result(app, "info")).api.messages_send, false);
  const t = await upload(app, "a.txt", "attached", "t1");
  await sendOk(app, { identity: "id2", attachments: [t] });
  assert.equal(world.sent[0].via, "compose");
  assert.equal(world.sent[0].details.identityId, "id2");
  assert.equal(world.sent[0].details.type, "new");
  assert.deepEqual(world.sent[0].files.map(f => f.name), ["a.txt"]);
  noWindowsLeft(world);
});

test("a window that Thunderbird composes in HTML by default is made plain text", async () => {
  const { world, app } = await lab({ messagesSend: false });
  world.quirks.htmlCompose = true;
  await sendOk(app);
  assert.equal(world.sent[0].details.isPlainText, true);
  assert.equal(world.sent[0].details.plainTextBody, "Words\n\nMore words ✓\n");
});

test("a signature that the identity adds after the words is allowed, and the words are still what was written", async () => {
  const { world, app } = await lab({ messagesSend: false });
  world.quirks.signature = "Me Myself, Example Corp";
  await sendOk(app);
  assert.equal(world.sent.length, 1);
  assert.ok(world.sent[0].details.plainTextBody.startsWith("Words\n\nMore words ✓"));
});

test("when Thunderbird does not take what it was given, nothing is sent and the window is closed", async () => {
  for (const field of ["subject", "to", "cc", "bcc", "identityId", "plainTextBody", "isPlainText"]) {
    const { world, app } = await lab({ messagesSend: false });
    world.quirks.htmlCompose = true;   // so that a window that ignores isPlainText is one that stays HTML
    world.ignore.add(field);
    const args = base({ cc: [{ name: "", email: "cc@example.net" }], bcc: [{ name: "", email: "b@example.net" }], identity: "id2", subject: "Different" });
    const answer = await ask(app, "send", args);
    assert.equal(answer.ok, false, field);
    assert.equal(answer.code, "engine_error", field);
    assert.match(answer.error, /Nothing was sent/, field);
    assert.equal(world.sent.length, 0, field);
    noWindowsLeft(world);
  }
});

test("when Thunderbird adds a recipient or an attachment of its own, nothing is sent", async () => {
  const { world, app } = await lab({ messagesSend: false });
  const original = world.messenger.compose.setComposeDetails;
  world.messenger.compose.setComposeDetails = async (tabId, details) => {
    await original(tabId, details);
    if (details.to) {
      world.compose(tabId).fields.to.push("extra@example.net");
    }
  };
  assert.equal((await ask(app, "send", base())).ok, false);
  assert.equal(world.sent.length, 0);
  noWindowsLeft(world);
  world.messenger.compose.setComposeDetails = original;
  const add = world.messenger.compose.addAttachment;
  world.messenger.compose.addAttachment = async (tabId, attachment) => {
    await add(tabId, attachment);
    world.compose(tabId).attachments.push({ id: 99, name: "surprise.txt", file: new File(["x"], "surprise.txt") });
  };
  const t = await upload(app, "a.txt", "x", "t1");
  assert.equal((await ask(app, "send", base({ attachments: [t] }))).ok, false);
  assert.equal(world.sent.length, 0);
  noWindowsLeft(world);
});

test("a mail with no subject is not sent through a window: Thunderbird would stop to ask, where nobody can answer", async () => {
  const { world, app } = await lab({ messagesSend: false });
  const answer = await ask(app, "send", base({ subject: "" }));
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /subject/);
  assert.equal(world.calls.filter(c => c[0] === "open").length, 0);
  const quiet = await lab();
  assert.equal((await ask(quiet.app, "send", base({ subject: "" }))).ok, true, "the quiet way asks nothing");
});

test("a window that opens too late is closed when it does, and nothing is sent", async () => {
  const { world, app } = await lab({ messagesSend: false });
  const open = world.openWindow.bind(world);
  world.openWindow = async (...args) => {
    await new Promise(resolve => world.clock.setTimeout(resolve, 60_000));
    return open(...args);
  };
  const answer = await ask(app, "send", base(), { advance: 56_000 });
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /Nothing was sent/);
  await world.clock.advance(10_000);
  noWindowsLeft(world);
  assert.equal(world.sent.length, 0);
});

// -- failing, and time --

test("a send Thunderbird refuses is an engine_error that says nothing was sent, in one sentence", async () => {
  for (const messagesSend of [true, false]) {
    const { world, app } = await lab({ messagesSend });
    world.behavior.send = { kind: "fail", message: "messages.sendMessage failed: Sending FAILED! The connection to the server smtp.example.test was refused.\nPlease try later." };
    const answer = await ask(app, "send", base());
    assert.equal(answer.ok, false);
    assert.equal(answer.code, "engine_error");
    assert.match(answer.error, /^Thunderbird could not send the mail: The connection to the server smtp\.example\.test was refused/);
    assert.match(answer.error, /Nothing was sent\.$/);
    assert.doesNotMatch(answer.error, /\n|sendMessage failed|Sending FAILED/);
    await settle();
    noWindowsLeft(world);
  }
});

test("a send that breaks half way says it cannot tell whether it went", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "fail", message: "messages.sendMessage failed: Sending FAILED! The connection timed out." };
  const answer = await ask(app, "send", base());
  assert.equal(answer.code, "unknown_outcome");
  assert.match(answer.error, /Sent folder/);
});

test("a send that does not answer in 55 seconds is unknown_outcome, answered once, and never retried", async () => {
  for (const messagesSend of [true, false]) {
    const { world, app } = await lab({ messagesSend });
    world.behavior.send = { kind: "hang" };
    const port = app.port();
    port.receive({ id: 900, op: "send", ...base() });
    await world.clock.advance(SEND_DEADLINE_MS - 1000);
    assert.equal(port.answerTo(900).length, 0, "still waiting a second before the deadline");
    await world.clock.advance(1000);
    const answers = port.answerTo(900);
    assert.equal(answers.length, 1);
    assert.equal(answers[0].ok, false);
    assert.equal(answers[0].code, "unknown_outcome");
    await world.clock.advance(120_000);
    assert.equal(port.answerTo(900).length, 1, "and no second answer");
    assert.equal(world.sent.length, 1, "it was not tried again");
  }
});

test("the 55 seconds are counted from when the request came, not from when the send began", async () => {
  const { world, app } = await lab({ messagesSend: false });
  world.behavior.send = { kind: "hang" };
  const original = world.openWindow.bind(world);
  world.openWindow = async (...args) => {
    await new Promise(resolve => world.clock.setTimeout(resolve, 20_000));   // a slow Thunderbird
    return original(...args);
  };
  const port = app.port();
  port.receive({ id: 901, op: "send", ...base() });
  await world.clock.advance(54_000);
  assert.equal(port.answerTo(901).length, 0);
  await world.clock.advance(1100);
  assert.equal(port.answerTo(901)[0].code, "unknown_outcome");
});

test("a send that is too late to begin is refused as a plain failure, never begun", async () => {
  const { world, app } = await lab({ messagesSend: false });
  const original = world.openWindow.bind(world);
  world.openWindow = async (...args) => {
    await new Promise(resolve => world.clock.setTimeout(resolve, 52_500));
    return original(...args);
  };
  const port = app.port();
  port.receive({ id: 902, op: "send", ...base() });
  await world.clock.advance(60_000);
  const [answer] = port.answerTo(902);
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /Nothing was sent/);
  assert.equal(world.sent.length, 0);
  noWindowsLeft(world);
});

test("while a send has not answered, another for the same account is refused; one for another account is not", async () => {
  const { world, app } = await lab({ extra: true });
  world.behavior.send = { kind: "slow", ms: 70_000 };
  const port = app.port();
  port.receive({ id: 910, op: "send", ...base() });
  await world.clock.advance(56_000);
  assert.equal(port.answerTo(910)[0].code, "unknown_outcome");
  const second = await ask(app, "send", base());
  assert.equal(second.ok, false);
  assert.equal(second.code, "engine_error");
  assert.match(second.error, /earlier mail/);
  assert.match(second.error, /Nothing was sent/);
  assert.equal(world.sent.length, 1);
  world.behavior.send = { kind: "ok" };
  assert.equal((await ask(app, "send", base({ account: "account2" }))).ok, true);
  await world.clock.advance(20_000);   // the first finally answers Thunderbird; the account is free again
  assert.equal((await ask(app, "send", base())).ok, true);
  assert.equal(port.answerTo(910).length, 1);
});

test("sends go one at a time, in the order they came", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 2000 };
  const port = app.port();
  const started = [];
  const original = world.messenger.messages.sendMessage;
  world.messenger.messages.sendMessage = (...args) => {
    started.push([world.clock.now(), args[0].subject]);
    return original(...args);
  };
  port.receive({ id: 920, op: "send", ...base({ subject: "first" }) });
  port.receive({ id: 921, op: "send", ...base({ subject: "second" }) });
  port.receive({ id: 922, op: "send", ...base({ subject: "third" }) });
  await world.clock.advance(7000);
  assert.deepEqual(started.map(s => s[1]), ["first", "second", "third"]);
  assert.ok(started[1][0] - started[0][0] >= 2000);
  assert.ok(started[2][0] - started[1][0] >= 2000);
  assert.deepEqual([920, 921, 922].map(id => port.answerTo(id)[0].ok), [true, true, true]);
});

test("a repeated send request with the same id sends once and is answered once", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 500 };
  const port = app.port();
  port.receive({ id: 930, op: "send", ...base() });
  port.receive({ id: 930, op: "send", ...base() });
  await world.clock.advance(2000);
  assert.equal(world.sent.length, 1);
  assert.equal(port.answerTo(930).length, 1);
});

test("a mail kept for later is not a sent mail: it is said not to be known", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "ok", mode: "sendLater" };
  const answer = await ask(app, "send", base());
  assert.equal(answer.code, "unknown_outcome");
});

test("a window of a send that is still in progress is left to Thunderbird and closed when it ends", async () => {
  const { world, app } = await lab({ messagesSend: false });
  world.behavior.send = { kind: "slow", ms: 70_000 };
  const port = app.port();
  port.receive({ id: 940, op: "send", ...base() });
  await world.clock.advance(56_000);
  assert.equal(port.answerTo(940)[0].code, "unknown_outcome");
  assert.equal(world.windows.size, 1, "not closed under Thunderbird's feet");
  await world.clock.advance(20_000);
  await settle();
  noWindowsLeft(world);
});

test("a failed send closes its window, however it failed", async () => {
  const { world, app } = await lab({ messagesSend: false });
  world.behavior.send = { kind: "fail" };
  await ask(app, "send", base());
  await settle();
  noWindowsLeft(world);
  world.messenger.compose.setComposeDetails = async () => {
    throw new Error("the window went");
  };
  const answer = await ask(app, "send", base());
  assert.equal(answer.ok, false);
  await settle();
  noWindowsLeft(world);
});
