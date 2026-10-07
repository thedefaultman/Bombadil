// mark and move.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, ask, result } from "./fake.js";

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  const app = await start({ world });
  return { world, app, ...boxes };
}

const folderOf = (world, key) => world.folders.get(world.messages.get(world.headerOf(key).id).folderId).specialUse[0];

test("mark sets read and flagged on the message, and says nothing else", async () => {
  const { world, app, inbox } = await lab();
  const h = world.message(inbox, { headerMessageId: "a@example.org", read: false, flagged: false });
  assert.deepEqual(await result(app, "mark", { account: "account1", key: "a@example.org", read: true }), {});
  assert.equal(world.header(h.id).read, true);
  assert.equal(world.header(h.id).flagged, false);
  await result(app, "mark", { account: "account1", key: "a@example.org", flagged: true });
  assert.equal(world.header(h.id).flagged, true);
  assert.equal(world.header(h.id).read, true);
  await result(app, "mark", { account: "account1", key: "a@example.org", read: false, flagged: false });
  assert.equal(world.header(h.id).read, false);
  assert.equal(world.header(h.id).flagged, false);
});

test("mark changes every copy of the mail, and leaves alone the ones that are already so", async () => {
  const { world, app, inbox, archive } = await lab();
  const a = world.message(inbox, { headerMessageId: "twin@example.org", read: false });
  const b = world.message(archive, { headerMessageId: "twin@example.org", read: false });
  const c = world.message(inbox, { headerMessageId: "twin@example.org", read: true });
  void c;
  await result(app, "mark", { account: "account1", key: "twin@example.org", read: true });
  assert.equal(world.header(a.id).read, true);
  assert.equal(world.header(b.id).read, true);
  assert.equal(world.calls.filter(call => call[0] === "update").length, 2);
});

test("mark with nothing to change, or for a mail that is not there, is refused", async () => {
  const { app, world, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  assert.equal((await ask(app, "mark", { account: "account1", key: "a@example.org" })).code, "bad_request");
  assert.equal((await ask(app, "mark", { account: "account1", key: "a@example.org", read: "yes" })).code, "bad_request");
  assert.equal((await ask(app, "mark", { account: "account1", key: "gone@example.org", read: true })).code, "not_found");
  assert.equal((await ask(app, "mark", { account: "account1", read: true })).code, "bad_request");
});

test("mark does not wait for ever on a Thunderbird that does not answer", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  world.messenger.messages.update = () => new Promise(() => {});
  const answer = await ask(app, "mark", { account: "account1", key: "a@example.org", read: true }, { advance: 9600 });
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
});

test("move to the archive goes to the account's Archive folder, and the mail is found there under the same key", async () => {
  const { world, app, inbox, archive } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org", plain: "hello" });
  await result(app, "list", { account: "account1", folder: "inbox" });
  assert.deepEqual(await result(app, "move", { account: "account1", key: "a@example.org", to: "archive" }), {});
  assert.equal(folderOf(world, "a@example.org"), "archives");
  assert.deepEqual(world.calls.find(c => c[0] === "move")[2], archive.id, "to the folder by its id");
  const listed = await result(app, "list", { account: "account1", folder: "archive" });
  assert.deepEqual(listed.messages.map(m => m.key), ["a@example.org"]);
  const got = await result(app, "get", { account: "account1", key: "a@example.org" });
  assert.equal(got.message.folder, "archive");
  assert.equal((await result(app, "list", { account: "account1", folder: "inbox" })).messages.length, 0);
});

test("move to the trash and back to the inbox", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  await result(app, "move", { account: "account1", key: "a@example.org", to: "trash" });
  assert.equal(folderOf(world, "a@example.org"), "trash");
  await result(app, "move", { account: "account1", key: "a@example.org", to: "inbox" });
  assert.equal(folderOf(world, "a@example.org"), "inbox");
});

test("move to where the mail already is does nothing", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  assert.deepEqual(await result(app, "move", { account: "account1", key: "a@example.org", to: "inbox" }), {});
  assert.deepEqual(world.calls.filter(c => c[0] === "move" || c[0] === "archive"), []);
});

test("with no archive folder yet, Thunderbird is asked to archive, which makes the one its settings call for", async () => {
  const world = new World();
  const account = world.account("account1");
  const inbox = world.folder(account, "INBOX", { specialUse: ["inbox"] });
  world.message(inbox, { headerMessageId: "a@example.org" });
  const app = await start({ world });
  await result(app, "move", { account: "account1", key: "a@example.org", to: "archive" });
  assert.equal(world.calls.filter(c => c[0] === "archive").length, 1);
  assert.equal(folderOf(world, "a@example.org"), "archives");
});

test("with no trash folder there is nothing to move to, and nothing is deleted", async () => {
  const world = new World();
  const account = world.account("account1");
  const inbox = world.folder(account, "INBOX", { specialUse: ["inbox"] });
  world.message(inbox, { headerMessageId: "a@example.org" });
  const app = await start({ world });
  const answer = await ask(app, "move", { account: "account1", key: "a@example.org", to: "trash" });
  assert.equal(answer.ok, false);
  assert.equal(answer.code, "engine_error");
  assert.match(answer.error, /Trash/);
  assert.equal(folderOf(world, "a@example.org"), "inbox");
});

test("a mail in the inbox and the archive is archived by taking it out of the inbox", async () => {
  const { world, app, inbox, archive } = await lab();
  const copy = world.message(inbox, { headerMessageId: "twin@example.org" });
  world.message(archive, { headerMessageId: "twin@example.org" });
  await result(app, "move", { account: "account1", key: "twin@example.org", to: "archive" });
  assert.equal(world.header(copy.id), undefined, "the inbox copy was moved (it has a new id)");
  assert.equal(world.inFolder(inbox.id).length, 0);
  assert.equal(world.inFolder(archive.id).length, 2);
});

test("move refuses a target that is not one, and a mail that is not there", async () => {
  const { app, world, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  assert.equal((await ask(app, "move", { account: "account1", key: "a@example.org", to: "spam" })).code, "bad_request");
  assert.equal((await ask(app, "move", { account: "account1", key: "a@example.org" })).code, "bad_request");
  assert.equal((await ask(app, "move", { account: "account1", key: "none@example.org", to: "trash" })).code, "not_found");
});

test("nothing in the add-on can delete a mail", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  world.messenger.messages.delete = () => {
    throw new Error("deleted");
  };
  await result(app, "move", { account: "account1", key: "a@example.org", to: "trash" });
  assert.equal(world.messages.size, 1);
});
