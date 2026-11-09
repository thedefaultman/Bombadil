// streams.js: messages in order without holding them, and letting a list go that is not wanted any more.

import test from "node:test";
import assert from "node:assert/strict";
import { World, settle } from "./fake.js";
import { abort, folderStream, newestFirst } from "../../../share/mail/extension/streams.js";

const lab = (count, pageSize = 10) => {
  const world = new World({ pageSize });
  const { inbox } = world.standard("account1");
  for (let i = 0; i < count; i++) {
    world.message(inbox, { headerMessageId: `m${i}@example.org`, date: new Date(world.clock.now() - i * 1000) });
  }
  return { world, inbox };
};

test("a folder is read newest first a page at a time, and the list is gone when it has been read to its end", async () => {
  const { world, inbox } = lab(35);
  const seen = [];
  for await (const header of folderStream(world.messenger, inbox.id)) {
    seen.push(header.headerMessageId);
  }
  assert.equal(seen.length, 35);
  assert.equal(seen[0], "m0@example.org");
  assert.equal(world.lists.size, 0);
});

test("a folder that is left half way is let go of: the list is aborted and then read out, so that Thunderbird forgets it", async () => {
  const { world, inbox } = lab(500);
  let n = 0;
  for await (const header of folderStream(world.messenger, inbox.id)) {
    if (++n === 15) {
      break;
    }
  }
  assert.equal(world.lists.size, 1, "the list is still held at the moment the reader leaves");
  await settle();
  await settle();
  assert.equal(world.lists.size, 0, "and is gone a moment after");
  const names = world.calls.map(c => c[0]);
  assert.ok(names.indexOf("abortList") >= 0 && names.indexOf("abortList") < names.lastIndexOf("continueList"), "aborted first, then read out");
});

test("abort lets a list go: nothing is held, and it is no error that it is gone already", async () => {
  const { world, inbox } = lab(500);
  const page = await world.messenger.messages.list(inbox.id);
  await abort(world.messenger, page.id);
  assert.equal(world.lists.size, 0);
  await assert.doesNotReject(abort(world.messenger, page.id), "a list that is gone is not a failure");
  await assert.doesNotReject(abort(world.messenger, "never-was"));
});

test("abort never rejects, whatever Thunderbird says, and never throws where it is called", async () => {
  for (const fail of ["abortList", "continueList"]) {
    const messenger = {
      messages: {
        abortList: async () => {
          if (fail === "abortList") {
            throw new Error("boom");
          }
        },
        continueList: async () => {
          throw new Error("boom");
        },
      },
    };
    await assert.doesNotReject(abort(messenger, "l1"), fail);
  }
  const broken = { messages: { abortList: () => { throw new Error("synchronously"); }, continueList: async () => ({ id: null }) } };
  await assert.doesNotReject(abort(broken, "l1"), "a call that throws before it returns a promise");
});

test("what abort reads out is bounded, so that a list that never ends cannot keep it busy", async () => {
  let reads = 0;
  const messenger = {
    messages: {
      abortList: async () => {},
      continueList: async () => ({ id: `l${++reads}`, messages: [] }),
    },
  };
  await abort(messenger, "l0");
  assert.ok(reads > 0 && reads <= 64, `${reads} pages read`);
});

test("a Thunderbird that has stopped answering does not hold up whoever let the list go", async () => {
  const never = () => new Promise(() => {});
  const messenger = {
    messages: {
      list: async () => ({ id: "l1", messages: [{ id: 1, date: new Date(0) }] }),
      abortList: never,
      continueList: never,
    },
  };
  const stream = folderStream(messenger, "f1");
  assert.equal((await stream.next()).value.id, 1);
  const outcome = await Promise.race([stream.return().then(() => "returned"), new Promise(resolve => setTimeout(() => resolve("held up"), 1000))]);
  assert.equal(outcome, "returned", "the reader is done: what is left of the list is nobody's wait");
});

test("a stream that has read to the end leaves nothing to abort", async () => {
  const { world, inbox } = lab(10);
  for await (const header of folderStream(world.messenger, inbox.id)) {
    assert.ok(header.id);
  }
  assert.equal(world.calls.filter(c => c[0] === "abortList").length, 0);
});

test("folders are merged newest first, a tie going to the earlier folder, and all of them are let go when the reader leaves", async () => {
  const world = new World({ pageSize: 10 });
  const { inbox, archive } = world.standard("account1");
  const at = n => new Date(Date.UTC(2026, 0, 1) + n * 1000);
  for (let i = 0; i < 40; i++) {
    world.message(inbox, { headerMessageId: `i${i}@example.org`, date: at(1000 - i * 2) });
    world.message(archive, { headerMessageId: `a${i}@example.org`, date: at(999 - i * 2) });
  }
  world.message(inbox, { headerMessageId: "tie-inbox@example.org", date: at(500) });
  world.message(archive, { headerMessageId: "tie-archive@example.org", date: at(500) });
  const merged = newestFirst([folderStream(world.messenger, inbox.id), folderStream(world.messenger, archive.id)]);
  const got = [];
  for await (const header of merged) {
    got.push(header.headerMessageId);
    if (got.length === 30) {
      break;
    }
  }
  assert.deepEqual(got.slice(0, 4), ["i0@example.org", "a0@example.org", "i1@example.org", "a1@example.org"]);
  await settle();
  await settle();
  assert.equal(world.lists.size, 0);
  const all = [];
  for await (const header of newestFirst([folderStream(world.messenger, inbox.id), folderStream(world.messenger, archive.id)])) {
    all.push(header.headerMessageId);
  }
  assert.ok(all.indexOf("tie-inbox@example.org") < all.indexOf("tie-archive@example.org"));
  assert.equal(all.length, 82);
});
