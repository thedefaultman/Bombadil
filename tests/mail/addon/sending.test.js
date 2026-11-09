// send: the one op that cannot be taken back.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, ask, result, settle } from "./fake.js";
import { composed, SEND_DEADLINE_MS } from "../../../share/mail/extension/sending.js";

const b64 = text => Buffer.from(text).toString("base64");

async function lab({ messagesSend = true, extra = false, signature = "", workSignature = "" } = {}) {
  const world = new World({ messagesSend });
  const boxes = world.standard("account1", {
    identities: [
      { id: "id1", email: "me@example.test", name: "Me Myself", signature },
      { id: "id2", email: "work@example.test", name: "Me At Work", signature: workSignature },
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
  assert.equal(
    sent.details.plainTextBody,
    "Thanks.\n\n> The original words\n\nOn 9/29/26 1:00 PM, Dave wrote:\n> The original words\n",
    "the person's words, then the mail it answers as Thunderbird quotes it"
  );
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

test("a sender that writes HTML is refused with a sentence, by either way, and nothing is sent: its words would go out changed", async () => {
  for (const messagesSend of [true, false]) {
    const { world, app } = await lab({ messagesSend });
    world.quirks.htmlCompose = true;
    for (const extra of [{}, { kind: "reply", reply_to: "orig@example.org", subject: "Re: Project plan" }]) {
      const answer = await ask(app, "send", base(extra));
      assert.equal(answer.ok, false);
      assert.equal(answer.code, "engine_error");
      assert.match(answer.error, /HTML.*Nothing was sent/);
    }
    assert.equal(world.sent.length, 0);
    assert.equal(world.calls.filter(c => c[0] === "open").length, 0, "no window was opened to find out");
    noWindowsLeft(world);
  }
});

test("the quoted mail and the signature that Thunderbird composes are kept under the person's words, as the draft says", async () => {
  const { world, app } = await lab({ signature: "Me Myself, Example Corp" });
  await result(app, "send", base({ kind: "reply", reply_to: "orig@example.org", to: [{ name: "Dave", email: "dave@example.org" }], subject: "Re: Project plan", body: "Thanks.\n" }));
  assert.equal(
    world.sent[0].details.plainTextBody,
    "Thanks.\n\nOn 9/29/26 1:00 PM, Dave wrote:\n> The original words\n\n-- \nMe Myself, Example Corp\n"
  );
  noWindowsLeft(world);
});

test("a forward carries the forwarded mail under the person's words", async () => {
  const { world, app } = await lab();
  await result(app, "send", base({ kind: "forward", reply_to: "orig@example.org", subject: "Fwd: Project plan", body: "FYI\n" }));
  const body = world.sent[0].details.plainTextBody;
  assert.ok(body.startsWith("FYI\n\n-------- Forwarded Message --------\nSubject: \tProject plan\n"), body);
  assert.ok(body.includes("The original words"), "the forwarded mail's own words are in it");
});

test("a new mail from a sender with a signature goes through a window, which is where Thunderbird puts it", async () => {
  const { world, app } = await lab({ signature: "Me Myself, Example Corp" });
  const out = await sendOk(app, { body: "Words\n" });
  assert.equal(out.saved, true);
  assert.equal(world.sent[0].via, "compose", "messages.sendMessage composes nothing, so it would have left the signature out");
  assert.equal(world.sent[0].details.plainTextBody, "Words\n\n-- \nMe Myself, Example Corp\n");
  noWindowsLeft(world);
});

test("a sender with no signature still goes the quiet way, and one that is asked for is the one whose signature is used", async () => {
  const { world, app } = await lab({ signature: "Me Myself, Example Corp", workSignature: "Me At Work" });
  await sendOk(app, { identity: "id2", body: "Words\n" });
  assert.equal(world.sent[0].via, "compose");
  assert.equal(world.sent[0].details.identityId, "id2");
  assert.equal(world.sent[0].details.plainTextBody, "Words\n\n-- \nMe At Work\n", "the signature of the sender that was asked for, not the first");
  const none = await lab({ signature: "x" });
  await sendOk(none.app, { identity: "id2" });
  assert.equal(none.world.sent[0].via, "messages", "id2 has no signature: nothing to put in a window for");
});

test("what is left of a window after the words is only what Thunderbird composed: words given with no room are put first", async () => {
  assert.equal(composed("Words", "\n\nOn a day, Dave wrote:\n> hi\n"), "Words\n\nOn a day, Dave wrote:\n> hi\n");
  assert.equal(composed("Words\n\n\n", "\n-- \nSig\n"), "Words\n\n-- \nSig\n");
  assert.equal(composed("Words", ""), "Words\n");
  assert.equal(composed("Words", "  \n \n"), "Words\n");
  assert.equal(composed("", ""), "\n");
  assert.equal(composed("", "\n\nOn a day, Dave wrote:\n> hi\n"), "\n\nOn a day, Dave wrote:\n> hi\n", "no words: Thunderbird's own mail as it made it");
});

test("when Thunderbird does not take what it was given, nothing is sent and the window is closed", async () => {
  for (const field of ["subject", "to", "cc", "bcc", "identityId", "plainTextBody"]) {
    const { world, app } = await lab({ messagesSend: false, signature: "Me Myself", workSignature: "Me At Work" });
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

// -- a link that has gone, a Thunderbird that is stuck, and the time that is left --

test("a send whose connection is gone is not begun: not queued behind another, and not after a window was made", async () => {
  const { world, app } = await lab({ extra: true });
  world.behavior.send = { kind: "slow", ms: 4000 };
  const first = app.port();
  first.receive({ id: 1, op: "send", ...base({ subject: "first" }) });
  first.receive({ id: 2, op: "send", ...base({ subject: "queued behind it" }) });
  await settle();
  first.die();
  await world.clock.advance(5000);
  assert.deepEqual(world.sent.map(s => s.details.subject), ["first"], "the one that was already in Thunderbird's hands goes on");
  assert.equal(first.answerTo(2).length, 0);

  const { world: other, app: second } = await lab({ messagesSend: false });
  const real = other.messenger.compose.getComposeDetails;
  other.messenger.compose.getComposeDetails = async (...args) => {
    second.port().die();   // the host goes while the window is being filled in
    return real(...args);
  };
  const port = second.port();
  port.receive({ id: 3, op: "send", ...base() });
  await other.clock.advance(1000);
  assert.equal(other.sent.length, 0, "a mail nobody can be told about is not sent");
  noWindowsLeft(other);
});

test("a send that reaches the add-on on a connection that has already gone sends nothing", async () => {
  const { world, app } = await lab();
  const dead = { ids: new Set(), live: false, post: () => false };
  await app.engine.handle({ id: 5, op: "send", ...base() }, dead);
  assert.equal(world.sent.length, 0);
  assert.equal(dead.ids.size, 0, "and it is not left among the requests being answered");
});

test("a send given up on makes the account an error until Thunderbird answers it, and its end ends that", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 70_000 };
  const port = app.port();
  port.receive({ id: 20, op: "send", ...base() });
  await world.clock.advance(56_000);
  assert.equal(port.answerTo(20)[0].code, "unknown_outcome");
  await world.clock.advance(1500);
  assert.deepEqual(port.events("sync").map(e => [e.account, e.state]), [["account1", "error"]]);
  assert.match(port.events("sync")[0].detail, /restart/i);
  assert.equal((await result(app, "accounts"))[0].state, "error");
  const again = await ask(app, "send", base());
  assert.match(again.error, /stuck on an earlier mail/);
  assert.match(again.error, /dialog nobody can see/);
  assert.match(again.error, /restarted/);
  assert.match(again.error, /Nothing was sent/);
  await world.clock.advance(20_000);
  assert.deepEqual(port.events("sync").map(e => e.state), ["error", "idle"], "Thunderbird answered at last: the account is well again");
  assert.equal((await result(app, "accounts"))[0].state, "ok");
});

test("a send that ends within its time does not make the account an error", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 20_000 };
  const port = app.port();
  port.receive({ id: 21, op: "send", ...base() });
  await world.clock.advance(25_000);
  assert.equal(port.answerTo(21)[0].ok, true);
  assert.equal(port.events("sync").length, 0);
  assert.equal(app.engine.sending.isLost("account1"), false);
});

test("a look at the Sent folder that never answers does not keep a send from being answered, nor the account from the next one", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "ok", quiet: true, copy: false };
  const query = world.messenger.messages.query;
  world.messenger.messages.query = (...args) => (args[0]?.headerMessageId?.startsWith("sent-") ? new Promise(() => {}) : query(...args));
  const port = app.port();
  port.receive({ id: 30, op: "send", ...base() });
  await world.clock.advance(3500);
  assert.equal(port.answerTo(30).length, 1, "answered when the look is given up, long before the deadline");
  assert.equal(port.answerTo(30)[0].ok, true);
  assert.equal(port.answerTo(30)[0].result.saved, false, "and says it does not know the copy is saved");
  world.messenger.messages.query = query;
  world.behavior.send = { kind: "ok" };
  assert.equal((await ask(app, "send", base({ subject: "next" }))).ok, true, "the lane is free for the next");
});

test("a send queued behind another is held to the 55 seconds from when it came, so one that would begin too late is refused", async () => {
  const { world, app } = await lab();
  world.behavior.send = { kind: "slow", ms: 53_000 };
  const port = app.port();
  port.receive({ id: 40, op: "send", ...base({ subject: "long" }) });
  port.receive({ id: 41, op: "send", ...base({ subject: "late" }) });
  await world.clock.advance(60_000);
  assert.equal(port.answerTo(40)[0].ok, true);
  const late = port.answerTo(41)[0];
  assert.equal(late.ok, false);
  assert.equal(late.code, "engine_error", "never begun, so a plain failure and not unknown_outcome");
  assert.match(late.error, /Nothing was sent/);
  assert.deepEqual(world.sent.map(s => s.details.subject), ["long"]);
});

test("the mail that is replied to is the one that was named, even when Thunderbird has given that number to another", async () => {
  const { world, app, inbox, orig } = await lab({ messagesSend: false });
  await result(app, "get", { account: "account1", key: "orig@example.org" });   // remembers where it is
  world.messages.get(orig.id).header.headerMessageId = "someone-else@example.org";
  world.message(inbox, { headerMessageId: "orig@example.org", subject: "The real one", author: "Eve <eve@example.org>" });
  const opened = [];
  const open = world.openWindow.bind(world);
  world.openWindow = (kind, id, ...rest) => (opened.push(id), open(kind, id, ...rest));
  await result(app, "send", base({ kind: "reply", reply_to: "orig@example.org" }));
  assert.equal(opened.length, 1);
  assert.notEqual(opened[0], orig.id, "the window was opened on the mail with that Message-ID");
  assert.equal(world.header(opened[0]).subject, "The real one");
});

test("an address whose domain is one word is an address: Thunderbird judges the rest, as it does for the person", async () => {
  const { world, app } = await lab();
  await sendOk(app, { to: [{ name: "", email: "root@localhost" }, { name: "Ops", email: "ops@intranet" }] });
  assert.deepEqual(world.sent[0].details.to, ["root@localhost", '"Ops" <ops@intranet>']);
  for (const email of ["nobody", "a@@b", "@b", "a@", "a b@c", "a@b\nBcc: x@example.org"]) {
    const answer = await ask(app, "send", base({ to: [{ name: "", email }] }));
    assert.equal(answer.code, "bad_request", JSON.stringify(email));
  }
  assert.equal(world.sent.length, 1);
});
