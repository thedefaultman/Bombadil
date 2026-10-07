/*
 * The add-on's side of the engine protocol (docs/MAIL.md, "Engine protocol"): a request in, one answer out.
 *
 * `handle(frame, session)` is given one frame from the service. Whatever happens in it (a bad argument, a
 * Thunderbird call that throws, one that never answers, a result that cannot be sent) the request is
 * answered once and only once, with `{id, ok: true, result}` or `{id, ok: false, error, code}`, and nothing
 * is thrown back to the caller. Each op has a time of its own that is shorter than the service's, and
 * what an op has not done by then is answered as a failure (for a send, as `unknown_outcome`: it may be
 * under way); what it says later is dropped.
 *
 * An answer ends the request: what it is still waiting for (a place in the gate, a page from Thunderbird) is given
 * up and what it was going to do next is not done (`ctx.cancelled()`, and gate.js), though a call it already made
 * is Thunderbird's to finish or not.
 *
 * A request belongs to the connection it came on. Its answer goes to that connection or to nobody: the
 * service that asked is gone when the link is, and the one that replaces it numbers its requests from the
 * start. A frame that repeats the id of a request still being answered on the same connection is ignored,
 * not answered, so that a repeated `send` can never send twice and never gives the first one a second answer.
 *
 * The ops are in the modules that know them; this is the table that says which is which, and what each one
 * is allowed to wait for (the gate, gate.js).
 */

import { AccountsView } from "./accounts.js";
import { systemClock } from "./clock.js";
import { attachment, Outgoing, Stash, transfersOf } from "./files.js";
import { mark, move } from "./changing.js";
import { badRequest, describe, engineError, EngineError, UNKNOWN_OUTCOME } from "./errors.js";
import { Events } from "./events.js";
import { find, known } from "./finding.js";
import { Gate } from "./gate.js";
import { Mailbox } from "./mailbox.js";
import { get, list } from "./reading.js";
import { Sending } from "./sending.js";

export const PROTOCOL = 1;
const BROWSER_INFO_MS = 3000;
const READ_MS = 9500;                  // the service gives up on a read at 10 s
const FILE_MS = 100_000;               // and on an attachment at 120 s, a file that has to come from the mail server
const SEND_MS = 58_000;                // the send itself is over at 55 s; this is for a send that is not
const ID_MAX = 64;

const all = () => null;
const none = () => [];
const of = args => (typeof args.account === "string" ? [args.account] : []);

// op -> {run, ms, gate}: `gate` is how it waits (see gate.js): "free" (not at all), "send", or a function giving the
// accounts it touches.
const OPS = new Map([
  ["info", { run: env => env.info(), ms: 5000, gate: "free" }],
  ["accounts", { run: env => env.view.rows(), ms: READ_MS, gate: none }],
  ["list", { run: (env, a, ctx) => list(env, a, ctx), ms: READ_MS, gate: of }],
  ["find", { run: (env, a, ctx) => find(env, a, ctx), ms: READ_MS, gate: a => (Array.isArray(a.accounts) ? a.accounts.filter(x => typeof x === "string") : all()) }],
  ["get", { run: (env, a) => get(env, a), ms: READ_MS, gate: of }],
  ["mark", { run: (env, a) => mark(env, a), ms: READ_MS, gate: of }],
  ["move", { run: (env, a) => move(env, a), ms: READ_MS, gate: of }],
  ["attachment", { run: (env, a, ctx) => attachment(env, a, ctx), ms: FILE_MS, gate: of }],
  ["blob", { run: (env, a) => (env.stash.put(a), {}), ms: READ_MS, gate: "free" }],
  ["known", { run: (env, a, ctx) => known(env, a, ctx), ms: 4500, gate: none }],
  ["send", { run: (env, a, ctx) => env.sending.send(a, ctx), ms: SEND_MS, gate: "send" }],
]);

const defaultId = () => crypto.randomUUID().replaceAll("-", "");

export class Engine {
  constructor({ messenger, clock = systemClock, log = () => {}, randomId = defaultId }) {
    this.messenger = messenger;
    this.clock = clock;
    this.log = log;
    this.out = () => false;               // where events go: the live connection, when there is one
    this.app = { name: "Thunderbird", version: "" };
    this.mailbox = new Mailbox({ messenger, clock });
    this.stash = new Stash({ clock });
    this.gate = new Gate({ clock });
    this.view = new AccountsView({ messenger, clock, mailbox: this.mailbox, stuck: id => this.sending.isLost(id) });
    this.sending = new Sending({
      messenger,
      clock,
      mailbox: this.mailbox,
      stash: this.stash,
      changed: () => this.events.sync.poke(),   // a send given up on makes an account an error, and its end ends that
    });
    this.events = new Events({
      messenger,
      clock,
      mailbox: this.mailbox,
      view: this.view,
      emit: frame => this.out(frame),
      log,
    });
    // What the ops are given.
    this.env = {
      messenger,
      clock,
      mailbox: this.mailbox,
      stash: this.stash,
      view: this.view,
      sending: this.sending,
      outgoing: new Outgoing(log),
      randomId,
      info: () => this.info(),
    };
  }

