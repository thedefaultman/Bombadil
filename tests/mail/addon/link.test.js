// The native-messaging port: hello first, reconnecting, and nothing carried over from one connection to the next.

import test from "node:test";
import assert from "node:assert/strict";
import { World, start, ask, result, settle } from "./fake.js";

async function lab(options = {}) {
  const world = new World(options);
  const boxes = world.standard("account1");
  const app = await start({ world });
  const times = [];
  const connect = world.messenger.runtime.connectNative;
  world.messenger.runtime.connectNative = name => {
    times.push(world.clock.now());
    return connect(name);
  };
  return { world, app, times, ...boxes };
}

test("the first frame on a connection is hello, with what the service wants to know", async () => {
  const { app } = await lab();
  const [hello] = app.port().sent;
  assert.equal(app.port().name, "bombadil_mail");
  assert.equal(hello.event, "hello");
  assert.equal(hello.version, 1);
  assert.equal(hello.app, "Thunderbird");
  assert.equal(hello.app_version, "157.0");
  assert.ok(Array.isArray(hello.caps) && hello.caps.includes("send") && hello.caps.includes("messages_send"));
  assert.deepEqual(app.port().sent.filter(f => f.event === "hello").length, 1);
});

test("hello says whether the quiet way of sending is there", async () => {
  const world = new World({ messagesSend: false });
  world.standard("account1");
  const app = await start({ world });
  assert.ok(!app.port().sent[0].caps.includes("messages_send"));
});

test("a Thunderbird that does not say what it is, is still served", async () => {
  const world = new World();
  world.standard("account1");
  world.quirks.browserInfoHangs = true;
  const app = await (async () => {
    const started = start({ world });
    await world.clock.advance(3100);
    return started;
  })();
  assert.equal(app.port().sent[0].event, "hello");
  assert.equal(app.port().sent[0].app_version, "");
});

test("when the host goes, the add-on connects again after half a second, and says hello again", async () => {
  const { world, app, times } = await lab();
  const first = app.port();
  first.die();
  await world.clock.advance(499);
  assert.equal(world.ports.length, 1);
  await world.clock.advance(1);
  assert.equal(world.ports.length, 2);
  const second = app.port();
  assert.notEqual(first, second);
  assert.equal(second.sent[0].event, "hello");
  assert.equal(times.length, 1);
  assert.deepEqual(await result(app, "info").then(r => r.app), "Thunderbird");
});

test("the pause doubles with every connection that does not last, up to ten seconds", async () => {
  const { world, app } = await lab();
  app.port().die();
  const pauses = [];
  let last = world.clock.now();
  for (let i = 0; i < 8; i++) {
    const before = world.ports.length;
    while (world.ports.length === before) {
      await world.clock.advance(100);
    }
    pauses.push(world.clock.now() - last);
    last = world.clock.now();
    app.port().die();   // each connection ends the moment it is made
  }
  assert.deepEqual(pauses, [500, 1000, 2000, 4000, 8000, 10_000, 10_000, 10_000]);
});

test("a connection that lasted a minute starts the pauses from the beginning", async () => {
  const { world, app } = await lab();
  app.port().die();
  await world.clock.advance(500);
  app.port().die();                       // the pause after this one is 1 s
  await world.clock.advance(1000);
  await world.clock.advance(61_000);      // this one lasts a minute
  app.port().die();
  const count = world.ports.length;
  await world.clock.advance(499);
  assert.equal(world.ports.length, count);
  await world.clock.advance(1);
  assert.equal(world.ports.length, count + 1, "half a second again, not two seconds");
});

test("a connection shorter than a minute does not reset the pause", async () => {
  const { world, app } = await lab();
  app.port().die();
  await world.clock.advance(500);
  await world.clock.advance(59_000);
  app.port().die();
  const count = world.ports.length;
  await world.clock.advance(999);
  assert.equal(world.ports.length, count);
  await world.clock.advance(1);
  assert.equal(world.ports.length, count + 1, "one second: the pauses go on doubling");
});

test("a host that cannot be started at all is tried again, and the add-on carries on when it can", async () => {
  const { world, app } = await lab();
  world.connectError = new Error("No such native application bombadil_mail");
  app.port().die();
  await world.clock.advance(40_000);
  const tried = world.ports.length;
  assert.equal(tried, 1, "no port was made while it could not be");
  world.connectError = null;
  await world.clock.advance(10_000);
  assert.equal(world.ports.length, 2);
  assert.equal(app.port().sent[0].event, "hello");
  assert.ok(app.logs.some(([what]) => what === "connectNative"));
});

test("stopping the link stops the reconnecting", async () => {
  const { world, app } = await lab();
  app.port().die();
  app.link.stop();
  app.engine.stop();
  await world.clock.advance(60_000);
  assert.equal(world.ports.length, 1);
  assert.equal(world.clock.pending(), 0, "no timer is left running");
});

test("what was asked on one connection is never answered on the next", async () => {
  const { world, app, inbox } = await lab();
  world.message(inbox, { headerMessageId: "a@example.org" });
  let release;
  const real = world.messenger.messages.list;
  world.messenger.messages.list = (...args) => new Promise(resolve => (release = () => resolve(real(...args))));
  const first = app.port();
  first.receive({ id: 1, op: "list", account: "account1", folder: "inbox" });
  await settle();
  first.die();
  await world.clock.advance(600);
  const second = app.port();
  release();
  await settle();
  await world.clock.advance(100);
  assert.equal(second.answerTo(1).length, 0);
  assert.equal(first.answerTo(1).length, 0, "and the first one is closed, so it was not written to");
  // the new connection numbers its requests again, and that id is its own
  world.messenger.messages.list = real;
  second.receive({ id: 1, op: "list", account: "account1", folder: "inbox" });
  await settle();
  await settle();
  assert.equal(second.answerTo(1).length, 1);
});

