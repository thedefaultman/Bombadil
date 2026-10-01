/*
 * The newest messages that match something, found without walking a mailbox.
 *
 * Thunderbird's `messages.query` looks at every header of the folders it is given, one at a time, whatever it
 * is asked, and what it finds comes back in no order; but it filters on dates, unread, flagged and attachments
 * inside Thunderbird, at about 0.03 ms a message, where handing a message over costs about a millisecond.
 * Paging through `messages.list` to skip what is not wanted pays that millisecond for every message it skips.
 * So the newest matches are found by asking for them in windows of time, the last day, then a week, a month,
 * three months, a year, three years and everything older, each a separate query that only the dates of its
 * own window let through. A window keeps its newest matches and no more; when the windows so far have made
 * enough, the older ones are never asked. `list` (for what is unread, or older than a page it was shown) and
 * `find` are made of this.
 *
 * A window is only useful whole: what a query had found when time ran out is not the newest of that window
 * (it comes in no order), so a window that did not finish is told as such and nothing in it is used.
 */

import { abort } from "./streams.js";
import { realMessageId, seconds } from "./mailbox.js";

const DAY = 86_400_000;
const WINDOW_DAYS = [1, 7, 30, 90, 365, 1095];

/**
 * The time ranges to search, newest first, each as {from, to} in milliseconds, both ends inclusive. They are
 * counted back from `until` (or from `now`, when that is later or not given), so a search that starts years
 * ago has a small first window and not a huge one.
 */
export function windows(since, until, now) {
  const top = until ?? Infinity;
  const low = since ?? -Infinity;
  const anchor = Math.min(top, now);
  const out = [];
  let to = top;
  for (const edge of WINDOW_DAYS.map(days => anchor - days * DAY).filter(edge => edge < top && edge > low)) {
    out.push({ from: edge, to });
    to = edge - 1;
  }
  out.push({ from: low, to });
  return out.filter(window => window.from <= window.to);
}

/** What makes two headers one message: the account and the Message-ID, else what the key is made from. */
export const sameMessage = header =>
  `${header.folder?.accountId}\n${realMessageId(header) || `${header.author}|${seconds(header)}|${header.subject}`}`;

/**
 * The newest `max` of the headers it is given, in any order, newest first. With `distinct` a message that is
 * in several folders (Gmail's Inbox, All Mail and labels; a mail the person sent to themselves) is kept once, as
 * the copy with the lowest `rank`, so that copies cannot use up what was asked for.
 */
export class Newest {
  constructor(max, { distinct = false, rank = () => 0 } = {}) {
    this.max = max;
    this.distinct = distinct;
    this.rank = rank;
    this.items = [];
    this.copies = new Map();   // sameMessage -> the header kept
  }

  add(header) {
    const key = this.distinct ? sameMessage(header) : null;
    if (key !== null) {
      const have = this.copies.get(key);
      if (have) {
        if (this.rank(header) < this.rank(have)) {
          this.items[this.items.indexOf(have)] = header;
          this.copies.set(key, header);
        }
        return;
      }
    }
    const at = header.date.getTime();
    let i = this.items.length;
    while (i > 0 && this.items[i - 1].date.getTime() < at) {
      i--;
    }
    if (i >= this.max) {
      return;
    }
    this.items.splice(i, 0, header);
    if (key !== null) {
      this.copies.set(key, header);
    }
    if (this.items.length > this.max) {
      const dropped = this.items.pop();
      if (this.distinct) {
        this.copies.delete(sameMessage(dropped));
      }
    }
  }
}

/**
 * Run `query` and give every header it finds that `accept`s to `newest`. Resolves to whether it read to the
 * end: false when `end` (a time on the clock) passed, or `cancelled()` said the op was answered already, with
 * pages still to come; those are then let go.
 */
export async function scan({ messenger, clock }, query, newest, { end, cancelled = () => false, accept = () => true }) {
  let page = await messenger.messages.query(query);
  try {
    while (true) {
      for (const header of page.messages) {
        if (accept(header)) {
          newest.add(header);
        }
      }
      if (!page.id) {
        return true;
      }
      if (cancelled() || clock.now() > end) {
        return false;
      }
      page = await messenger.messages.continueList(page.id);
    }
  } finally {
    if (page && page.id) {
      abort(messenger, page.id);
    }
  }
}

/** The query for one window: its dates, and what else Thunderbird is to filter on. */
export function windowQuery(window, folderId, filters = {}) {
  const query = { folderId, ...filters };
  if (window.from > -Infinity) {
    query.fromDate = new Date(window.from);
  }
  if (window.to < Infinity) {
    query.toDate = new Date(window.to);
  }
  return query;
}