  /** What Thunderbird is, once (it is not asked again). A Thunderbird that does not say is still served. */
  async init() {
    try {
      const info = await Promise.race([
        this.messenger.runtime.getBrowserInfo(),
        new Promise((_, reject) => this.clock.setTimeout(() => reject(new Error("slow")), BROWSER_INFO_MS)),
      ]);
      this.app = { name: String(info?.name ?? "Thunderbird"), version: String(info?.version ?? "") };
    } catch (e) {
      this.log("browser info", e);
    }
  }

  start() {
    return this.events.start();
  }

  stop() {
    this.events.stop();
    this.stash.close();
  }

  api() {
    const { messages, compose } = this.messenger;
    return {
      messages_send: typeof messages?.sendMessage === "function",
      compose_reply: typeof compose?.beginReply === "function",
    };
  }

  info() {
    return { version: PROTOCOL, app: this.app.name, app_version: this.app.version, api: this.api() };
  }

  /** The first frame of every connection. */
  hello() {
    const api = this.api();
    const caps = ["list", "find", "get", "mark", "move", "attachments", "send", "known", "sync"];
    if (api.messages_send) {
      caps.push("messages_send");
    }
    return { event: "hello", version: PROTOCOL, app: this.app.name, app_version: this.app.version, caps };
  }

  /**
   * One frame from the service on `session` ({ids: Set, post(frame) -> bool}). Returns a promise that is
   * settled when the request has been answered (tests wait on it); it never rejects.
   */
  handle(frame, session) {
    try {
      return this.accept(frame, session);
    } catch (e) {
      this.log("handle", e);
      return Promise.resolve();
    }
  }

  accept(frame, session) {
    if (!frame || typeof frame !== "object" || Array.isArray(frame)) {
      return Promise.resolve();
    }
    const { id, op } = frame;
    if (typeof frame.event === "string" && op === undefined) {
      this.log(`the host said ${String(frame.event).slice(0, 40)}`);
      return Promise.resolve();
    }
    if (!(Number.isSafeInteger(id) || (typeof id === "string" && id.length > 0 && id.length <= ID_MAX))) {
      this.log("a request with no usable id");
      return Promise.resolve();
    }
    if (session.ids.has(id)) {
      this.log(`request ${String(id).slice(0, 20)} is already being answered`);
      return Promise.resolve();
    }
    session.ids.add(id);
    const spec = typeof op === "string" ? OPS.get(op) : undefined;
    const after = [];
    const over = new AbortController();
    const receivedAt = this.clock.now();
    const ctx = {
      receivedAt,
      // aborted when the request has been answered, however: what it waits for is given up
      signal: over.signal,
      cancelled: () => over.signal.aborted,
      // whether the connection the request came on is still there (a send that is begun on a dead one cannot be told)
      live: () => session.live,
      left: () => receivedAt + (spec?.ms ?? 0) - this.clock.now(),
      after: fn => after.push(fn),
      // for what an op says after its answer, on this connection only (a file's pieces are no use on another one)
      emit: frame => {
        try {
          return session.post(frame);
        } catch {
          return false;
        }
      },
    };
    let answered = false;
    let timer = null;
    let settle;
    const settled = new Promise(resolve => (settle = resolve));

    const post = body => {
      try {
        return session.post({ id, ...body });
      } catch (e) {
        this.log("answer", e);
        try {
          return session.post({ id, ok: false, error: "Thunderbird gave an answer that could not be sent.", code: "engine_error" });
        } catch {
          return false;
        }
      }
    };
    const finish = body => {
      if (answered) {
        return;
      }
      answered = true;
      this.clock.clearTimeout(timer);
      session.ids.delete(id);
      over.abort();
      const delivered = post(body);
      if (delivered) {
        for (const fn of after) {
          Promise.resolve()
            .then(fn)
            .catch(e => this.log("after answer", e));
        }
      }
      settle();
    };
    const fail = e => finish({ ok: false, ...describe(e) });

    if (!spec) {
      fail(badRequest("Thunderbird's add-on does not know that request."));
      return settled;
    }
    timer = this.clock.setTimeout(
      () =>
        fail(
          spec.gate === "send"
            ? new EngineError(UNKNOWN_OUTCOME, "Thunderbird did not say whether the mail was sent in time. Look in the Sent folder before sending it again.")
            : engineError("Thunderbird took too long over that.")
        ),
      spec.ms
    );
    Promise.resolve()
      .then(() => this.dispatch(spec, frame, ctx))
      .then(result => finish({ ok: true, result: result === undefined ? {} : result }), fail);
    return settled;
  }

  dispatch(spec, args, ctx) {
    const run = () => spec.run(this.env, args, ctx);
    if (spec.gate === "free") {
      return run();
    }
    if (spec.gate === "send") {
      // The pieces of the mail's files are for this send and no other, whether it went, failed, or was never begun
      // because a place could not be had.
      return this.gate
        .send(typeof args.account === "string" ? args.account : "", run, ctx.signal)
        .finally(() => transfersOf(args.attachments).forEach(xfer => this.stash.drop(xfer)));
    }
    return this.gate.run(spec.gate(args), run, ctx.signal);
  }
}
