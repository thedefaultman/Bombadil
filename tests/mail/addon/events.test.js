// What Thunderbird says, and what the service is told.

import test from "node:test";
import assert from "node:assert/strict";
import { World, ask, result, settle, start } from "./fake.js";

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  for (let i = 0; i < 3; i++) {
    world.message(boxes.inbox, { headerMessageId: `seed${i}@example.org`, date: new Date(world.clock.now() - (i + 1) * 3600_000) });
  }
  const app = await start({ world });
  await world.clock.advance(5000);   // the first look, and what it waits for
  return { world, app, ...boxes };
}

const told = (app, name) => app.port().events(name);
const asList = headers => ({ id: null, messages: headers });

test("new mail in an inbox is told as the messages, newest first, with the account", async () => {
  const { world, app, inbox } = await lab();
  const one = world.message(inbox, { headerMessageId: "new1@example.org", date: new Date(world.clock.now() - 60_000), subject: "First" });
  const two = world.message(inbox, { headerMessageId: "new2@example.org", date: new Date(world.clock.now() - 1000), subject: "Second" });
  world.events.messages.onNewMailReceived.fire(world.messenger.messages ? inbox : inbox, asList([one, two]));
  await settle();
  await settle();
  const [event] = told(app, "new_mail");
  assert.equal(event.account, "account1");
  assert.deepEqual(event.messages.map(m => m.key), ["new2@example.org", "new1@example.org"]);
  assert.equal(event.messages[0].folder, "inbox");
  assert.equal(event.messages[0].subject, "Second");
  assert.equal(event.messages[0].unread, true);
});

test("mail that arrives in any other folder is not new mail", async () => {
  const { world, app, sent, archive, junk, trash } = await lab();
  for (const folder of [sent, archive, junk, trash]) {
    const h = world.message(folder, { date: new Date(world.clock.now() - 1000) });
    world.events.messages.onNewMailReceived.fire(folder, asList([h]));
  }
  await settle();
  await settle();
  assert.equal(told(app, "new_mail").length, 0);
});

test("a burst of new mail is told as the newest twenty", async () => {
  const { world, app, inbox } = await lab();
  const batch = [];
  for (let i = 0; i < 35; i++) {
    batch.push(world.message(inbox, { headerMessageId: `burst${i}@example.org`, date: new Date(world.clock.now() - 10_000 + i * 100) }));
  }
  world.events.messages.onNewMailReceived.fire(inbox, asList(batch));
  await settle();
  await settle();
  const [event] = told(app, "new_mail");
  assert.equal(event.messages.length, 20);
  assert.equal(event.messages[0].key, "burst34@example.org");
});

test("a list that came with new mail and has more is let go, not read on", async () => {
  const { world, app, inbox } = await lab();
  const h = world.message(inbox, { date: new Date(world.clock.now() - 1000) });
  world.events.messages.onNewMailReceived.fire(inbox, { id: "list99", messages: [h] });
  await settle();
  assert.ok(world.calls.some(c => c[0] === "abortList" && c[1] === "list99"));
});

test("a failure in making the event is logged, and the next new mail is still told", async () => {
  const { world, app, inbox } = await lab();
  const real = world.messenger.messages.listAttachments;
  world.messenger.messages.listAttachments = async () => {
    throw new Error("nope");
  };
  const a = world.message(inbox, { headerMessageId: "a@example.org", date: new Date(world.clock.now() - 2000) });
  world.events.messages.onNewMailReceived.fire(inbox, asList([a]));
  await settle();
  await settle();
  world.messenger.messages.listAttachments = real;
  const b = world.message(inbox, { headerMessageId: "b@example.org", date: new Date(world.clock.now() - 1000) });
  world.events.messages.onNewMailReceived.fire(inbox, asList([b]));
  await settle();
  await settle();
  assert.equal(told(app, "new_mail").length, 2);
});

test("counts_changed is told once for a burst of changes, then again for the next burst", async () => {
  const { world, app, inbox } = await lab();
  const before = told(app, "counts_changed").length;
  for (let i = 0; i < 200; i++) {
    world.events.folders.onFolderInfoChanged.fire(inbox, { unreadMessageCount: i });
    world.events.messages.onUpdated.fire({}, { read: true });
    world.events.messages.onMoved.fire({}, {});
    world.events.messages.onDeleted.fire({});
    world.events.messages.onCopied.fire({}, {});
  }
  await world.clock.advance(499);
  assert.equal(told(app, "counts_changed").length, before);
  await world.clock.advance(2);
  assert.equal(told(app, "counts_changed").length, before + 1);
  await world.clock.advance(5000);
  assert.equal(told(app, "counts_changed").length, before + 1, "and nothing more without a reason");
  world.events.messages.onUpdated.fire({}, { flagged: true });
  await world.clock.advance(600);
  assert.equal(told(app, "counts_changed").length, before + 2);
});

test("a constant stream of changes is at most two counts_changed a second", async () => {
  const { world, app } = await lab();
  const before = told(app, "counts_changed").length;
  for (let ms = 0; ms < 10_000; ms += 10) {
    world.events.messages.onUpdated.fire({}, { read: true });
    await world.clock.advance(10);
  }
  const events = told(app, "counts_changed").length - before;
  assert.ok(events >= 10 && events <= 21, `${events}`);
});