test("events are told on the connection there is, and dropped when there is none", async () => {
  const { world, app } = await lab();
  app.port().die();
  assert.equal(app.engine.out({ event: "counts_changed" }), false);
  await world.clock.advance(600);
  assert.equal(app.port().sent.length, 1, "only the hello: events from the gap are not replayed");
  assert.equal(app.engine.out({ event: "counts_changed" }), true);
  assert.equal(app.port().events("counts_changed").length, 1);
});

test("a frame from the host that says something is logged and not answered", async () => {
  const { app } = await lab();
  const before = app.port().sent.length;
  app.port().receive({ event: "host_error", id: 5, error: "a line was too long" });
  await settle();
  assert.equal(app.port().sent.length, before);
  assert.ok(app.logs.some(([what]) => /host said/.test(what)));
});

test("ask never has to be repeated: a link that comes back serves requests as before", async () => {
  const { world, app } = await lab();
  for (let i = 0; i < 3; i++) {
    app.port().die();
    await world.clock.advance(10_000);
    assert.equal((await result(app, "info")).version, 1);
  }
});

test("hello is first even when the host speaks before the add-on has said anything", async () => {
  const { world, app } = await lab();
  const real = world.messenger.runtime.connectNative;
  world.messenger.runtime.connectNative = name => {
    const port = real(name);
    return port;
  };
  app.port().die();
  await world.clock.advance(600);
  const port = app.port();
  port.receive({ id: 77, op: "info" });
  await settle();
  assert.equal(port.sent[0].event, "hello");
  assert.equal(port.sent[1].id, 77);
});

test("a request with no connection to answer on is dropped without a fuss", async () => {
  const { world, app } = await lab();
  const port = app.port();
  const real = port.postMessage.bind(port);
  port.postMessage = () => {
    throw new Error("port is closed");
  };
  port.receive({ id: 5, op: "info" });
  await settle();
  await settle();
  port.postMessage = real;
  assert.equal(port.answerTo(5).length, 0);
  await ask(app, "info");   // and the add-on is still there
  void world;
});

// -- mail that arrives while there is no host --

const arrive = (world, inbox, id, ageMs = 1000) => {
  const header = world.message(inbox, { headerMessageId: id, date: new Date(world.clock.now() - ageMs) });
  world.events.messages.onNewMailReceived.fire(inbox, { id: null, messages: [header] });
};

test("new mail that arrives while the host is away is told after hello when it is back", async () => {
  const { world, app, inbox } = await lab();
  app.port().die();
  arrive(world, inbox, "gap1@example.org", 2000);
  arrive(world, inbox, "gap2@example.org", 1000);
  await settle();
  await settle();
  await world.clock.advance(600);
  const frames = app.port().sent;
  assert.equal(frames[0].event, "hello", "hello is first, always");
  const told = frames.filter(f => f.event === "new_mail");
  assert.deepEqual(told.map(f => f.messages[0].key), ["gap1@example.org", "gap2@example.org"]);
  assert.equal(told[0].account, "account1");
  const first = app.port();
  first.die();
  await world.clock.advance(10_000);
  assert.notEqual(app.port(), first);
  assert.equal(app.port().events("new_mail").length, 0, "and each is told once");
});

test("only the newest twenty of what was missed are kept, and nothing that is older than ten minutes", async () => {
  const { world, app } = await lab();
  const connect = world.messenger.runtime.connectNative;
  world.messenger.runtime.connectNative = () => {
    throw new Error("the host is not there");
  };
  app.port().die();
  const message = n => ({ key: `k${n}@example.org` });
  app.engine.out({ event: "new_mail", account: "account1", messages: [message("old")] });
  await world.clock.advance(11 * 60_000);
  for (let i = 0; i < 30; i++) {
    app.engine.out({ event: "new_mail", account: "account1", messages: [message(i)] });
  }
  app.engine.out({ event: "counts_changed" });
  app.engine.out({ event: "sync", account: "account1", state: "idle", detail: "" });
  app.engine.out({ event: "new_mail", account: "account1" });
  assert.equal(app.link.missed.length, 20, "the oldest are let go as the newer come");
  world.messenger.runtime.connectNative = connect;
  await world.clock.advance(10_000);
  const frames = app.port().sent;
  assert.equal(frames[0].event, "hello");
  const keys = frames.filter(f => f.event === "new_mail").map(f => f.messages[0].key);
  assert.deepEqual(keys, Array.from({ length: 20 }, (_, i) => `k${i + 10}@example.org`));
  assert.equal(frames.filter(f => f.event === "counts_changed" || f.event === "sync").length, 0, "other events are not carried over");
});

test("what was missed is dropped when it is over ten minutes old by the time the host is back", async () => {
  const { world, app } = await lab();
  const connect = world.messenger.runtime.connectNative;
  world.messenger.runtime.connectNative = () => {
    throw new Error("the host is not there");
  };
  app.port().die();
  app.engine.out({ event: "new_mail", account: "account1", messages: [{ key: "stale@example.org" }] });
  await world.clock.advance(11 * 60_000);
  world.messenger.runtime.connectNative = connect;
  await world.clock.advance(10_000);
  assert.equal(app.port().events("new_mail").length, 0);
  assert.equal(app.link.missed.length, 0);
});
