/*
 * Who runs when.
 *
 * The service may have a few hundred requests waiting at once (it allows 256 on a link), and Thunderbird is
 * one program with one event loop, so what runs at the same time is limited, and what has to wait does so
 * for a short, fixed time.
 *
 * - Ordinary ops (everything but `send`, `blob` and `info`) take one of eight slots. A ninth waits for one,
 *   at most eight seconds and with at most sixty-four others, and is told "busy" if it cannot have one.
 * - Sends for an account go one after another, in the order they came, and no more than eight wait in all.
 *   Sends for different accounts do not wait for each other: one that is stuck on a dialog (an outgoing server
 *   that refuses) must not hold up the mail of an account that has nothing wrong with it.
 * - An ordinary op for an account that a send is running for waits for the send, up to three seconds, so a
 *   listing is not made while the mail is half way out of the door. Then it goes ahead. It does not fail and it
 *   does not wait out the send: a send that is stuck on a dialog must not make the rest of that account's mail
 *   unreadable for a minute. Ops for other accounts never wait for a send.
 *
 * What holds a place is the request, not Thunderbird's call. Every request is answered by a time of its own
 * (engine.js), and when it has been (the `signal` that is handed in is aborted) its place is given up at once,
 * though the call it made may still be pending inside Thunderbird for good: a call that never answers must not
 * keep a slot for ever, or eight of them would leave the add-on answering "busy" to everything until restart.
 * A request that is answered while it waits for a place never begins; a mark or a move that was reported as
 * failed does not happen after all.
 */

import { engineError } from "./errors.js";

const SLOTS = 8;
const WAITING_MAX = 64;
const SLOT_WAIT_MS = 8000;
const SEND_WAIT_MS = 3000;
const SENDS_MAX = 8;

const busy = () => engineError("Thunderbird is busy with other requests. Try again in a moment.");
const answered = () => engineError("That was answered already.");

/** A promise that fails when the signal is aborted, and never settles otherwise: for racing a call against it. */
const stopped = signal =>
  new Promise((_, reject) => {
    if (signal?.aborted) {
      reject(answered());
    } else {
      signal?.addEventListener("abort", () => reject(answered()), { once: true });
    }
  });

export class Gate {
  constructor({ clock, slots = SLOTS, waiting = WAITING_MAX, slotWaitMs = SLOT_WAIT_MS, sendWaitMs = SEND_WAIT_MS, sends = SENDS_MAX }) {
    this.clock = clock;
    this.free = slots;
    this.waiters = [];
    this.waitingMax = waiting;
    this.slotWaitMs = slotWaitMs;
    this.sendWaitMs = sendWaitMs;
    this.sendsMax = sends;
    this.lanes = new Map();   // account -> {queue: [job], current: {done} | null}, while it has a send queued or running
    this.queued = 0;          // sends waiting for their turn, in all the lanes
  }

  /**
   * Run `fn` as an ordinary op. `accounts` are the accounts it touches: a list, `null` for all of them, or an
   * empty list for none (and then it never waits for a send). `signal` is aborted when the request was answered.
   */
  async run(accounts, fn, signal) {
    await this.afterSend(accounts, signal);
    const release = await this.slot(signal);
    try {
      if (signal?.aborted) {
        throw answered();
      }
      return await Promise.race([fn(), stopped(signal)]);
    } finally {
      release();
    }
  }

  async afterSend(accounts, signal) {
    const sending = [];
    for (const [account, lane] of this.lanes) {
      if (lane.current && (accounts === null || accounts.includes(account))) {
        sending.push(lane.current.done);
      }
    }
    if (sending.length === 0 || signal?.aborted) {
      return;
    }
    await new Promise(resolve => {
      const finish = () => {
        this.clock.clearTimeout(timer);
        signal?.removeEventListener("abort", finish);
        resolve();
      };
      const timer = this.clock.setTimeout(finish, this.sendWaitMs);
      signal?.addEventListener("abort", finish, { once: true });
      Promise.all(sending).then(finish);
    });
  }

  slot(signal) {
    if (signal?.aborted) {
      return Promise.reject(answered());
    }
    if (this.free > 0) {
      this.free--;
      return Promise.resolve(this.releaser());
    }
    if (this.waiters.length >= this.waitingMax) {
      return Promise.reject(busy());
    }
    return new Promise((resolve, reject) => {
      const waiter = { resolve, timer: null, leave: null };
      const leave = error => {
        this.clock.clearTimeout(waiter.timer);
        signal?.removeEventListener("abort", onAbort);
        const at = this.waiters.indexOf(waiter);
        if (at >= 0) {
          this.waiters.splice(at, 1);
          reject(error);
        }
      };
      const onAbort = () => leave(answered());
      waiter.timer = this.clock.setTimeout(() => leave(busy()), this.slotWaitMs);
      waiter.leave = () => {
        this.clock.clearTimeout(waiter.timer);
        signal?.removeEventListener("abort", onAbort);
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      this.waiters.push(waiter);
    });
  }

  releaser() {
    let released = false;
    return () => {
      if (released) {
        return;
      }
      released = true;
      const next = this.waiters.shift();
      if (next) {
        next.leave();
        next.resolve(this.releaser());
      } else {
        this.free++;
      }
    };
  }

  /** Run `fn` as a send for `account`, alone for that account, after the sends for it that came before. */
  send(account, fn, signal) {
    if (this.queued >= this.sendsMax) {
      return Promise.reject(engineError("Thunderbird has too many mails waiting to be sent. Nothing was sent."));
    }
    let lane = this.lanes.get(account);
    if (!lane) {
      lane = { queue: [], current: null };
      this.lanes.set(account, lane);
    }
    return new Promise((resolve, reject) => {
      lane.queue.push({ fn, resolve, reject, signal });
      this.queued++;
      this.pump(account);
    });
  }

  async pump(account) {
    const lane = this.lanes.get(account);
    if (!lane || lane.current) {
      return;
    }
    let job = null;
    while (lane.queue.length) {
      job = lane.queue.shift();
      this.queued--;
      if (!job.signal?.aborted) {
        break;
      }
      job.reject(answered());   // answered while it waited: it was never begun, and never will be
      job = null;
    }
    if (!job) {
      this.lanes.delete(account);
      return;
    }
    let finish;
    lane.current = { done: new Promise(resolve => (finish = resolve)) };
    try {
      job.resolve(await Promise.race([job.fn(), stopped(job.signal)]));
    } catch (e) {
      job.reject(e);
    } finally {
      lane.current = null;
      finish();
      this.pump(account);
    }
  }
}