test("a folder made, renamed, moved or removed, or an account changed, is accounts_changed, once a second at most, and the folders are looked at again", async () => {
  const { world, app, inbox } = await lab();
  const before = told(app, "accounts_changed").length;
  assert.equal((await result(app, "list", { account: "account1", folder: "archive" })).messages.length, 0);
  const account = world.accounts.get("account1");
  const year = world.folder(account, "Projects");
  world.message(year, { headerMessageId: "p@example.org" });
  for (const name of ["onCreated", "onRenamed", "onMoved", "onDeleted"]) {
    world.events.folders[name].fire(year);
  }
  for (const name of ["onCreated", "onDeleted", "onUpdated"]) {
    world.events.accounts[name].fire("account1", {});
  }
  await world.clock.advance(999);
  assert.equal(told(app, "accounts_changed").length, before);
  await world.clock.advance(2);
  assert.equal(told(app, "accounts_changed").length, before + 1);
  assert.deepEqual((await result(app, "list", { account: "account1", folder: "other" })).messages.map(m => m.key), ["p@example.org"]);
  void inbox;
});

test("the folder tables are dropped when folders change, not left to be a minute out of date", async () => {
  const { world, app } = await lab();
  assert.equal((await result(app, "accounts"))[0].folders.archive, true);
  const account = world.accounts.get("account1");
  account.rootFolders = account.rootFolders.filter(f => f.name !== "Archives");
  world.events.folders.onDeleted.fire({});
  assert.equal((await result(app, "accounts"))[0].folders.archive, false);
});

test("an event Thunderbird does not have in this version is skipped, and the rest work", async () => {
  const world = new World();
  const boxes = world.standard("account1");
  delete world.messenger.folders.onFolderInfoChanged;
  delete world.messenger.messages.onCopied;
  world.messenger.accounts.onUpdated = undefined;
  const app = await start({ world });
  world.events.messages.onUpdated.fire({}, {});
  await world.clock.advance(600);
  assert.equal(app.port().events("counts_changed").length, 1);
  void boxes;
});

test("an event that throws is logged and does not stop the others", async () => {
  const { world, app } = await lab();
  world.events.messages.onNewMailReceived.fire(null, null);
  world.events.messages.onNewMailReceived.fire({ accountId: "nobody", path: "/x" }, asList([{ date: "not a date" }]));
  await settle();
  await settle();
  world.events.messages.onUpdated.fire({}, {});
  await world.clock.advance(600);
  assert.ok(told(app, "counts_changed").length >= 1);
});

test("stopping removes the listeners and the timers", async () => {
  const { world, app } = await lab();
  app.engine.stop();
  app.link.stop();
  for (const group of Object.values(world.events)) {
    for (const event of Object.values(group)) {
      assert.equal(event.listeners.length, 0);
    }
  }
  assert.equal(world.clock.pending(), 0);
});

// -- sync --

test("an account that has not got its folders is syncing, and the first look tells nothing", async () => {
  const world = new World();
  world.account("account1");
  const app = await start({ world });
  await world.clock.advance(1100);
  assert.equal(app.port().events("sync").length, 0);
  const rows = await result(app, "accounts");
  assert.equal(rows[0].state, "syncing");
  assert.match(rows[0].detail, /first time/);
});

test("when the folders and the first mail arrive, it is told as sync idle, and the account is ok", async () => {
  const world = new World();
  const account = world.account("account1");
  const app = await start({ world });
  await world.clock.advance(1100);
  const inbox = world.folder(account, "INBOX", { specialUse: ["inbox"] });
  world.events.folders.onCreated.fire(inbox);
  await world.clock.advance(2100);
  world.message(inbox, { headerMessageId: "first@example.org" });
  world.events.folders.onFolderInfoChanged.fire(inbox, { totalMessageCount: 1 });
  await world.clock.advance(2100);
  const events = app.port().events("sync");
  assert.deepEqual(events.map(e => [e.account, e.state]), [["account1", "idle"]]);
  assert.equal((await result(app, "accounts"))[0].state, "ok");
});

test("a mailbox that really is empty is syncing for two minutes and ok after", async () => {
  const world = new World();
  const account = world.account("account1");
  world.folder(account, "INBOX", { specialUse: ["inbox"] });
  const app = await start({ world });
  assert.equal((await result(app, "accounts"))[0].state, "syncing");
  await world.clock.advance(119_000);
  assert.equal((await result(app, "accounts"))[0].state, "syncing");
  await world.clock.advance(20_000);
  assert.equal((await result(app, "accounts"))[0].state, "ok");
  assert.deepEqual(app.port().events("sync").map(e => e.state), ["idle"], "the recheck timer told it");
});

test("an account with no inbox after five minutes is an error, with a detail that does not claim to know why", async () => {
  const world = new World();
  world.account("account1");
  const app = await start({ world });
  await world.clock.advance(5 * 60_000 + 20_000);
  const rows = await result(app, "accounts");
  assert.equal(rows[0].state, "error");
  assert.match(rows[0].detail, /may not have worked/);
  assert.deepEqual(app.port().events("sync").map(e => e.state), ["error"]);
});

test("the add-on never says signin or blocked: nothing in Thunderbird says so", async () => {
  const { app } = await lab();
  for (const row of await result(app, "accounts")) {
    assert.ok(["ok", "syncing", "error"].includes(row.state));
  }
});

test("no timer runs once everything is settled", async () => {
  const { world, app } = await lab();
  await world.clock.advance(10 * 60_000);
  void app;
  const names = [...world.clock.timers.values()].length;
  assert.equal(names, 0);
});

test("an account that appears later is told if it is not ok, and one that goes is forgotten", async () => {
  const { world, app } = await lab();
  world.account("account2");
  world.events.accounts.onCreated.fire("account2", {});
  await world.clock.advance(3000);
  assert.deepEqual(app.port().events("sync").map(e => [e.account, e.state]), [["account2", "syncing"]]);
  world.accounts.delete("account2");
  world.events.accounts.onDeleted.fire("account2");
  await world.clock.advance(3000);
  assert.equal(app.engine.view.told.has("account2"), false);
  assert.equal(app.engine.mailbox.tables.has("account2"), false);
});
