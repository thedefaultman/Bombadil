/*
 * Who runs when.
 *
 * The service may have a few hundred requests waiting at once (it allows 256 on a link), and Thunderbird is
 * one program with one event loop, so what runs at the same time is limited, and what has to wait does so
 * for a short, fixed time.
 *
 * - Ordinary ops (everything but `send`, `blob` and `info`) take one of eight slots. A ninth waits for one,
 *   at most eight seconds and with at most sixty-four others, and is told "busy" if it cannot have one.
 * - A send runs alone: sends go one after another, in the order they came, and no more than eight wait.
 * - An ordinary op for the account that a send is running for waits for the send, up to three seconds, so a
 *   listing is not made while the mail is half way out of the door. Then it goes ahead. It does not fail and it
 *   does not wait out the send: a send that is stuck on a dialog must not make the rest of that account's mail
 *   unreadable for a minute. Ops for other accounts never wait for a send.
 */

import { engineError } from "./errors.js";

const SLOTS = 8;
const WAITING_MAX = 64;
const SLOT_WAIT_MS = 8000;
const SEND_WAIT_MS = 3000;
const SENDS_MAX = 8;

const busy = () => engineError("Thunderbird is busy with other requests. Try again in a moment.");

export class Gate {
  constructor({ clock, slots = SLOTS, waiting = WAITING_MAX, slotWaitMs = SLOT_WAIT_MS, sendWaitMs = SEND_WAIT_MS, sends = SENDS_MAX }) {
    this.clock = clock;
    this.free = slots;
    this.waiters = [];
    this.waitingMax = waiting;
    this.slotWaitMs = slotWaitMs;
    this.sendWaitMs = sendWaitMs;
    this.sendsMax = sends;
    this.queue = [];          // sends waiting for their turn
    this.current = null;      // {account, done} for the send that is running
  }

  /**
   * Run `fn` as an ordinary op. `accounts` are the accounts it touches: a list, `null` for all of them, or an
   * empty list for none (and then it never waits for a send).
   */
  async run(accounts, fn) {
    await this.afterSend(accounts);
    const release = await this.slot();
    try {
      return await fn();
    } finally {
      release();
    }
  }

  async afterSend(accounts) {
    const current = this.current;
    if (!current || (accounts !== null && !accounts.includes(current.account))) {
      return;
    }
    await new Promise(resolve => {
      const timer = this.clock.setTimeout(resolve, this.sendWaitMs);
      current.done.then(() => {
        this.clock.clearTimeout(timer);
        resolve();
      });
    });
  }

  slot() {
    if (this.free > 0) {
      this.free--;
      return Promise.resolve(this.releaser());
    }
    if (this.waiters.length >= this.waitingMax) {
      return Promise.reject(busy());
    }
    return new Promise((resolve, reject) => {
      const waiter = { resolve, timer: null };
      waiter.timer = this.clock.setTimeout(() => {
        this.waiters.splice(this.waiters.indexOf(waiter), 1);
        reject(busy());
      }, this.slotWaitMs);
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
        this.clock.clearTimeout(next.timer);
        next.resolve(this.releaser());
      } else {
        this.free++;
      }
    };
  }

  /** Run `fn` as a send for `account`, alone, after the sends that came before it. */
  send(account, fn) {
    if (this.queue.length >= this.sendsMax) {
      return Promise.reject(engineError("Thunderbird has too many mails waiting to be sent. Nothing was sent."));
    }
    return new Promise((resolve, reject) => {
      this.queue.push({ account, fn, resolve, reject });
      this.pump();
    });
  }

  async pump() {
    if (this.current) {
      return;
    }
    const job = this.queue.shift();
    if (!job) {
      return;
    }
    let finish;
    this.current = { account: job.account, done: new Promise(resolve => (finish = resolve)) };
    try {
      job.resolve(await job.fn());
    } catch (e) {
      job.reject(e);
    } finally {
      this.current = null;
      finish();
      this.pump();
    }
  }
}
